"""Ввод-вывод статистики: bot.db (read-only), equity-массивы, локальные свечи/фандинг."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np

from tradebot.web.stats_base import SYMBOL, TF, iso, parse_ts
from tradebot.web.stats_market import Market, market_from_df


# ---------------------------------------------------------------- ввод-вывод
def _db_uri(path: Path) -> str:
    return f"file:{Path(path).as_posix()}?mode=ro"


def fingerprint(db: sqlite3.Connection, path: Path) -> tuple:
    """Дёшево: последние id таблиц + mtime файла БД и WAL (WAL меняется чаще основного файла)."""
    def one(sql):
        r = db.execute(sql).fetchone()
        return r[0] if r else None
    mt = []
    for suffix in ("", "-wal"):
        try:
            mt.append(Path(str(path) + suffix).stat().st_mtime_ns)
        except OSError:
            mt.append(None)
    try:
        rt = db.execute("SELECT COUNT(*), TOTAL(COALESCE(pnl,0)), TOTAL(COALESCE(approx,0)) FROM roundtrips").fetchone()
    except sqlite3.Error:                                  # старая БД без таблицы
        rt = None
    return (one("SELECT MAX(id) FROM trades"), one("SELECT MAX(rowid) FROM equity"), one("SELECT MAX(id) FROM events"), rt, *mt)


def read_db(db: sqlite3.Connection, q_start: float | None) -> dict:
    """Читает всё нужное для compute_stats. equity — WHERE ts >= q_start (ISO-строки сортируются как время) + одна точка до него."""
    fills = [dict(zip(("id", "ts", "side", "qty", "price", "reason", "order_id"), r))
             for r in db.execute("SELECT id,ts,side,qty,price,reason,order_id FROM trades ORDER BY id")]
    try:                                                   # итоги закрытых сделок по данным биржи (пишет бот); таблицы может не быть
        rtrips = [dict(zip(("ts_open", "ts_close", "side", "qty", "pnl", "fees", "funding", "approx", "order_id"), r)) for r in db.execute(
            "SELECT ts_open,ts_close,side,qty,pnl,fees,funding,approx,order_id FROM roundtrips ORDER BY id")]
    except sqlite3.Error:
        rtrips = []
    events = [dict(zip(("ts", "level", "text"), r)) for r in db.execute(
        "SELECT ts,level,text FROM events WHERE text LIKE '%озиция исчезла%' OR text LIKE '%Бот запущен%' ORDER BY id")]
    if q_start is None:
        rows = db.execute("SELECT ts,v FROM equity ORDER BY rowid").fetchall()
    else:
        q = iso(q_start)
        rows = db.execute("SELECT ts,v FROM equity WHERE ts >= ? ORDER BY rowid", (q,)).fetchall()
        b = db.execute("SELECT ts,v FROM equity WHERE ts < ? ORDER BY rowid DESC LIMIT 1", (q,)).fetchone()
        if b:
            rows.insert(0, b)
    first = db.execute("SELECT ts FROM equity ORDER BY rowid LIMIT 1").fetchone()
    t, v = _eq_arrays(rows)
    kv = {}
    for k in ("position", "env", "heartbeat", "s_leverage", "s_stop_pct"):
        r = db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        try:
            kv[k] = json.loads(r[0]) if r else None
        except ValueError:
            kv[k] = None
    return {"fills": fills, "events": events, "eq_t": t, "eq_v": v, "eq_first_ts": parse_ts(first[0]) if first else None, "kv": kv,
            "roundtrips": rtrips}


def _eq_arrays(rows) -> tuple[np.ndarray, np.ndarray]:
    if not rows:
        return np.zeros(0, "int64"), np.zeros(0)
    try:   # быстрый путь: формат БД 'YYYY-MM-DDTHH:MM:SS+00:00' (UTC)
        t = np.array([str(r[0])[:19] for r in rows], dtype="datetime64[s]").astype("int64")
    except ValueError:
        t = np.array([parse_ts(r[0]) or 0 for r in rows], dtype="int64")
    v = np.array([np.nan if r[1] is None else r[1] for r in rows], dtype="float64")
    order = np.argsort(t, kind="stable")
    return t[order], v[order]


def load_market() -> tuple[Market | None, str | None]:
    """Часовые свечи + фандинг из локального кэша (без сети). Любая проблема -> (None, текст)."""
    from tradebot.web._facade import F
    try:
        df = F.get_df(SYMBOL, TF)
        last = F._last_ts(F.MARKET / f"{SYMBOL}_funding.parquet")
        fu = datetime.fromisoformat(last).timestamp() if last else None
        return market_from_df(df, fu), None
    except Exception as e:  # noqa: BLE001  DataMissing/DataEmpty/любой сбой чтения — статистика не падает
        return None, type(e).__name__


def _market_sig() -> tuple:
    from tradebot.web._facade import F
    try:
        return tuple(p.stat().st_mtime_ns if p.exists() else None for p in F._files(SYMBOL, TF))
    except Exception:  # noqa: BLE001
        return ()
