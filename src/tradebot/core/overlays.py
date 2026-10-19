"""Надстройки над сигналом стратегии: дневной трендовый фильтр и трейлинг-стоп по ATR."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradebot.core.indicators import atr, ema


def htf_filter(df: pd.DataFrame, sig: pd.Series, n: int = 50) -> pd.Series:
    """Разрешает только лонги, когда цена выше дневной EMA(n) на вчерашнем закрытии, и только шорты — когда ниже."""
    daily = df["close"].resample("1D").last().dropna()
    bull = (daily > ema(daily, n)).astype(float)
    bull[ema(daily, n).isna()] = np.nan
    bull = bull.shift(1).reindex(df.index, method="ffill")
    out = sig.where(bull == 1, sig.clip(upper=0)).where(bull != 0, sig.clip(lower=0))
    return out.where(bull.notna(), 0.0)


def atr_stop(df: pd.DataFrame, sig: pd.Series, mult: float, n: int = 14) -> pd.Series:
    """Трейлинг-стоп по закрытию: выход, если цена откатила от экстремума позиции на mult*ATR(в момент входа).
    После стопа тот же сигнал игнорируется, пока он не сменится."""
    s = sig.fillna(0).to_numpy()
    c = df["close"].to_numpy()
    a = atr(df, n).to_numpy()
    out = np.zeros(len(s))
    pos = 0.0
    blocked = 0.0
    peak = 0.0
    risk = 0.0
    for i in range(len(s)):
        want = s[i]
        if want != blocked:
            blocked = 0.0
        if blocked != 0.0 and want == blocked:
            want = 0.0
        if want != pos:
            pos = want
            if pos != 0 and not np.isnan(a[i]):
                peak, risk = c[i], mult * a[i]
            elif pos != 0:
                pos = 0.0
        if pos > 0:
            peak = max(peak, c[i])
            if c[i] < peak - risk:
                blocked, pos = 1.0, 0.0
        elif pos < 0:
            peak = min(peak, c[i])
            if c[i] > peak + risk:
                blocked, pos = -1.0, 0.0
        out[i] = pos
    return pd.Series(out, index=df.index)


VARIANTS = {
    "base": {},
    "+HTF50": {"htf": 50},
    "+stop3ATR": {"stop": 3.0},
    "+HTF50+stop3ATR": {"htf": 50, "stop": 3.0},
}


def apply(df: pd.DataFrame, sig: pd.Series, htf: int | None = None, stop: float | None = None) -> pd.Series:
    if htf:
        sig = htf_filter(df, sig, htf)
    if stop:
        sig = atr_stop(df, sig, stop)
    return sig
