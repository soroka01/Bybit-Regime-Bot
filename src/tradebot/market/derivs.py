"""Деривативные данные Bybit: фандинг, open interest, long/short ratio. Кэш в parquet."""
from __future__ import annotations

import time

import pandas as pd
import requests

from tradebot.market import klines as data

BASE = "https://api.bybit.com/v5/market/"


from tradebot.market.klines import _atomic_parquet  # noqa: E402


def _get(path: str, params: dict) -> list:
    for attempt in range(5):
        try:
            r = requests.get(BASE + path, params=params, timeout=20)
            r.raise_for_status()
            js = r.json()
            if js["retCode"] != 0:
                raise RuntimeError(js["retMsg"])
            return js["result"]["list"]
        except Exception:
            if attempt == 4:
                raise
            time.sleep(1.5 * (attempt + 1))


def _paginate(path, params, ts_key, start_ms, end_ms):
    rows, end = [], end_ms
    while end > start_ms:
        chunk = _get(path, {**params, "startTime": start_ms, "endTime": end})
        if not chunk:
            break
        rows.extend(chunk)
        oldest = min(int(x[ts_key]) for x in chunk)
        if oldest <= start_ms:
            break
        end = oldest - 1
        time.sleep(0.05)
    return rows


# Гранулярность OI/LSR (интервалы Bybit open-interest / account-ratio) по длине свечи в минутах.
GRANULARITY = {1: "5min", 3: "5min", 5: "5min", 15: "15min", 30: "30min", 60: "1h", 120: "1h",
               240: "4h", 360: "4h", 720: "4h", 1440: "1d", 10080: "1d"}
GRAN_MINUTES = {"5min": 5, "15min": 15, "30min": 30, "1h": 60, "4h": 240, "1d": 1440}


def granularity_for(minutes: float) -> str:
    """Период OI/LSR для свечей длиной `minutes`. Для 1h/4h — как и раньше ('1h'/'4h')."""
    m = int(round(minutes))
    if m in GRANULARITY:
        return GRANULARITY[m]
    return "1h" if m <= 60 else "4h"


