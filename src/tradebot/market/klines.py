"""Загрузка свечей с публичного API Bybit v5 (ключи не нужны) + кэш в parquet."""
from __future__ import annotations

import time

import pandas as pd
import requests

URL = "https://api.bybit.com/v5/market/kline"
from tradebot.paths import MARKET as DATA_DIR

# ключ -> (интервал Bybit, минут в свече) — ЕДИНСТВЕННЫЙ источник списка таймфреймов (веб, тесты, селекторы).
# Порядок по возрастанию длины. Месячный интервал «M» намеренно НЕ добавлен: свечи переменной длины ломают
# арифметику «старт + tf = закрытие свечи», на которой держится отсутствие look-ahead.
TIMEFRAMES = {
    "1m": ("1", 1),
    "3m": ("3", 3),
    "5m": ("5", 5),
    "15m": ("15", 15),
    "30m": ("30", 30),
    "1h": ("60", 60),
    "2h": ("120", 120),
    "4h": ("240", 240),
    "6h": ("360", 360),
    "12h": ("720", 720),
    "1d": ("D", 1440),
    "1w": ("W", 10080),   # недельные свечи Bybit стартуют в понедельник 00:00 UTC (эпоха — четверг!)
}

# Предел глубины истории (дней): на мелких свечах миллионы баров. Остальные — 1095.
HISTORY_DAYS = {"1m": 90, "3m": 270, "5m": 540}
DEFAULT_HISTORY_DAYS = 1095


def history_days(tf: str) -> int:
    return HISTORY_DAYS.get(tf, DEFAULT_HISTORY_DAYS)


def effective_days(tf: str, days: int) -> int:
    """Запрошенная глубина, ограниченная пределом таймфрейма."""
    return max(1, min(int(days), history_days(tf)))


def estimate_requests(tf: str, days: int | None = None, per_page: int = 1000) -> int:
    d = effective_days(tf, days or DEFAULT_HISTORY_DAYS)
    return -(-d * 1440 // TIMEFRAMES[tf][1] // per_page) or 1


def bar_floor(ts: pd.Timestamp, tf: str) -> pd.Timestamp:
    """Старт свечи, в которую попадает ts, по сетке Bybit (недельная — понедельник, а не эпоха-четверг)."""
    ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
    if tf == "1w":
        d = ts.normalize()
        return d - pd.Timedelta(days=d.weekday())
    return ts.floor(pd.Timedelta(minutes=TIMEFRAMES[tf][1]))


def last_closed_start(now: pd.Timestamp, tf: str, last: pd.Timestamp | None = None) -> pd.Timestamp:
    """Старт последней ЗАКРЫТОЙ свечи на момент now. Если известна реальная свеча `last`, сетка берётся от неё
    (last + k*шаг), поэтому логика верна для любого шага, в том числе недельного."""
    step = pd.Timedelta(minutes=TIMEFRAMES[tf][1])
    if last is not None:
        k = (now - last) // step
        return last + (k - 1) * step
    return bar_floor(now, tf) - step


def _atomic_parquet(df, path) -> None:
    """Запись через временный файл в той же папке + os.replace: обрыв не оставит битый кэш."""
    import os
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp)
    os.replace(tmp, path)


def _path(symbol: str, tf: str) -> Path:
    return DATA_DIR / f"{symbol}_{tf}.parquet"


def _fetch_range(symbol: str, tf: str, start_ms: int, end_ms: int, category: str = "linear") -> pd.DataFrame:
    interval, minutes = TIMEFRAMES[tf]
    rows: list[list[str]] = []
    end = end_ms
    while end > start_ms:
        for attempt in range(5):
            try:
                r = requests.get(
                    URL,
                    params={"category": category, "symbol": symbol, "interval": interval,
                            "start": start_ms, "end": end, "limit": 1000},
                    timeout=20,
                )
                r.raise_for_status()
                js = r.json()
                if js["retCode"] != 0:
                    raise RuntimeError(js["retMsg"])
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(1.5 * (attempt + 1))
        chunk = js["result"]["list"]  # новые -> старые
        if not chunk:
            break
        rows.extend(chunk)
        oldest = int(chunk[-1][0])
        if oldest <= start_ms:
            break
        end = oldest - 1
        time.sleep(0.05)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df = df.set_index("ts").astype(float).sort_index()
    return df[~df.index.duplicated()]


def load(symbol: str = "BTCUSDT", tf: str = "1h", days: int = 1095, refresh: bool = True) -> pd.DataFrame:
    """Возвращает OHLCV за последние `days` дней (не больше HISTORY_DAYS[tf]); докачивает недостающее и кэширует."""
    days = effective_days(tf, days)
    DATA_DIR.mkdir(exist_ok=True)
    p = _path(symbol, tf)
    minutes = TIMEFRAMES[tf][1]
    now_ms = int(time.time() * 1000)
    want_start = now_ms - days * 86_400_000

    cached = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    parts = [cached] if len(cached) else []

    if len(cached) == 0 or cached.index[0].value // 1_000_000 > want_start + minutes * 60_000 * 2:
        parts.append(_fetch_range(symbol, tf, want_start,
                                  (cached.index[0].value // 1_000_000 - 1) if len(cached) else now_ms))
    if refresh and len(cached):
        parts.append(_fetch_range(symbol, tf, cached.index[-1].value // 1_000_000, now_ms))

    df = pd.concat([x for x in parts if len(x)]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    # последняя свеча может быть незакрытой -> отбрасываем
    if len(df) and df.index[-1].value // 1_000_000 + minutes * 60_000 > now_ms:
        df = df.iloc[:-1]
    _atomic_parquet(df, p)
    cutoff = pd.Timestamp(want_start, unit="ms", tz="UTC")
    return df[df.index >= cutoff]


if __name__ == "__main__":
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    for tf in ("15m", "1h", "4h"):
        d = load(sym, tf)
        print(sym, tf, len(d), d.index[0], "->", d.index[-1])
