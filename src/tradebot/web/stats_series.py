"""Ряды для графиков: даунсэмплинг equity/просадки, корзины P/L по периодам (UTC), гистограмма доходностей."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from tradebot.web.stats_base import EQ_POINTS_MAX, _num


def _bucket_edges(t: np.ndarray, n: int):
    """Границы бакетов по ВРЕМЕНИ (равные интервалы): индексы начала каждой непустой группы."""
    t0, span = float(t[0]), float(t[-1] - t[0])
    if span <= 0:
        return np.array([0])
    idx = np.minimum(((t - t0) / span * n).astype("int64"), n - 1)
    return np.concatenate(([0], np.flatnonzero(np.diff(idx)) + 1))


def downsample(t: np.ndarray, v: np.ndarray, dd: np.ndarray, n: int = EQ_POINTS_MAX) -> dict:
    """Кривая equity и просадка не более n точек: на бакет — последняя (t, v) + min/max v и min просадки."""
    if len(t) <= n:
        return {"equity": {"t": [int(x) for x in t], "v": [_num(x, 4) for x in v], "lo": [_num(x, 4) for x in v],
                           "hi": [_num(x, 4) for x in v]},
                "drawdown": {"t": [int(x) for x in t], "dd_pct": [_num(x * 100, 4) for x in dd]}}
    starts = _bucket_edges(t, n)
    ends = np.append(starts[1:], len(t)) - 1
    lo, hi, dmin = np.minimum.reduceat(v, starts), np.maximum.reduceat(v, starts), np.minimum.reduceat(dd, starts)
    tt = t[ends]
    return {"equity": {"t": [int(x) for x in tt], "v": [_num(x, 4) for x in v[ends]], "lo": [_num(x, 4) for x in lo],
                       "hi": [_num(x, 4) for x in hi]},
            "drawdown": {"t": [int(x) for x in tt], "dd_pct": [_num(x * 100, 4) for x in dmin]}}


def _bucket_kind(range_key: str) -> str:
    return {"1h": "5min", "1d": "hour"}.get(range_key) or (
        "day" if range_key in ("3d", "7d", "30d") else "week" if range_key in ("90d", "180d", "365d") else "month")


def bucket_start(kind: str, ts: float) -> int:
    ts = int(ts)
    if kind == "5min":
        return ts // 300 * 300
    if kind == "hour":
        return ts // 3600 * 3600
    if kind == "day":
        return ts // 86400 * 86400
    if kind == "week":                                       # неделя с понедельника UTC (1970-01-01 — четверг)
        d = ts // 86400
        return ((d + 3) // 7 * 7 - 3) * 86400
    d = datetime.fromtimestamp(ts, timezone.utc)
    return int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp())


def _next_bucket(kind: str, b: int) -> int:
    if kind == "month":
        d = datetime.fromtimestamp(b, timezone.utc)
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp())
    return b + {"5min": 300, "hour": 3600, "day": 86400, "week": 7 * 86400}[kind]


def pnl_buckets(kind: str, trades: list[dict], start: float, end: float) -> list[dict]:
    """Корзины P/L от начала окна до конца (пустые — с нулями); сделки по времени выхода."""
    rows: dict[int, dict] = {}
    b, last = bucket_start(kind, start), bucket_start(kind, end)
    while b <= last:
        rows[b] = {"t": b, "n": 0, "wins": 0, "net": 0.0}
        b = _next_bucket(kind, b)
    for tr in trades:
        r = rows.get(bucket_start(kind, tr["exit_ts"]))
        if r is not None:
            r["n"] += 1
            r["wins"] += tr["net"] > 0
            r["net"] += tr["net"]
    return [{**r, "net": _num(r["net"], 4)} for r in rows.values()]


def histogram(vals: list[float]) -> dict:
    if not vals:
        return {"edges": [], "counts": []}
    a = np.asarray(vals, dtype="float64")
    nb = int(min(20, max(5, math.ceil(math.sqrt(len(a))))))
    if float(a.max()) == float(a.min()):
        lo = float(a.min())
        return {"edges": [_num(lo - 0.5, 4), _num(lo + 0.5, 4)], "counts": [int(len(a))]}
    counts, edges = np.histogram(a, bins=nb)
    return {"edges": [_num(x, 4) for x in edges], "counts": [int(x) for x in counts]}