def estimate_requests(tf_minutes: float, days: int) -> int:
    """Грубая оценка числа запросов к Bybit для OI+LSR+funding на `days` (без свечей)."""
    g = GRAN_MINUTES[granularity_for(tf_minutes)]
    pts = days * 1440 / g
    return int(-(-pts // 200) + -(-pts // 500) + -(-1095 * 3 // 200))


def _incremental(name: str, fetch, days: int, refresh: bool = True) -> pd.DataFrame:
    """fetch(start_ms, end_ms) -> DataFrame (индекс ts). Докачивает только новое."""
    data.DATA_DIR.mkdir(exist_ok=True)
    p = data.DATA_DIR / f"{name}.parquet"
    now = int(time.time() * 1000)
    old = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    if len(old) and not refresh:
        return old
    start = (old.index[-1].value // 1_000_000 - 1) if len(old) else now - days * 86_400_000
    new = fetch(start, now)
    parts = [old, new]
    want_start = now - days * 86_400_000
    if len(old) and old.index[0].value // 1_000_000 - want_start > 7 * 86_400_000:
        # кэш короче запрошенной глубины (например, раньше качали только 1m на 90 дней) — достраиваем историю назад
        parts.append(fetch(want_start, old.index[0].value // 1_000_000 - 1))
    df = pd.concat([x for x in parts if len(x)]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    _atomic_parquet(df, p)
    return df


def _frame(rows, ts_key, col, src):
    d = pd.DataFrame(rows)
    if d.empty:
        return pd.DataFrame(columns=[col])
    d["ts"] = pd.to_datetime(d[ts_key].astype("int64"), unit="ms", utc=True)
    return d.assign(**{col: d[src].astype(float)}).set_index("ts")[[col]].sort_index()


def funding(symbol="BTCUSDT", days=1095, refresh=True) -> pd.Series:
    """Ставка фандинга на момент расчёта (каждые 8ч): положительная -> лонги платят шортам."""
    def f(a, b):
        rows = _paginate("funding/history", {"category": "linear", "symbol": symbol, "limit": 200},
                         "fundingRateTimestamp", a, b)
        return _frame(rows, "fundingRateTimestamp", "rate", "fundingRate")
    return _incremental(f"{symbol}_funding", f, days, refresh)["rate"]


def open_interest(symbol="BTCUSDT", days=1095, interval="1h", refresh=True) -> pd.Series:
    def f(a, b):
        rows = _paginate("open-interest", {"category": "linear", "symbol": symbol, "intervalTime": interval,
                                           "limit": 200}, "timestamp", a, b)
        return _frame(rows, "timestamp", "oi", "openInterest")
    return _incremental(f"{symbol}_oi_{interval}", f, days, refresh)["oi"]


def long_short_ratio(symbol="BTCUSDT", days=1095, period="1h", refresh=True) -> pd.Series:
    def f(a, b):
        rows = _paginate("account-ratio", {"category": "linear", "symbol": symbol, "period": period, "limit": 500},
                         "timestamp", a, b)
        d = _frame(rows, "timestamp", "buy", "buyRatio")
        if d.empty:
            return d.rename(columns={"buy": "ratio"})
        sell = _frame(rows, "timestamp", "sell", "sellRatio")
        return (d["buy"] / sell["sell"]).rename("ratio").to_frame()
    return _incremental(f"{symbol}_lsr_{period}", f, days, refresh)["ratio"]


def enrich(df: pd.DataFrame, symbol="BTCUSDT", refresh=True, days=1095) -> pd.DataFrame:
    """Добавляет к свечам: funding (платёж в свече, если в ней был расчёт), funding_last, oi, lsr.
    Значения «последние известные на закрытии свечи» (ffill по времени закрытия) -> без look-ahead.
    Гранулярность OI/LSR — по таблице GRANULARITY (1h для 1h/2h, 4h для 4h/6h/12h и т.д.)."""
    out = df.copy()
    step = out.index.to_series().diff().median()
    bar_start = out.index
    close_t = bar_start + step
    f = funding(symbol, max(days, 1095), refresh)       # фандинг — один общий файл, всегда полная история
    if int(bar_start[0].value) % int(step.value) == 0:   # сетка кратна эпохе (всё, кроме недельных) — прежний путь
        out["funding"] = f.groupby(f.index.floor(step)).sum().reindex(bar_start).fillna(0.0)
    else:                                                # недельные свечи (понедельник): ищем свечу по позиции
        pos = bar_start.searchsorted(f.index, side="right") - 1
        ok = (pos >= 0) & (f.index < bar_start[pos.clip(0)] + step)
        paid = pd.Series(f.values[ok], index=bar_start[pos[ok]]).groupby(level=0).sum()
        out["funding"] = paid.reindex(bar_start).fillna(0.0)
    out["funding_last"] = pd.Series(f.values, index=f.index).reindex(close_t, method="ffill").set_axis(bar_start)
    gran = granularity_for(step / pd.Timedelta(minutes=1))
    oi = open_interest(symbol, days, gran, refresh)
    out["oi"] = oi.reindex(close_t, method="ffill").set_axis(bar_start)
    try:
        lsr = long_short_ratio(symbol, days, gran, refresh)
        out["lsr"] = lsr.reindex(close_t, method="ffill").set_axis(bar_start)
    except Exception:
        out["lsr"] = float("nan")
    return out


if __name__ == "__main__":
    f = funding()
    print("funding", len(f), f.index[0], f.index[-1], f"mean/8h {f.mean():.6f}  ~год {f.mean() * 3 * 365:.1%}")
    o = open_interest(interval='1h')
    print("oi", len(o), o.index[0], o.index[-1])
    try:
        l = long_short_ratio()
        print("lsr", len(l), l.index[0], l.index[-1])
    except Exception as e:
        print("lsr failed", e)
