"""Метрики кошелька (equity): окно, просадка, дневные доходности, Sharpe/Sortino/Calmar, разрывы; кошелёк на момент t."""
from __future__ import annotations

import math

import numpy as np

from tradebot.web.stats_base import (ANN, EQ_ENTRY_MAX_AGE_S, GAP_S, MIN_DAYS_CALMAR, MIN_DAYS_SHARPE, _day, _num)


def select_series(t: np.ndarray, v: np.ndarray, start: float, end: float):
    """(t, v, starts_before_data) для окна: от eq_start (последняя точка <= start, иначе первая) до последней точки <= end."""
    if not len(t):
        return t, v, False
    i0 = int(np.searchsorted(t, start, side="right")) - 1
    before = i0 < 0
    i0 = max(i0, 0)
    i1 = int(np.searchsorted(t, end, side="right"))
    return t[i0:i1], v[i0:i1], before


def equity_metrics(t: np.ndarray, v: np.ndarray, start: float, end: float) -> dict | None:
    """Метрики по кошельку для окна [start, end]; None, если в окне меньше 2 точек. Внутри — numpy, наружу — числа/None."""
    ts, vs, before = select_series(t, v, start, end)
    if len(ts) < 2:
        return None
    eq0, eq1 = float(vs[0]), float(vs[-1])
    rm = np.maximum.accumulate(vs)
    dd = vs / rm - 1.0
    j = int(dd.argmin())
    i = int(vs[: j + 1].argmax())
    mdd = float(dd[j])
    # дневные доходности: последняя точка каждого дня UTC, доходность только если есть точка предыдущего дня
    day = ts // 86400
    last = np.flatnonzero(np.diff(day, append=day[-1] + 1))
    dd_day, dd_close = day[last], vs[last]
    rets, rdays = [], []
    for k in range(1, len(dd_day)):
        if dd_day[k] - dd_day[k - 1] == 1 and dd_close[k - 1] > 0:
            rets.append(dd_close[k] / dd_close[k - 1] - 1.0)
            rdays.append(int(dd_day[k]))
    r = np.array(rets, dtype="float64")
    n = len(r)
    sd = float(r.std(ddof=1)) if n >= 2 else None
    sharpe = sortino = calmar = vol = None
    if sd is not None:
        vol = sd * ANN * 100.0
    if n >= MIN_DAYS_SHARPE and sd and sd > 0:
        sharpe = float(r.mean()) / sd * ANN
        neg = r[r < 0]
        if len(neg):
            dsd = math.sqrt(float((np.minimum(r, 0.0) ** 2).mean()))
            sortino = float(r.mean()) / dsd * ANN if dsd > 0 else None
    span = max(float(ts[-1] - max(start, ts[0])), 1.0)
    if n >= MIN_DAYS_CALMAR and mdd < 0 and eq0 > 0:
        try:
            ann_ret = (eq1 / eq0) ** (365 * 86400 / span) - 1.0
            calmar = ann_ret / abs(mdd)
        except (OverflowError, ValueError, ZeroDivisionError):
            calmar = None
    gaps = np.diff(ts)
    big = gaps[gaps > GAP_S]
    tail = end - ts[-1]
    tail_gap = float(tail) if tail > GAP_S else 0.0
    best = int(r.argmax()) if n else None
    worst = int(r.argmin()) if n else None
    delta = eq1 - eq0
    return {
        "eq_start": _num(eq0, 4), "eq_end": _num(eq1, 4), "eq_start_ts": int(ts[0]), "eq_end_ts": int(ts[-1]),
        "starts_before_data": bool(before), "points": int(len(ts)), "delta": _num(delta, 4),
        "wallet_return_pct": _num((eq1 / eq0 - 1.0) * 100.0, 4) if eq0 > 0 else None,
        "max_drawdown_pct": _num(mdd * 100.0, 4), "max_drawdown_peak_ts": int(ts[i]), "max_drawdown_trough_ts": int(ts[j]),
        "daily_points": n, "volatility_pct": _num(vol, 4), "sharpe": _num(sharpe, 4), "sortino": _num(sortino, 4),
        "calmar": _num(calmar, 4),
        "best_day": {"date": _day(rdays[best] * 86400), "ret_pct": _num(r[best] * 100, 4)} if n else None,
        "worst_day": {"date": _day(rdays[worst] * 86400), "ret_pct": _num(r[worst] * 100, 4)} if n else None,
        "gaps": {"count": int(len(big)) + (1 if tail_gap else 0), "total_s": int(big.sum() + tail_gap) if (len(big) or tail_gap) else 0},
        "_series": (ts, vs, dd), "_daily": (rdays, r),
    }


def equity_at(eq_t: np.ndarray, eq_v: np.ndarray, t: float) -> float | None:
    """Кошелёк на момент t: последняя точка equity не позже t и не старше EQ_ENTRY_MAX_AGE_S; нет такой (или значение <= 0) -> None."""
    if not len(eq_t):
        return None
    i = int(np.searchsorted(eq_t, t, side="right")) - 1
    if i < 0 or t - float(eq_t[i]) > EQ_ENTRY_MAX_AGE_S:
        return None
    v = float(eq_v[i])
    return v if math.isfinite(v) and v > 0 else None
