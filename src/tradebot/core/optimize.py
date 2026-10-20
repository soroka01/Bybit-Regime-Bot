"""Честный подбор параметров стратегии: перебор всей сетки, отбор по in-sample (IS), проверка out-of-sample (OOS).

Принципы против подгонки:
  * период делится по времени: первые 67% — IS (по ним и только по ним выбираем), последние 33% — OOS (только отчёт);
  * минимум сделок на IS (30, пропорционально короче для коротких периодов, но не меньше 10);
  * критерий «robust» = 0.5 * собственный скор + 0.5 * средний скор соседей по сетке (плато лучше одиночного пика).

Модуль чистый: без сети и без знания о веб-слое. Сигнал поставляет make_signal(params) на ПОЛНОЙ истории
(прогрев индикаторов), здесь он только режется по периодам.
"""
from __future__ import annotations

import itertools
import time
from typing import Callable

import numpy as np
import pandas as pd

from tradebot.core.engine import backtest

# Версия логики оптимизатора/вердиктов/движка для дисковых кэшей. ПОВЫШАЙТЕ при любой правке, меняющей результаты
# (дополнительно кэш сам инвалидируется по хэшу исходников core/optimize|engine|strategies|overlays|indicators).
# 3 = «семантика бэктеста v2»: htf_filter (бык -> только лонги, медведь -> только шорты), ликвидация по эффективному плечу
# leverage*|pos|, сделки по смене знака + комиссия выхода в сделке, доходность свечи >= -100 %, Sortino по всем свечам,
# CAGR/Calmar = None при выборке < 7 дн., atr_stop по знаку сигнала, комиссия перпа по умолчанию 0.1 %.
CACHE_VERSION = 3

IS_FRAC = 0.67
BASE_MIN_TRADES = 30
MIN_TRADES_FLOOR = 10
MIN_OOS_DAYS = 90          # короче — никаких robust/overfit (только «мало данных»)
SHORT_OOS_DAYS = 180       # короче — аннуализированные метрики (Sharpe/Calmar/CAGR) сильно завышены
DD_FLOOR = 0.05           # |maxDD| в знаменателе Calmar не меньше 5%: крошечная просадка не взрывает метрику
LIQ_PENALTY = 2.0          # вычитается из скора за каждую ликвидацию
NEIGHBOUR_FLOOR = -1.0     # скор «плохого/невалидного» соседа при усреднении
CRITERIA = ("robust", "calmar", "sharpe", "return")
METRIC_KEYS = ("total_return", "cagr", "buy_hold_return", "max_drawdown", "sharpe", "calmar", "trades",
               "win_rate", "avg_month", "median_month", "green_months", "months_15plus", "liquidations")


def min_trades(is_days: float) -> int:
    """30 сделок для >= года IS, пропорционально меньше для коротких периодов (но не меньше 10)."""
    return int(min(BASE_MIN_TRADES, max(MIN_TRADES_FLOOR, round(BASE_MIN_TRADES * is_days / 365))))


def calmar_floored(m: dict) -> float | None:
    """CAGR / max(|maxDD|, 5%)."""
    if not m or m.get("cagr") is None or m.get("max_drawdown") is None:
        return None
    return float(m["cagr"]) / max(abs(float(m["max_drawdown"])), DD_FLOOR)


def score_of(m: dict, criterion: str) -> float:
    """Скор по именованному критерию; robust использует Calmar (с полом на просадку). NaN — результат непригоден."""
    if not m:
        return float("nan")
    key = {"sharpe": "sharpe", "return": "total_return"}.get(criterion, "calmar")
    v = calmar_floored(m) if key == "calmar" else m.get(key)
    if v is None or not np.isfinite(v):
        return float("nan")
    return float(v) - LIQ_PENALTY * float(m.get("liquidations") or 0)


def robust_scores(scores: dict[tuple, float], axes: list[list]) -> dict[tuple, float]:
    """0.5*собственный + 0.5*среднее по соседям (соседние значения вдоль каждой оси сетки).
    Невалидный (NaN) сосед считается как NEIGHBOUR_FLOOR; невалидная точка сама не получает скора."""
    pos = [{v: i for i, v in enumerate(ax)} for ax in axes]
    out = {}
    for key, own in scores.items():
        if not np.isfinite(own):
            continue
        nb = []
        for a, ax in enumerate(axes):
            i = pos[a][key[a]]
            for j in (i - 1, i + 1):
                if 0 <= j < len(ax):
                    k2 = key[:a] + (ax[j],) + key[a + 1:]
                    s = scores.get(k2, float("nan"))
                    nb.append(s if np.isfinite(s) else NEIGHBOUR_FLOOR)
        out[key] = 0.5 * own + 0.5 * (float(np.mean(nb)) if nb else own)
    return out


