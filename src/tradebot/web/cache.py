"""Дисковый кэш результатов (рейтинг стратегий, подбор параметров): sqlite (WAL), ключ = версия + хэш кода + настройки
+ отпечаток данных. Любая ошибка кэша не должна ломать расчёт: чтение при проблеме = промах, запись при проблеме
молча пропускается. Повреждённый файл БД откладывается в сторону и создаётся заново."""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from tradebot.core import optimize
from tradebot.paths import RESULTS

log = logging.getLogger("tradebot.web.cache")

DB_PATH = RESULTS / "cache" / "web_cache.sqlite3"
MAX_ENTRIES = 2000
MAX_BYTES = 200 * 1024 * 1024
# Файлы (относительно пакета tradebot), от которых зависит содержимое сохранённых результатов: движок/оптимизатор/стратегии/индикаторы,
# нормализация рынка и подготовка данных (деривативы), упаковка результата и схемы запросов. Правка любого — новый ключ кэша.
_CODE_FILES = (
    "core/optimize.py", "core/engine.py", "core/strategies.py", "core/overlays.py", "core/indicators.py", "core/portfolio.py",
    "market/klines.py", "market/derivs.py",
    "web/backtests.py", "web/research.py", "web/meta.py", "web/data.py", "web/serialize.py", "web/schemas.py",
)
_lock = threading.Lock()


@functools.lru_cache(maxsize=1)
def code_hash() -> str:
    """Хэш исходников, влияющих на результаты (_CODE_FILES): правка кода автоматически инвалидирует кэш. Окончания строк
    нормализованы (CRLF == LF): смена autocrlf кэш не сбрасывает."""
    base = Path(optimize.__file__).parent.parent
    h = hashlib.sha256(str(optimize.CACHE_VERSION).encode())
    for name in _CODE_FILES:
        h.update(name.encode())
        try:
            h.update((base / name).read_bytes().replace(b"\r\n", b"\n"))
        except OSError:
            h.update(b"?")
    return h.hexdigest()[:16]


def make_key(kind: str, **parts) -> str:
    payload = json.dumps({"v": optimize.CACHE_VERSION, "code": code_hash(), "kind": kind, **parts},
                         sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("""CREATE TABLE IF NOT EXISTS items (
            key TEXT PRIMARY KEY, kind TEXT, symbol TEXT, tf TEXT, strategy TEXT, data_sig TEXT,
            created REAL, size INTEGER, payload TEXT)""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_sym_tf ON items(symbol, tf)")
    except Exception:
        con.close()          # иначе Windows не даст переименовать повреждённый файл
        raise
    return con


def _quarantine() -> None:
    """БД повреждена — переименовываем, чтобы не мешала; кэш просто начнётся заново."""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        try:
            if p.exists():
                os.replace(p, p.with_name(p.name + ".corrupt"))
        except OSError:
            pass


_CORRUPT_CODES = (getattr(sqlite3, "SQLITE_CORRUPT", 11), getattr(sqlite3, "SQLITE_NOTADB", 26))


def _is_corrupt(e: sqlite3.DatabaseError) -> bool:
    """Файл БД действительно испорчен (malformed / not a database), а не просто занят или нет места: только тогда его откладывают
    в сторону. Занятая БД (locked/busy), полный диск, ошибки ввода-вывода — временные, кэш при них НЕ теряем."""
    code = getattr(e, "sqlite_errorcode", None)
    return code is not None and (code & 0xFF) in _CORRUPT_CODES


def _run(fn, default=None):
    with _lock:
        for attempt in (0, 1):
            try:
                con = _connect()
                try:
                    return fn(con)
                finally:
                    con.close()
            except sqlite3.DatabaseError as e:      # «not a database», malformed — сброс; locked/full/IO — просто промах
                if not _is_corrupt(e):
                    log.warning("cache DB error (%s) — пропуск без сброса", e)
                    return default
                log.warning("cache DB corrupt (%s) — reset", e)
                _quarantine()
            except Exception as e:  # noqa: BLE001
                log.warning("cache error: %s", e)
                return default
    return default


def get_many(keys: list[str]) -> dict[str, dict]:
    """Только чтение с диска. Битый JSON = промах (и строка удаляется)."""
    if not keys:
        return {}

    def go(con):
        out, bad = {}, []
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            rows = con.execute(f"SELECT key, payload FROM items WHERE key IN ({','.join('?' * len(chunk))})", chunk).fetchall()
            for k, payload in rows:
                try:
                    out[k] = json.loads(payload)
                except (TypeError, ValueError):
                    bad.append(k)
        if bad:
            con.executemany("DELETE FROM items WHERE key=?", [(k,) for k in bad])
            con.commit()
        return out
    return _run(go, {}) or {}


def get(key: str) -> dict | None:
    return get_many([key]).get(key)


def put(key: str, payload: dict, *, kind: str, symbol: str, tf: str, strategy: str, data_sig: str) -> None:
    def go(con):
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)   # внутри защищённой части: сбой записи кэша не валит запрос
        con.execute("INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?,?,?,?)",
                    (key, kind, symbol, tf, strategy, data_sig, time.time(), len(text), text))
        n, total = con.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM items").fetchone()
        if n > MAX_ENTRIES or total > MAX_BYTES:       # вытесняем самые старые
            rows = con.execute("SELECT key, size FROM items ORDER BY created ASC").fetchall()
            drop = []
            for k, sz in rows:
                if n <= MAX_ENTRIES and total <= MAX_BYTES:
                    break
                drop.append((k,))
                n -= 1
                total -= sz
            con.executemany("DELETE FROM items WHERE key=?", drop)
        con.commit()
    _run(go)


def purge_stale(symbol: str, current_sig: dict[str, str]) -> int:
    """Удаляет записи символа, чья сигнатура данных (по таймфрейму) не совпадает с текущей. current_sig: tf -> sig."""
    def go(con):
        n = 0
        for (tf,) in con.execute("SELECT DISTINCT tf FROM items WHERE symbol=?", (symbol,)).fetchall():
            cur = con.execute("DELETE FROM items WHERE symbol=? AND tf=? AND data_sig<>?", (symbol, tf, current_sig.get(tf, "")))
            n += cur.rowcount
        con.commit()
        return n
    return _run(go, 0) or 0


def stats() -> dict:
    return _run(lambda c: dict(zip(("entries", "bytes"), c.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM items").fetchone())), {}) or {}
