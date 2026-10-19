"""Аудит торгового ядра: Store (T10 индексы/парные записи/копия БД/чистка, T11 seed номера ордера, T6 monitor_ok_ts)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tradebot.trading import state
from tradebot.trading.state import Store, read_snapshot

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


@pytest.fixture
def st(tmp_path):
    s = Store(tmp_path / "bot.db")
    yield s
    s.close()


# ---------- T10: индексы ----------
def test_indexes_created_and_idempotent(tmp_path):
    p = tmp_path / "bot.db"
    Store(p).close()
    Store(p).close()                                   # повторное открытие: CREATE INDEX IF NOT EXISTS не падает
    db = sqlite3.connect(p)
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_equity_ts", "idx_signals_key_id", "idx_signals_ts", "idx_events_ts"} <= names
    plan = " ".join(str(r) for r in db.execute("EXPLAIN QUERY PLAN SELECT v FROM equity WHERE ts<=? ORDER BY ts DESC LIMIT 1", ("x",)))
    assert "idx_equity_ts" in plan                      # equity_before идёт по индексу, а не сканом всей кривой
    db.close()


# ---------- T10: парные записи одной транзакцией ----------
def test_set_acted_pair_is_atomic(st):
    st.set_acted(0.5, 0.01)
    assert (st.get("acted_exposure"), st.get("acted_qty")) == (0.5, 0.01)
    with pytest.raises(TypeError):
        st.set_acted(0.7, object())                     # вторая запись падает -> первая откатывается
    assert (st.get("acted_exposure"), st.get("acted_qty")) == (0.5, 0.01)
    st.clear_acted()
    assert st.get("acted_exposure") is None and st.get("acted_qty") is None


def test_set_acted_with_latch_and_set_latch(st):
    st.set_acted(None, None, latch=0.5)
    assert st.get("latch") is True and st.get("latch_exposure") == 0.5 and st.get("acted_qty") is None
    st.set("latch", False)
    st.set_latch(-0.3)
    assert st.get("latch") is True and st.get("latch_exposure") == -0.3


def test_mark_killed_is_one_transaction(st):
    st.set_setting("enabled", True)
    st.mark_killed("просадка")
    assert st.get("killed") is True and st.get("kill_reason") == "просадка" and st.settings()["enabled"] is False
    st.set_setting("enabled", True)
    st.set("killed", False)
    with pytest.raises(TypeError):
        st.mark_killed(object())                        # сбой на причине: enabled не должен остаться выключенным без killed
    assert st.settings()["enabled"] is True and st.get("killed") is False


# ---------- T11: seed номера ордера ----------
def test_seed_order_seq_never_goes_back(st):
    st.set("order_seq", 5)
    assert st.seed_order_seq(now=1000) == 1000          # время — нижняя граница
    assert st.next_seq() == 1001
    for i in range(3):
        st.add_trade("buy", 0.01, 100.0, "r", f"o{i}")
    st.set("order_seq", 1)                              # «старая/восстановленная» БД с устаревшим счётчиком
    assert st.seed_order_seq(now=2) == 3                # max(1, max(trades.id)=3, 2)
    st.set("order_seq", 50_000)
    assert st.seed_order_seq(now=10) == 50_000          # счётчик больше — не трогаем
    assert st.next_seq() == 50_001


def test_seed_order_seq_default_uses_wallclock(st):
    import time
    n = st.seed_order_seq()
    assert abs(n - int(time.time())) < 5


# ---------- T10: копия БД ----------
def _fill(st):
    st.set("equity", 1234.5)
    st.add_trade("buy", 0.01, 100.0, "r", "o1")
    st.add_event("info", "x")


def test_backup_roundtrip_and_due(st, tmp_path):
    _fill(st)
    d = tmp_path / "bk"
    assert st.backup_due(d, NOW)
    dest = st.backup(d, NOW)
    assert dest.name == "bot_20261020.db" and not st.backup_due(d, NOW)
    assert not list(d.glob("*.tmp"))
    db = sqlite3.connect(dest)
    assert db.execute("SELECT v FROM kv WHERE k='equity'").fetchone()[0] == "1234.5"
    assert db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    db.close()


def test_backup_keeps_last_14(st, tmp_path):
    d = tmp_path / "bk"
    for i in range(20):
        st.backup(d, NOW - timedelta(days=19 - i))
    names = sorted(p.name for p in d.glob("bot_*.db"))
    assert len(names) == 14 and names[-1] == "bot_20261020.db" and names[0] == "bot_20261007.db"


def test_backup_failure_leaves_no_partial_file(st, tmp_path, monkeypatch):
    d = tmp_path / "bk"

    class Boom(sqlite3.Connection):
        def backup(self, *a, **k):
            raise sqlite3.OperationalError("disk full")
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real(*a, **{**k, "factory": Boom}))
    with pytest.raises(sqlite3.OperationalError):
        st.backup(d, NOW)
    assert not list(d.glob("*"))                        # ни bot_*.db, ни .tmp


# ---------- T10: чистка ----------
def _seed_old(st):
    with st._lock:
        for key in ("A", "B"):
            for days in (200, 100, 95, 10, 1):
                st._db.execute("INSERT INTO signals(ts,bar,key,signal,target) VALUES(?,?,?,?,?)", (iso(days), "b", key, 1.0, 0.5))
        # ключ C: все строки старые — последняя должна остаться
        for days in (300, 250):
            st._db.execute("INSERT INTO signals(ts,bar,key,signal,target) VALUES(?,?,?,?,?)", (iso(days), "b", "C", -1.0, 0.0))
        for days in (500, 401, 399, 5):
            st._db.execute("INSERT INTO events(ts,level,text) VALUES(?,?,?)", (iso(days), "info", f"e{days}"))
        st._db.execute("INSERT INTO trades(ts,side,qty,price,reason,order_id) VALUES(?,?,?,?,?,?)", (iso(900), "buy", 0.01, 1.0, "r", "o"))
        st._db.execute("INSERT INTO equity(ts,v) VALUES(?,?)", (iso(900), 1.0))
        st._db.execute("INSERT INTO roundtrips(ts_open,ts_close) VALUES(?,?)", (iso(900), iso(899)))


def test_purge_signals_90d_keep_latest_per_key_events_400d(st):
    _seed_old(st)
    res = st.purge(NOW)
    assert res == {"signals": 7, "events": 2}           # A,B: 200/100/95 (по 3) -> 6, C: 300 -> 1; events: 500 и 401
    left = st._db.execute("SELECT key, COUNT(*) FROM signals GROUP BY key ORDER BY key").fetchall()
    assert left == [("A", 2), ("B", 2), ("C", 1)]        # C: осталась одна самая новая строка
    c = st._db.execute("SELECT ts FROM signals WHERE key='C'").fetchone()[0]
    assert c == iso(250)
    assert sorted(e[0] for e in st._db.execute("SELECT text FROM events")) == ["e399", "e5"]
    # trades / equity / roundtrips не трогаются, как бы стары ни были
    assert st.count("trades") == 1
    assert st._db.execute("SELECT COUNT(*) FROM equity").fetchone()[0] == 1
    assert st._db.execute("SELECT COUNT(*) FROM roundtrips").fetchone()[0] == 1
    assert set(st.latest_signals()) == {"A", "B", "C"}   # «прежний» сигнал каждого ключа цел


# ---------- T10: порядок копия -> чистка -> checkpoint ----------
def test_maintenance_order_backup_purge_checkpoint(st, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(st, "backup", lambda *a, **k: calls.append("backup") or tmp_path / "x")
    monkeypatch.setattr(st, "purge", lambda *a, **k: calls.append("purge") or {})
    monkeypatch.setattr(st, "checkpoint", lambda: calls.append("checkpoint"))
    out = st.maintenance(tmp_path / "bk", NOW)
    assert calls == ["backup", "purge", "checkpoint"] and out["ran"] and "error" not in out


def test_maintenance_failed_backup_skips_purge(st, tmp_path, monkeypatch):
    calls = []

    def bad(*a, **k):
        calls.append("backup")
        raise OSError("нет места")
    monkeypatch.setattr(st, "backup", bad)
    monkeypatch.setattr(st, "purge", lambda *a, **k: calls.append("purge") or {})
    monkeypatch.setattr(st, "checkpoint", lambda: calls.append("checkpoint"))
    out = st.maintenance(tmp_path / "bk", NOW)
    assert calls == ["backup", "checkpoint"] and "нет места" in out["error"]      # данные не удалены без копии


def test_maintenance_skips_when_todays_backup_exists(st, tmp_path):
    d = tmp_path / "bk"
    first = st.maintenance(d, NOW)
    assert first["ran"] and first["purged"] == {"signals": 0, "events": 0}
    assert st.maintenance(d, NOW) == {"ran": False}      # второй вызов в тот же день — ничего не делает
    assert st.maintenance(d, NOW + timedelta(days=1))["ran"]


def test_checkpoint_truncates_wal(tmp_path):
    p = tmp_path / "bot.db"
    s = Store(p)
    for i in range(300):
        s.add_equity(float(i))
    s.checkpoint()
    wal = tmp_path / "bot.db-wal"
    assert not wal.exists() or wal.stat().st_size == 0
    s.close()


# ---------- T6: monitor_ok_ts в снимке НЕ выставляется ----------
def test_snapshot_does_not_expose_monitor_ok_ts_until_web_contract_declares_it(tmp_path):
    """tests/test_contract.py требует, чтобы каждое поле /api/live было объявлено в web types.ts (LiveSnapshot). Пока поля
    monitor_ok_ts там нет, в снимок оно не попадает (сторож читает его прямо из bot.db). Появится в types.ts — добавить в
    read_snapshot и поменять этот тест."""
    p = tmp_path / "s.db"
    s = Store(p)
    s.set("monitor_ok_ts", "2026-10-20T12:00:00+00:00")
    assert "monitor_ok_ts" not in read_snapshot(p) and "heartbeat" in read_snapshot(p)
    s.close()