def verdict(is_m: dict, oos_m: dict, oos_days: float | None = None) -> str:
    """'robust' | 'overfit' | 'few' | 'loss'.
    loss — OOS не в плюсе; few — сделок на OOS меньше минимума (выводов делать нельзя);
    robust — IS в плюсе, OOS Sharpe > 0.5, CAGR OOS не ниже 25% от IS, средний месяц > 0 и >= 40% зелёных месяцев; иначе overfit."""
    if not oos_m or (oos_m.get("total_return") or 0) <= 0:
        return "loss"
    need = min_trades(oos_days) if oos_days else MIN_TRADES_FLOOR
    if (oos_days is not None and oos_days < MIN_OOS_DAYS) or (oos_m.get("trades") or 0) < need:
        return "few"
    is_cagr, oos_cagr = (is_m or {}).get("cagr") or 0.0, oos_m.get("cagr") or 0.0
    # лучшие параметры не заработали даже на подборе (IS <= 0) -> плюс на OOS это удача, а не устойчивость
    far_below = is_cagr <= 0 or oos_cagr < 0.25 * is_cagr
    if ((oos_m.get("sharpe") or 0) > 0.5 and not far_below and (oos_m.get("avg_month") or 0) > 0
            and (oos_m.get("green_months") or 0) >= 0.4):
        return "robust"
    return "overfit"


def _pick(m: dict) -> dict:
    if not m:
        return {}
    out = {k: (None if (v := m.get(k)) is None or not np.isfinite(v) else float(v)) for k in METRIC_KEYS}
    out["calmar"] = calmar_floored(m)   # единая «защищённая» версия Calmar для UI и рейтинга
    return out


def _rng(idx: pd.DatetimeIndex) -> dict:
    return {"start": idx[0].isoformat(), "end": idx[-1].isoformat(), "bars": int(len(idx)),
            "days": float((idx[-1] - idx[0]).total_seconds() / 86400)}


def optimize(df: pd.DataFrame, grid: dict[str, list], make_signal: Callable[[dict], pd.Series], *, days: int,
             fee: float, slippage: float, leverage: float = 1.0, long_only: bool = False,
             criterion: str = "robust", top: int = 10, curve: bool = False,
             check: Callable[[], None] | None = None, is_frac: float = IS_FRAC) -> dict:
    if criterion not in CRITERIA:
        raise ValueError(f"неизвестный критерий: {criterion}")
    t0 = time.monotonic()
    mask = df.index >= df.index[-1] - pd.Timedelta(days=days)
    sub = df[mask]
    if len(sub) < 50:
        raise ValueError("слишком мало данных за выбранный период")
    cut = sub.index[0] + (sub.index[-1] - sub.index[0]) * is_frac
    is_m_, oos_m_ = sub.index < cut, sub.index >= cut
    sub_is, sub_oos = sub[is_m_], sub[oos_m_]
    if len(sub_is) < 30 or len(sub_oos) < 30:
        raise ValueError("слишком мало данных для разделения IS/OOS")
    need = min_trades(_rng(sub_is.index)["days"])
    oos_days = _rng(sub_oos.index)["days"]

    names = list(grid)
    axes = [sorted(set(grid[n])) for n in names]
    combos = list(itertools.product(*axes))
    sigs: dict[tuple, pd.Series] = {}
    is_res: dict[tuple, dict] = {}
    scores: dict[tuple, float] = {}
    for key in combos:
        if check:
            check()
        params = dict(zip(names, key))
        sig = make_signal(params).reindex(df.index).fillna(0.0)
        sigs[key] = sig
        res = backtest(sub_is, sig[mask][is_m_], fee, slippage, leverage, long_only)
        m = res.metrics
        is_res[key] = m
        ok = bool(m) and (m.get("trades") or 0) >= need
        scores[key] = score_of(m, criterion if criterion != "robust" else "calmar") if ok else float("nan")

    rob = robust_scores(scores, axes)
    rank = rob if criterion == "robust" else {k: v for k, v in scores.items() if np.isfinite(v)}
    order = sorted(rank, key=lambda k: rank[k], reverse=True)[:top]

    results = []
    for n, key in enumerate(order):
        if check:
            check()
        sig = sigs[key]
        oos = backtest(sub_oos, sig[mask][oos_m_], fee, slippage, leverage, long_only)
        full = backtest(sub, sig[mask], fee, slippage, leverage, long_only)
        row = {"params": {k: (v.item() if hasattr(v, "item") else v) for k, v in zip(names, key)},
               "score": None if not np.isfinite(scores[key]) else float(scores[key]),
               "robust_score": float(rob[key]) if key in rob else None,
               "is": _pick(is_res[key]), "oos": _pick(oos.metrics), "full": _pick(full.metrics),
               "verdict": verdict(is_res[key], oos.metrics, oos_days)}
        if curve and n == 0 and len(oos.equity) > 2:
            ix = np.unique(np.linspace(0, len(oos.equity) - 1, 120).astype(int))
            row["curve"] = {"t": [int(x) for x in (oos.equity.index[ix].asi8 // {"ns": 10**9, "us": 10**6, "ms": 10**3, "s": 1}[oos.equity.index.unit])],
                            "equity": [round(float(x), 5) for x in oos.equity.to_numpy()[ix]],
                            "bh": [round(float(x), 5) for x in oos.bh_equity.to_numpy()[ix]]}
        results.append(row)

    return {"results": results, "n_combos": len(combos), "n_valid": int(np.isfinite(list(scores.values())).sum()),
            "min_trades": need, "criterion": criterion, "elapsed": round(time.monotonic() - t0, 2),
            "is_range": _rng(sub_is.index), "oos_range": _rng(sub_oos.index),
            "oos_days": round(oos_days, 1), "short_oos": bool(oos_days < SHORT_OOS_DAYS),
            "buy_hold_oos": float(sub_oos["close"].iloc[-1] / sub_oos["close"].iloc[0] - 1)}
