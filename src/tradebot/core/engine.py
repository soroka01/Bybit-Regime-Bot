"""Векторный бэктест-движок.

Сигнал считается по закрытию свечи t, позиция открывается по её close и держится до close t+1
(look-ahead исключён: pos = signal.shift(1)). Комиссия+проскальзывание берутся с каждого
изменения позиции. Плечо масштабирует доходность; ликвидация приближённо проверяется по
high/low свечи. Фандинг учитывается, если в df есть колонка "funding".

Модель ликвидации (maintenance margin): позиция ликвидируется, когда нереализованный убыток съедает всю маржу
за вычетом поддерживающей: lev * adverse <= -(1 - MMR * lev), MMR = 0.5 % (MAINT_MARGIN_RATE). Порог ограничен
диапазоном [-0.995; -0.5]. Эквивалентно движению против позиции на (1/lev - MMR): при 100x ликвидация после
≈ 0.5 %, при 50x — ≈ 1.5 %, при 10x — ≈ 9.5 %, при 3x — ≈ 32.8 %, при 1x — не достижима (-99.5 %).
Ликвидированная свеча теряет 100 % капитала.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BYBIT_TAKER_FEE = 0.00055  # 0.055% — базовый тариф taker для деривативов
MAINT_MARGIN_RATE = 0.005  # 0.5 % — поддерживающая маржа (MMR), упрощённо для BTCUSDT на Bybit
LIQ_FLOOR, LIQ_CEIL = -0.995, -0.5   # границы порога lev*adverse: не мягче -99.5 % и не жёстче -50 %


def liquidation_threshold(leverage: float, mmr: float = MAINT_MARGIN_RATE) -> float:
    """Порог lev*adverse, при котором позиция ликвидируется: -(1 - mmr*lev), зажатый в [-0.995; -0.5]."""
    return float(min(max(-(1.0 - mmr * leverage), LIQ_FLOOR), LIQ_CEIL))


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


def backtest(df: pd.DataFrame, signal: pd.Series, fee: float = BYBIT_TAKER_FEE, slippage: float = 0.0002,
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
    liquidated = (leverage * pd.Series(adverse, index=df.index).fillna(0.0)) <= liquidation_threshold(leverage, mmr)
    bar = bar.where(~liquidated, -1.0)

    equity = (1 + bar).cumprod()
    bh = close / close.iloc[0]

    trades = _trades(pos, bar, df.index)
    res = Result(equity, bh, pos, bar, trades)
    res.metrics = metrics(res, minutes_of(df.index), int(liquidated.sum()))
    return res


def _trades(pos: pd.Series, bar: pd.Series, index: pd.DatetimeIndex) -> pd.DataFrame:
    seg = (pos != pos.shift()).cumsum()
    active = pos != 0
    if not active.any():
        return pd.DataFrame(columns=["entry", "exit", "side", "return"])
    lg = np.log1p(bar.clip(lower=-0.999999))
    g = pd.DataFrame({"seg": seg[active], "lg": lg[active], "side": pos[active], "t": index[active]})
    agg = g.groupby("seg").agg(entry=("t", "first"), exit=("t", "last"), side=("side", "first"), lg=("lg", "sum"))
    agg["return"] = np.expm1(agg["lg"])
    agg["side"] = np.where(agg["side"] > 0, "long", "short")
    return agg.drop(columns="lg").reset_index(drop=True)


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
    years = max((eq.index[-1] - eq.index[0]).total_seconds() / (365.25 * 86400), 1e-9)
    total = eq.iloc[-1] - 1
    cagr = eq.iloc[-1] ** (1 / years) - 1 if eq.iloc[-1] > 0 else -1.0
    dd = eq / eq.cummax() - 1
    bars_per_year = 525_600 / minutes
    sd = bar.std()
    sharpe = bar.mean() / sd * np.sqrt(bars_per_year) if sd > 0 else 0.0
    dsd = bar[bar < 0].std()
    sortino = bar.mean() / dsd * np.sqrt(bars_per_year) if dsd and dsd > 0 else 0.0
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
        "calmar": cagr / abs(mdd) if mdd < 0 else 0.0,
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
