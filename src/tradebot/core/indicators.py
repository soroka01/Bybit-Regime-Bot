"""Индикаторы на pandas/numpy (без TA-Lib)."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def macd(s: pd.Series, fast: int, slow: int, signal: int):
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return line, sig


def bollinger(s: pd.Series, n: int, k: float):
    mid = sma(s, n)
    sd = s.rolling(n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def supertrend(df: pd.DataFrame, n: int, mult: float) -> pd.Series:
    """Возвращает направление тренда: +1 / -1."""
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    a = atr(df, n).to_numpy()
    close = df["close"].to_numpy()
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    size = len(close)
    fu, fl = upper.copy(), lower.copy()
    direction = np.ones(size)
    for i in range(1, size):
        if np.isnan(a[i]):
            continue
        fu[i] = upper[i] if (upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lower[i] if (lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
        if direction[i - 1] == 1:
            direction[i] = -1 if close[i] < fl[i] else 1
        else:
            direction[i] = 1 if close[i] > fu[i] else -1
    out = pd.Series(direction, index=df.index)
    out[pd.Series(a, index=df.index).isna()] = 0
    return out


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def hold(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """Состояние 1/0: включается на entry, выключается на exit_, иначе держится (entry в приоритете)."""
    s = pd.Series(np.nan, index=entry.index)
    s[exit_.fillna(False)] = 0.0
    s[entry.fillna(False)] = 1.0
    return s.ffill().fillna(0.0)


# ======================================================================================================
# Индикаторы, добавленные для расширенного набора стратегий (существующие функции выше не менялись).
# Везде значение в момент t использует только данные <= t. Рекурсии — простым циклом по спискам Python
# (быстрее, чем доступ к элементам pandas/numpy по одному).
# ======================================================================================================
def bars_per_year(df: pd.DataFrame) -> float:
    """Число свечей в году: 365.25*24*60 / медианный шаг в минутах."""
    step_min = float(df.index.to_series().diff().median().total_seconds() / 60)
    return 365.25 * 24 * 60 / step_min


def vol_scale(df: pd.DataFrame, win: int) -> pd.Series:
    """Годовая волатильность: std лог-доходностей за win свечей * sqrt(bars_per_year). NaN в прогреве."""
    lr = np.log(df["close"]).diff()
    return lr.rolling(win).std() * np.sqrt(bars_per_year(df))


def kama(close: pd.Series, n: int, fast: int, slow: int) -> tuple[pd.Series, pd.Series]:
    """Адаптивная средняя Кауфмана. Возвращает (kama, er). Первое значение (индекс n) = close; до него NaN."""
    c = close.to_numpy(dtype=float)
    size = len(c)
    den = close.diff().abs().rolling(n).sum().to_numpy()
    num = (close - close.shift(n)).abs().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        er = np.where(den > 0, num / den, 0.0)
    er = np.where(np.isnan(den), np.nan, er)
    f, s = 2.0 / (fast + 1), 2.0 / (slow + 1)
    sc = ((er * (f - s) + s) ** 2).tolist()
    cl = c.tolist()
    out = [float("nan")] * size
    if size > n:
        k = cl[n]
        out[n] = k
        for i in range(n + 1, size):
            k += sc[i] * (cl[i] - k)
            out[i] = k
    return pd.Series(out, index=close.index), pd.Series(er, index=close.index)


def parabolic_sar(df: pd.DataFrame, af_start: float, af_step: float, af_max: float) -> tuple[pd.Series, pd.Series]:
    """Параболический SAR Уайлдера. Возвращает (sar, trend): trend = +1 (цена выше SAR) / -1 (ниже), 0 на первой свече.
    Разворот на свече i определяется по её high/low против SAR, посчитанного из свечей < i, т.е. известен на закрытии i."""
    hi, lo, cl = df["high"].tolist(), df["low"].tolist(), df["close"].tolist()
    size = len(cl)
    sar_o = [float("nan")] * size
    tr_o = [0.0] * size
    if size < 3:
        return pd.Series(sar_o, index=df.index), pd.Series(tr_o, index=df.index)
    up = cl[1] >= cl[0]
    sar = lo[0] if up else hi[0]
    ep = hi[1] if up else lo[1]
    af = af_start
    sar_o[1], tr_o[1] = sar, (1.0 if up else -1.0)
    for i in range(2, size):
        sar = sar + af * (ep - sar)
        if up:
            m = lo[i - 1] if lo[i - 1] < lo[i - 2] else lo[i - 2]
            if m < sar:
                sar = m
            if lo[i] < sar:
                up, sar, ep, af = False, ep, lo[i], af_start
            elif hi[i] > ep:
                ep = hi[i]
                af = af + af_step if af + af_step < af_max else af_max
        else:
            m = hi[i - 1] if hi[i - 1] > hi[i - 2] else hi[i - 2]
            if m > sar:
                sar = m
            if hi[i] > sar:
                up, sar, ep, af = True, ep, hi[i], af_start
            elif lo[i] < ep:
                ep = lo[i]
                af = af + af_step if af + af_step < af_max else af_max
        sar_o[i], tr_o[i] = sar, (1.0 if up else -1.0)
    return pd.Series(sar_o, index=df.index), pd.Series(tr_o, index=df.index)


def _donchian_trail_state(close: pd.Series, n: int) -> np.ndarray:
    """Лонг-состояние (0/1) «Donchian с трейлинг-стопом» для окна n (вычисление без кэша).
    Вход: close_t > max(close за предыдущие n свечей). Стоп = max(прошлый стоп, (max+min closes за n свечей вкл. t)/2),
    выход при close_t < стоп."""
    c = close.to_numpy(dtype=float)
    prev_max = close.rolling(n).max().shift().to_numpy().tolist()
    mid = ((close.rolling(n).max() + close.rolling(n).min()) / 2).to_numpy().tolist()
    cl = c.tolist()
    out = [0.0] * len(cl)
    inpos, stop = False, 0.0
    for i in range(len(cl)):
        x = cl[i]
        if inpos:
            m = mid[i]
            if m > stop:
                stop = m
            if x < stop:
                inpos = False
        elif x > prev_max[i]:      # NaN в прогреве -> False
            inpos, stop = True, mid[i]
        if inpos:
            out[i] = 1.0
    return np.array(out)


_TRAIL_CACHE: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
_TRAIL_CACHE_SIZE = 96          # ансамбль считает 6 окон, перебор параметров повторяет одни и те же (close, n) десятки раз
_trail_lock = threading.Lock()


def donchian_trail_state(close: pd.Series, n: int) -> np.ndarray:
    """Лонг-состояние (0/1) «Donchian с трейлинг-стопом» для окна n (см. _donchian_trail_state) с мемоизацией по СОДЕРЖИМОМУ
    (хэш значений close + n): перебор параметров ансамбля пересчитывает одно и то же состояние для одних и тех же окон.
    Результат побитово тот же, что у прямого расчёта; вызывающему отдаётся новый массив, кэш изменить нельзя."""
    c = np.ascontiguousarray(close.to_numpy(dtype=float))
    key = (hashlib.blake2b(c.tobytes(), digest_size=16).digest(), len(c), int(n))
    with _trail_lock:
        hit = _TRAIL_CACHE.get(key)
        if hit is not None:
            _TRAIL_CACHE.move_to_end(key)
            return hit.astype(float)
    out = _donchian_trail_state(close, n)
    with _trail_lock:
        _TRAIL_CACHE[key] = out.astype(np.int8)       # значения только 0/1: int8 в 8 раз компактнее
        while len(_TRAIL_CACHE) > _TRAIL_CACHE_SIZE:
            _TRAIL_CACHE.popitem(last=False)
    return out


def timed_hold(event: pd.Series, hold_bars: int, close: pd.Series | None = None, atr_: pd.Series | None = None,
               stop_mult: float = 0.0, side: float = 1.0) -> pd.Series:
    """Держим позицию hold_bars свечей после события (повторное событие продлевает срок).
    stop_mult > 0: трейлинг-стоп на close (для лонга: макс. за время сделки от close - mult*ATR; для шорта зеркально);
    после срабатывания стопа позиция 0 до следующего события. Возвращает 0/1 (знак side применять снаружи)."""
    if not stop_mult or stop_mult <= 0:
        return event.astype(float).rolling(hold_bars, min_periods=1).max().fillna(0.0)
    ev = event.fillna(False).to_numpy(dtype=bool).tolist()
    cl = close.to_numpy(dtype=float).tolist()
    ad = (stop_mult * atr_.to_numpy(dtype=float)).tolist()
    out = [0.0] * len(ev)
    rem, stop = 0, 0.0
    for i in range(len(ev)):
        was = rem > 0
        rem = hold_bars if ev[i] else (rem - 1 if rem > 0 else 0)
        if rem > 0:
            ns = cl[i] - ad[i] if side > 0 else -(cl[i] + ad[i])   # для шорта работаем в зеркальных ценах
            px = cl[i] if side > 0 else -cl[i]
            if not was or ns > stop:
                stop = ns
            if px < stop:
                rem = 0
        if rem > 0:
            out[i] = 1.0
    return pd.Series(out, index=event.index)
