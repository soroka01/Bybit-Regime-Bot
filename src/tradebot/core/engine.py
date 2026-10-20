"""Векторный бэктест-движок.

Сигнал считается по закрытию свечи t, позиция открывается по её close и держится до close t+1
(look-ahead исключён: pos = signal.shift(1)). Комиссия+проскальзывание берутся с каждого
изменения позиции. Плечо масштабирует доходность; ликвидация приближённо проверяется по
high/low свечи. Фандинг учитывается, если в df есть колонка "funding".

Модель ликвидации (maintenance margin): позиция ликвидируется, когда нереализованный убыток съедает всю маржу
за вычетом поддерживающей: L * adverse <= -(1 - MMR * L), MMR = 0.5 % (MAINT_MARGIN_RATE), где L = leverage * |pos| —
ЭФФЕКТИВНОЕ плечо свечи (при позиции 0.5 от капитала и плече 2x эффективное плечо 1x, а не 2x). Порог ограничен
диапазоном [-0.995; -0.5]. Эквивалентно движению против позиции на (1/L - MMR): при 100x ликвидация после
≈ 0.5 %, при 50x — ≈ 1.5 %, при 10x — ≈ 9.5 %, при 3x — ≈ 32.8 %, при 1x — не достижима (-99.5 %).
Ликвидированная свеча теряет 100 % капитала. Капитал не уходит ниже нуля: доходность свечи не может быть меньше -100 %;
после разорения (капитал 0) позиция считается закрытой, доходности свечей — нулевыми, ликвидации больше не считаются.
Предел модели: просадка внутри сделки (по high/low) считается от close предыдущей свечи, а не от экстремума сегмента.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Единый источник комиссии taker перпа по умолчанию для бэктестов (движок, веб-схемы, /api/meta, CLI research/*).
# Реальный taker аккаунта 0.1 % (Bybit fee-rate API и execFee в сделках бота, проверено 2026-10-09) — не базовые 0.055 %.
# Спот-умолчание (0.1 %) живёт в web/schemas.SPOT_FEE.
DEFAULT_PERP_FEE = 0.001
MAINT_MARGIN_RATE = 0.005  # 0.5 % — поддерживающая маржа (MMR), упрощённо для BTCUSDT на Bybit
LIQ_FLOOR, LIQ_CEIL = -0.995, -0.5   # границы порога L*adverse: не мягче -99.5 % и не жёстче -50 %
MIN_CAGR_DAYS = 7.0   # короче — CAGR/Calmar не определены (None): экстраполяция на год из менее недели бессмысленна


def liquidation_threshold(leverage, mmr: float = MAINT_MARGIN_RATE):
    """Порог L*adverse, при котором позиция ликвидируется: -(1 - mmr*L), зажатый в [-0.995; -0.5].
    Скаляр -> float, массив эффективных плеч -> массив порогов."""
    t = np.clip(-(1.0 - mmr * np.asarray(leverage, dtype="float64")), LIQ_FLOOR, LIQ_CEIL)
    return float(t) if t.ndim == 0 else t


@dataclass
class Result:
    equity: pd.Series
    bh_equity: pd.Series
    position: pd.Series
    bar_returns: pd.Series
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)


def minutes_of(index: pd.DatetimeIndex) -> float:
    return float(pd.Series(index).diff().median().total_seconds() / 60)


def backtest(df: pd.DataFrame, signal: pd.Series, fee: float = DEFAULT_PERP_FEE, slippage: float = 0.0002,
             leverage: float = 1.0, long_only: bool = False, mmr: float = MAINT_MARGIN_RATE) -> Result:
    sig = signal.reindex(df.index).fillna(0.0)
    if long_only:
        sig = sig.clip(lower=0)
    pos = sig.shift(1).fillna(0.0)

    close = df["close"]
    prev = close.shift()
    ret = close.pct_change().fillna(0.0)
    turnover = pos.diff().abs().fillna(pos.abs())

    bar = leverage * pos * ret - turnover * (fee + slippage) * leverage
    if "funding" in df:  # положительный фандинг: лонги платят, шорты получают
        bar = bar - leverage * pos * df["funding"]
    adverse = np.where(pos > 0, df["low"] / prev - 1, np.where(pos < 0, -(df["high"] / prev - 1), 0.0))
    lev_eff = leverage * pos.abs()          # эффективное плечо свечи: доля капитала в позиции * плечо
    liquidated = (lev_eff * pd.Series(adverse, index=df.index).fillna(0.0)) <= liquidation_threshold(lev_eff.to_numpy(), mmr)
    bar = bar.where(~liquidated, -1.0).clip(lower=-1.0)     # капитал не может стать отрицательным

    ruined = (bar <= -1.0).to_numpy()
    if ruined.any():        # разорение: дальше капитал 0, позиции нет, доходности/ликвидации не считаются
        after = np.arange(len(bar)) > int(ruined.argmax())
        bar = bar.mask(after, 0.0)
        pos = pos.mask(after, 0.0)
        liquidated = liquidated & ~after

    equity = (1 + bar).cumprod()
    bh = close / close.iloc[0]

    trades = _trades(pos, bar, df.index, (fee + slippage) * leverage)
    res = Result(equity, bh, pos, bar, trades)
    res.metrics = metrics(res, minutes_of(df.index), int(liquidated.sum()))
    return res


def _trades(pos: pd.Series, bar: pd.Series, index: pd.DatetimeIndex, unit_cost: float = 0.0) -> pd.DataFrame:
    """Сделки = непрерывные участки одного знака позиции (смена знака или уход в ноль закрывает сделку; изменение размера
    при том же знаке — НЕ новая сделка). Доходность сделки включает комиссию входа (она в доходности первой свечи) и
    комиссию ВЫХОДА: она списывается на свече закрытия (позиция там уже 0 либо новая), поэтому приписывается закрываемой
    сделке; при развороте комиссия делится: |pos_prev|*unit_cost — закрываемой, остальное — новой. unit_cost = (fee+slip)*leverage."""
    p = pos.to_numpy(dtype="float64")
    b = bar.to_numpy(dtype="float64").copy()
    sgn = np.sign(p)
    active = sgn != 0
    if not active.any():
        return pd.DataFrame(columns=["entry", "exit", "side", "return"])
    sprev = np.concatenate([[0.0], sgn[:-1]])
    pprev = np.concatenate([[0.0], p[:-1]])
    start = active & (sgn != sprev)
    closing = (sprev != 0) & (sgn != sprev)                  # на этой свече закрылась сделка предыдущей свечи
    seg = np.cumsum(start)                                   # номер сделки (с 1) для активных свечей
    n_tr = int(seg[-1]) if len(seg) else 0

    exit_part = np.where(closing & (sgn != 0), np.abs(pprev) * unit_cost, 0.0)    # доля комиссии выхода при развороте
    b_new = np.where(exit_part > 0, np.where(b > -1.0, np.maximum(b + exit_part, -1.0), b), b)
    lg_act = np.log1p(np.maximum(b_new, -0.999999))
    lg = np.bincount(seg[active], weights=lg_act[active], minlength=n_tr + 1)[1:]

    ci = np.flatnonzero(closing)
    if len(ci):                                               # вклад свечи закрытия в закрываемую сделку
        to_zero = sgn[ci] == 0
        lg_exit = np.where(to_zero, np.log1p(np.maximum(b[ci], -0.999999)), np.log1p(-np.minimum(exit_part[ci], 0.999999)))
        lg = lg + np.bincount(seg[ci - 1], weights=lg_exit, minlength=n_tr + 1)[1:]

    entry_i = np.flatnonzero(start)
    end = active & (np.concatenate([sgn[1:], [0.0]]) != sgn)
    exit_i = np.flatnonzero(end)
    return pd.DataFrame({"entry": index[entry_i], "exit": index[exit_i],
                         "side": np.where(sgn[entry_i] > 0, "long", "short"), "return": np.expm1(lg)})


def monthly_returns(equity: pd.Series) -> pd.Series:
    m = equity.resample("ME").last()
    first = equity.iloc[0]
    prev = m.shift().fillna(first)
    return (m / prev - 1)


def metrics(res: Result, minutes: float, liquidations: int = 0) -> dict:
    eq, bar = res.equity, res.bar_returns
    n = len(eq)
    if n < 3:
        return {}
    span_days = (eq.index[-1] - eq.index[0]).total_seconds() / 86400
    years = max(span_days / 365.25, 1e-9)
    total = eq.iloc[-1] - 1
    if span_days < MIN_CAGR_DAYS:      # годовая экстраполяция короткой выборки — шум: CAGR и Calmar не определены
        cagr = None
    else:
        cagr = eq.iloc[-1] ** (1 / years) - 1 if eq.iloc[-1] > 0 else -1.0
    dd = eq / eq.cummax() - 1
    bars_per_year = 525_600 / minutes
    sd = bar.std()
    sharpe = bar.mean() / sd * np.sqrt(bars_per_year) if sd > 0 else 0.0
    dsd = float(np.sqrt(np.mean(np.minimum(bar.to_numpy(), 0.0) ** 2)))     # downside deviation по ВСЕМ свечам
    sortino = bar.mean() / dsd * np.sqrt(bars_per_year) if dsd > 0 else 0.0
    mdd = dd.min()
    mr = monthly_returns(eq)
    # неполные крайние месяцы не учитываем, если месяцев достаточно
    mr_full = mr.iloc[1:-1] if len(mr) > 4 else mr
    t = res.trades
    wins = t.loc[t["return"] > 0, "return"]
    losses = t.loc[t["return"] <= 0, "return"]
    pf = wins.sum() / -losses.sum() if len(losses) and losses.sum() < 0 else (np.inf if len(wins) else 0.0)
    return {
        "total_return": total,
        "cagr": cagr,
        "buy_hold_return": res.bh_equity.iloc[-1] - 1,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": None if cagr is None else (cagr / abs(mdd) if mdd < 0 else 0.0),
        "trades": len(t),
        "win_rate": float((t["return"] > 0).mean()) if len(t) else 0.0,
        "profit_factor": pf,
        "avg_trade": float(t["return"].mean()) if len(t) else 0.0,
        "exposure": float((res.position != 0).mean()),
        "avg_month": float(mr_full.mean()) if len(mr_full) else 0.0,
        "median_month": float(mr_full.median()) if len(mr_full) else 0.0,
        "green_months": float((mr_full > 0).mean()) if len(mr_full) else 0.0,
        "months_15plus": float((mr_full >= 0.15).mean()) if len(mr_full) else 0.0,
        "worst_month": float(mr_full.min()) if len(mr_full) else 0.0,
        "liquidations": liquidations,
    }
