"""Преобразование pandas/numpy в JSON-безопасные структуры + даунсэмплинг рядов для графиков."""
from __future__ import annotations

import numpy as np
import pandas as pd


def fl(arr, nd: int = 6) -> list:
    """Массив -> список float; NaN/inf -> None."""
    a = np.asarray(arr, dtype="float64")
    out = np.round(a, nd).tolist()
    for i in np.flatnonzero(~np.isfinite(a)):
        out[i] = None
    return out


def num(x, nd: int = 6):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if np.isfinite(v) else None


def clean(obj):
    """Рекурсивно приводит numpy/pandas-типы к JSON-совместимым, NaN/inf -> None."""
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        return num(obj, 8)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, pd.Series):
        return clean(obj.tolist())
    if isinstance(obj, pd.DataFrame):
        return clean(obj.to_dict("records"))
    if obj is pd.NaT:
        return None
    return str(obj)


def secs(index: pd.DatetimeIndex) -> np.ndarray:
    """Unix-секунды (int64) для uPlot."""
    return (index.asi8 // (10**9 if index.unit == "ns" else {"us": 10**6, "ms": 10**3, "s": 1}[index.unit])).astype("int64")


def pick_indices(values: np.ndarray, n: int = 1500) -> np.ndarray:
    """Индексы точек для даунсэмплинга с сохранением min/max каждого бакета (+ первая/последняя точка)."""
    m = len(values)
    if m <= n:
        return np.arange(m)
    buckets = max(n // 2, 1)
    edges = np.linspace(0, m, buckets + 1).astype(int)
    keep = {0, m - 1}
    v = np.nan_to_num(np.asarray(values, dtype="float64"), nan=0.0)
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        seg = v[a:b]
        keep.add(a + int(seg.argmin()))
        keep.add(a + int(seg.argmax()))
    return np.array(sorted(keep), dtype=int)


def bucket_mean(values: np.ndarray, t: np.ndarray, n: int = 400) -> tuple[np.ndarray, np.ndarray]:
    """Среднее по бакетам (для полос экспозиции/сигналов)."""
    m = len(values)
    if m <= n:
        return t, values
    edges = np.linspace(0, m, n + 1).astype(int)
    idx = edges[:-1]
    sums = np.add.reduceat(values, idx)
    cnt = np.diff(np.append(idx, m))
    return t[idx], sums / np.maximum(cnt, 1)
