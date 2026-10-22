"""Загрузка свечей с публичного API Bybit v5 (ключи не нужны) + кэш в parquet."""
from __future__ import annotations

import contextlib
import logging
import os
import re
import time
import uuid
from pathlib import Path

import pandas as pd
import requests

URL = "https://api.bybit.com/v5/market/kline"
from tradebot.i18n import tr
from tradebot.market import guard
from tradebot.paths import MARKET as DATA_DIR

log = logging.getLogger("tradebot.market")

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


class NoDataError(RuntimeError):
    """Биржа не вернула ни одной свечи (и в локальном кэше их нет): считать нечего."""


TMP_MAX_AGE_S = 3600          # осиротевшие временные файлы старше часа (обрыв процесса посреди записи) удаляются при следующей записи


def _replace(tmp: Path, path: Path) -> None:
    """os.replace с повтором: на Windows замена не удаётся, пока читатель держит файл открытым (PermissionError)."""
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _sweep_tmp(path: Path) -> None:
    now = time.time()
    for pat in (path.name + ".*.tmp", path.name + ".tmp"):
        for t in path.parent.glob(pat):
            with contextlib.suppress(OSError):
                if now - t.stat().st_mtime > TMP_MAX_AGE_S:
                    t.unlink()


def _atomic_parquet(df, path) -> None:
    """Запись через временный файл в той же папке + os.replace: обрыв не оставит битый кэш. Имя временного файла уникально
    (pid + uuid): два параллельных писателя (бот и веб-докачка) не затирают друг другу временный файл."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        df.to_parquet(tmp)
        _replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    _sweep_tmp(path)


def _read_parquet(path) -> pd.DataFrame:
    """pd.read_parquet с коротким повтором: во время os.replace чужим писателем файл на Windows может на миг не открываться."""
    for attempt in range(5):
        try:
            return pd.read_parquet(path)
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


_SYMBOL_RE = re.compile(r"[A-Z0-9]{2,20}")


def check_symbol(symbol: str) -> str:
    """Символ обязан быть тикером (A-Z, 0-9): имя попадает в путь файла, обратный слэш/«..»/UNC-путь сюда пройти не должны."""
    if not isinstance(symbol, str) or not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("неизвестный символ или таймфрейм")
    return symbol


def _path(symbol: str, tf: str) -> Path:
    if tf not in TIMEFRAMES:
        raise ValueError("неизвестный символ или таймфрейм")
    return DATA_DIR / f"{check_symbol(symbol)}_{tf}.parquet"


# ---------------------------------------------------------------- пропуски в данных (только журнал: числа не меняются)
_gap_seen: dict[str, tuple] = {}


def find_gaps(index: pd.DatetimeIndex, minutes: float) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    """Пропуски сетки: [(последняя метка ДО пропуска, первая метка ПОСЛЕ, сколько баров отсутствует)]. Индекс отсортирован."""
    if len(index) < 2:
        return []
    step = pd.Timedelta(minutes=minutes)
    d = index[1:] - index[:-1]
    pos = (d > step).nonzero()[0]
    return [(index[i], index[i + 1], int(d[i] // step) - 1) for i in pos]


def warn_gaps(label: str, gaps: list) -> None:
    """Одно предупреждение на каждое новое состояние пропусков (бот зовёт load раз в час — не спамим одним и тем же)."""
    if not gaps:
        _gap_seen.pop(label, None)
        return
    sig = (len(gaps), gaps[0][0], gaps[-1][1], sum(g[2] for g in gaps))
    if _gap_seen.get(label) == sig:
        return
    _gap_seen[label] = sig
    log.warning(tr("%s: gaps in the data: %d, %d bars missing; first after %s, last before %s"),
                label, len(gaps), sig[3], gaps[0][0].isoformat(), gaps[-1][1].isoformat())


def _fetch_range(symbol: str, tf: str, start_ms: int, end_ms: int, category: str = "linear") -> pd.DataFrame:
    interval = TIMEFRAMES[tf][0]
    rows: list[list[str]] = []
    end = end_ms
    while end > start_ms:
        guard.check_cancel()
        for attempt in range(5):
            guard.check_cancel()
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
                guard.sleep(1.5 * (attempt + 1))
        chunk = js["result"]["list"]  # новые -> старые
        if not chunk:
            break
        rows.extend(chunk)
        oldest = int(chunk[-1][0])
        if oldest <= start_ms:
            break
        end = oldest - 1
        guard.sleep(0.05)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df = df.set_index("ts").astype(float).sort_index()
    return df[~df.index.duplicated()]


def load(symbol: str = "BTCUSDT", tf: str = "1h", days: int = 1095, refresh: bool = True) -> pd.DataFrame:
    """Возвращает OHLCV за последние `days` дней (не больше HISTORY_DAYS[tf]); докачивает недостающее и кэширует.
    Файл защищён блокировкой по пути: если его дольше guard.LOCK_TIMEOUT_S занимает другое обновление (например, долгая
    веб-докачка), вызов не ждёт, а отдаёт то, что уже лежит на диске (без докачки)."""
    days = effective_days(tf, days)
    DATA_DIR.mkdir(exist_ok=True)
    p = _path(symbol, tf)
    minutes = TIMEFRAMES[tf][1]
    with guard.locked(p) as got:
        now_ms = int(time.time() * 1000)
        want_start = now_ms - days * 86_400_000
        cutoff = pd.Timestamp(want_start, unit="ms", tz="UTC")
        if not got:
            if not p.exists():
                raise guard.LockTimeout(f"{p.name} занят другим обновлением, локальной копии нет")
            log.warning(tr("klines %s %s: the file is busy with another update — using the existing data without a top-up"), symbol, tf)
            old = _read_parquet(p)
            return old[old.index >= cutoff]

        cached = _read_parquet(p) if p.exists() else pd.DataFrame()
        parts = [cached] if len(cached) else []

        if len(cached) == 0 or cached.index[0].value // 1_000_000 > want_start + minutes * 60_000 * 2:
            parts.append(_fetch_range(symbol, tf, want_start,
                                      (cached.index[0].value // 1_000_000 - 1) if len(cached) else now_ms))
        if refresh and len(cached):
            parts.append(_fetch_range(symbol, tf, cached.index[-1].value // 1_000_000, now_ms))

        parts = [x for x in parts if len(x)]
        if not parts:
            raise NoDataError(f"Bybit не вернул свечи {symbol} {tf}, локального кэша нет")
        df = pd.concat(parts).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        # последняя свеча может быть незакрытой -> отбрасываем
        if len(df) and df.index[-1].value // 1_000_000 + minutes * 60_000 > now_ms:
            df = df.iloc[:-1]
        win = df[df.index >= cutoff]
        warn_gaps(f"klines {symbol} {tf}", find_gaps(win.index, minutes))
        _atomic_parquet(df, p)
        return win


if __name__ == "__main__":
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    for tf in ("15m", "1h", "4h"):
        d = load(sym, tf)
        print(sym, tf, len(d), d.index[0], "->", d.index[-1])
