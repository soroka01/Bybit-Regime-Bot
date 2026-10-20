"""Надстройки над сигналом стратегии: дневной трендовый фильтр и трейлинг-стоп по ATR."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradebot.core.indicators import atr, ema


def htf_filter(df: pd.DataFrame, sig: pd.Series, n: int = 50) -> pd.Series:
    """Дневной трендовый фильтр: цена выше дневной EMA(n) на вчерашнем закрытии (бык) -> разрешены только лонги
    (sig.clip(lower=0)), ниже (медведь) -> только шорты (sig.clip(upper=0)); прогрев EMA (первые n дней) -> 0.
    Дневное значение сдвинуто на 1 день (shift(1)): внутри дня используется только закрытие предыдущего дня."""
    daily = df["close"].resample("1D").last().dropna()
    e = ema(daily, n).where(np.arange(len(daily)) >= n - 1)      # EMA считаем прогретой только после n дневных свечей
    bull = (daily > e).astype(float)
    bull[e.isna()] = np.nan
    bull = bull.shift(1).reindex(df.index, method="ffill")
    return sig.clip(lower=0).where(bull == 1, sig.clip(upper=0).where(bull == 0, 0.0))


def atr_stop(df: pd.DataFrame, sig: pd.Series, mult: float, n: int = 14) -> pd.Series:
    """Трейлинг-стоп по закрытию: выход, если цена откатила от экстремума позиции на mult*ATR(в момент входа).
    После стопа то же НАПРАВЛЕНИЕ сигнала игнорируется, пока оно не сменится. Логика привязана к знаку сигнала, поэтому
    работает и для дробных сигналов (вол-таргет, ансамбли): изменение размера внутри направления не сбрасывает экстремум."""
    s = sig.fillna(0).to_numpy(dtype="float64")
    c = df["close"].to_numpy()
    a = atr(df, n).to_numpy()
    out = np.zeros(len(s))
    side = 0.0      # направление текущей позиции (-1/0/+1)
    blocked = 0.0   # направление, заблокированное после стопа
    peak = 0.0
    risk = 0.0
    for i in range(len(s)):
        want = s[i]
        w = float(np.sign(want))
        if w != blocked:
            blocked = 0.0
        if blocked != 0.0 and w == blocked:
            want, w = 0.0, 0.0
        if w != side:
            if w != 0 and np.isnan(a[i]):       # ATR ещё не посчитан: не входим (повторим на следующей свече)
                want, w = 0.0, 0.0
            side = w
            if side != 0:
                peak, risk = c[i], mult * a[i]
        if side > 0:
            peak = max(peak, c[i])
            if c[i] < peak - risk:
                blocked, side, want = 1.0, 0.0, 0.0
        elif side < 0:
            peak = min(peak, c[i])
            if c[i] > peak + risk:
                blocked, side, want = -1.0, 0.0, 0.0
        out[i] = want if side != 0 else 0.0
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
