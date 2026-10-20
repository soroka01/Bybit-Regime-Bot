"""Миграции БД бота (PRAGMA user_version): новая БД сразу в v1; старая (v0, в том числе восстановленная из копии) мигрирует при
открытии Store - с предварительной копией bot_pre_v1.db, одной транзакцией и идемпотентно; сбой копии или самой миграции данных
не теряет и бота не останавливает. Только tmp-БД: storage/state/bot.db не затрагивается."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from tradebot.trading import state
from tradebot.trading.state import LEGACY_KV_V1, PRE_V1_BACKUP, SCHEMA, Store

LIVE_KV = {"s_enabled": True, "s_leverage": 2.0, "order_seq": 77, "acted_exposure": 0.5, "position": {"side": "long", "qty": 0.01},
           "pending_stopouts": [{"rt_id": 1, "since_ms": 5}], "peak_equity": 160.0}
LEGACY_KV = {"shadow": {"x": 1}, "s_allocated_capital": 160.0, "paper": False, "paper_plan": {"side": None}, "env": "mainnet"}


def make_v0(path, legacy=True, data=True):
    """БД прежней версии бота: схема без user_version, таблица shadow, устаревшие ключи kv и немного живых данных."""
    db = sqlite3.connect(str(path), isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    if legacy:
        db.execute("CREATE TABLE shadow(id INTEGER PRIMARY KEY, ts TEXT, data TEXT)")
        db.execute("INSERT INTO shadow(ts, data) VALUES('2026-10-01T00:00:00+00:00', 'теневая строка')")
    if data:
        for k, v in {**LIVE_KV, **(LEGACY_KV if legacy else {})}.items():
            db.execute("INSERT INTO kv(k, v) VALUES(?, ?)", (k, json.dumps(v)))
        db.execute("INSERT INTO trades(ts, side, qty, price, reason, order_id) VALUES('2026-10-01T00:00:00+00:00', 'buy', 0.01, 100000, 'бар', 'o1')")
        db.execute("INSERT INTO events(ts, level, text) VALUES('2026-10-01T00:00:00+00:00', 'info', 'Бот запущен')")
    db.close()


def version(path):
    db = sqlite3.connect(str(path))
    try:
        return db.execute("PRAGMA user_version").fetchone()[0]
    finally:
        db.close()


def tables(path):
    db = sqlite3.connect(str(path))
    try:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()


def kv_of(path):
    db = sqlite3.connect(str(path))
    try:
        return {k: json.loads(v) for k, v in db.execute("SELECT k, v FROM kv")}
    finally:
        db.close()


# ---------------------------------------------------------------------------------------------------- новая БД
def test_fresh_db_is_created_at_current_version_without_backup(tmp_path):
    p = tmp_path / "new.db"
    st = Store(p)
    st.set("x", 1)
    st.close()
    assert version(p) == state.SCHEMA_VERSION == 1 and "shadow" not in tables(p) and st.migration_error is None
    assert not (tmp_path / "backups").exists()                          # копировать нечего
    assert "shadow" not in SCHEMA


# ---------------------------------------------------------------------------------------------------- миграция v0 -> v1
def test_old_db_is_migrated_with_pre_migration_copy(tmp_path):
    p = tmp_path / "bot.db"
    make_v0(p)
    assert version(p) == 0 and "shadow" in tables(p)
    st = Store(p)
    st.close()
    assert st.migration_error is None and version(p) == 1
    assert "shadow" not in tables(p)
    kv = kv_of(p)
    assert not set(LEGACY_KV_V1) & set(kv)                              # устаревшие ключи стёрты
    assert kv == LIVE_KV                                                # живые - до байта, в том числе pending_stopouts
    db = sqlite3.connect(str(p))
    assert db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1 and db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    db.close()
    # копия: ровно это имя, прежнее содержимое (со всеми устаревшими объектами), версия 0
    bak = tmp_path / "backups" / "bot_pre_v1.db"
    assert PRE_V1_BACKUP == "bot_pre_v1.db" and bak.exists()
    assert version(bak) == 0 and "shadow" in tables(bak) and kv_of(bak) == {**LIVE_KV, **LEGACY_KV}
    assert not list((tmp_path / "backups").glob("*.tmp"))


def test_legacy_key_list_matches_what_nothing_reads_any_more():
    assert set(LEGACY_KV_V1) == {"shadow", "s_allocated_capital", "paper", "paper_plan", "env"}
    assert "pending_stopouts" not in LEGACY_KV_V1 and "pending_stopout" not in LEGACY_KV_V1      # живой ключ (мн. число) не трогаем


def test_pre_migration_copy_is_not_a_daily_backup_and_survives_retention(tmp_path):
    """bot_pre_v1.db не должна выглядеть как bot_YYYYMMDD.db: иначе backup_due() решил бы, что копия за сегодня есть, а чистка
    старых копий (оставляет последние `keep`) удалила бы страховку."""
    p = tmp_path / "bot.db"
    make_v0(p)
    bdir = tmp_path / "backups"
    st = Store(p)
    assert (bdir / "bot_pre_v1.db").exists()
    assert st.backup_due(bdir)                                          # дневной копии ещё нет, несмотря на pre_v1
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    for d in range(1, 20):
        (bdir / f"bot_202610{d:02d}.db").write_bytes(b"x")
    st.backup(bdir, now, keep=3)
    names = sorted(f.name for f in bdir.iterdir())
    assert "bot_pre_v1.db" in names and len([n for n in names if n != "bot_pre_v1.db"]) == 3
    assert not st.backup_due(bdir, now)
    st.close()


def test_migration_is_idempotent_and_does_not_copy_again(tmp_path):
    p = tmp_path / "bot.db"
    make_v0(p)
    Store(p).close()
    bak = tmp_path / "backups" / "bot_pre_v1.db"
    stamp = bak.stat().st_mtime_ns
    before = kv_of(p)
    Store(p).close()
    Store(p).close()
    assert version(p) == 1 and kv_of(p) == before and bak.stat().st_mtime_ns == stamp
    assert [f.name for f in (tmp_path / "backups").iterdir()] == ["bot_pre_v1.db"]


def test_restored_old_backup_remigrates_harmlessly_and_keeps_first_safety_copy(tmp_path):
    p = tmp_path / "bot.db"
    make_v0(p)
    Store(p).close()
    first = (tmp_path / "backups" / "bot_pre_v1.db").read_bytes()
    # «восстановили» старую копию (версия 0, снова с устаревшими объектами)
    for ext in ("", "-wal", "-shm"):
        (tmp_path / f"bot.db{ext}").unlink(missing_ok=True)
    make_v0(p)
    st = Store(p)
    st.close()
    assert version(p) == 1 and "shadow" not in tables(p) and kv_of(p) == LIVE_KV
    assert (tmp_path / "backups" / "bot_pre_v1.db").read_bytes() == first          # первая страховка не затёрта
    extra = [f.name for f in (tmp_path / "backups").iterdir() if f.name != "bot_pre_v1.db"]
    assert len(extra) == 1 and extra[0].startswith("bot_pre_v1_") and extra[0].endswith(".db")
    assert not [n for n in extra if len(n) == len("bot_YYYYMMDD.db")]             # и на дневную копию не похожа


def test_old_db_without_data_is_migrated_without_copy(tmp_path):
    p = tmp_path / "bot.db"
    make_v0(p, legacy=False, data=False)
    Store(p).close()
    assert version(p) == 1 and not (tmp_path / "backups").exists()


def test_old_db_with_only_current_schema_and_data_still_gets_copy(tmp_path):
    p = tmp_path / "bot.db"
    make_v0(p, legacy=False)
    st = Store(p)
    st.close()
    assert version(p) == 1 and (tmp_path / "backups" / "bot_pre_v1.db").exists() and kv_of(p) == LIVE_KV


def test_old_db_missing_newer_tables_gets_them_additively(tmp_path):
    p = tmp_path / "bot.db"
    db = sqlite3.connect(str(p))
    db.executescript("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT NOT NULL); CREATE TABLE trades(id INTEGER PRIMARY KEY, ts TEXT, side TEXT, "
                     "qty REAL, price REAL, reason TEXT, order_id TEXT); INSERT INTO kv VALUES('env', '\"mainnet\"'), ('s_leverage', '2.0');")
    db.close()
    st = Store(p)
    assert {"proposals", "roundtrips", "events", "signals", "equity"} <= tables(p) and "shadow" not in tables(p)
    assert st.settings()["leverage"] == 2.0 and st.get("env") is None
    st.close()


# ---------------------------------------------------------------------------------------------------- сбои: данные не теряем
def test_failing_backup_skips_migration_and_keeps_bot_running_on_version_0(tmp_path):
    p = tmp_path / "bot.db"
    make_v0(p)
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("файл на месте каталога копий", encoding="utf-8")
    st = Store(p, backup_dir=blocker / "db")                            # создать каталог копий нельзя
    assert st.migration_error and "копия перед миграцией не сделана" in st.migration_error
    assert version(p) == 0 and "shadow" in tables(p)                    # ничего не тронуто
    assert kv_of(p) == {**LIVE_KV, **LEGACY_KV}
    st.set("order_seq", 78)                                            # бот работает как раньше
    assert st.get("order_seq") == 78 and st.settings()["leverage"] == 2.0
    st.close()
    # следующий запуск с рабочим каталогом копий - миграция проходит
    st2 = Store(p, backup_dir=tmp_path / "ok")
    st2.close()
    assert st2.migration_error is None and version(p) == 1 and (tmp_path / "ok" / "bot_pre_v1.db").exists()
    assert kv_of(p) == {**LIVE_KV, "order_seq": 78}


def test_failing_copy_quick_check_also_skips_migration(tmp_path, monkeypatch):
    p = tmp_path / "bot.db"
    make_v0(p)

    def broken(self, dest):
        raise sqlite3.DatabaseError("quick_check копии не прошёл")
    monkeypatch.setattr(Store, "_snapshot_to", broken)
    st = Store(p)
    st.close()
    assert st.migration_error and version(p) == 0 and "shadow" in tables(p) and kv_of(p) == {**LIVE_KV, **LEGACY_KV}


def test_failing_transaction_rolls_back_everything(tmp_path):
    """Сбой посреди миграции (здесь - триггер, запрещающий удалять ключи): откатывается и DROP TABLE shadow, версия остаётся 0."""
    p = tmp_path / "bot.db"
    make_v0(p)
    db = sqlite3.connect(str(p))
    db.execute("CREATE TRIGGER no_del BEFORE DELETE ON kv BEGIN SELECT RAISE(ABORT, 'нельзя'); END")
    db.commit()
    db.close()
    st = Store(p)
    st.close()
    assert st.migration_error and "нельзя" in st.migration_error
    assert version(p) == 0 and "shadow" in tables(p) and kv_of(p) == {**LIVE_KV, **LEGACY_KV}
    assert (tmp_path / "backups" / "bot_pre_v1.db").exists()            # копия при этом уже есть
    db = sqlite3.connect(str(p))
    db.execute("DROP TRIGGER no_del")
    db.commit()
    db.close()
    Store(p).close()                                                    # следующий запуск доводит миграцию
    assert version(p) == 1 and "shadow" not in tables(p) and kv_of(p) == LIVE_KV


def test_readers_do_not_need_the_migration(tmp_path):
    """read_snapshot (веб) читает и БД версии 0, и мигрированную одинаково: устаревших ключей в ответе нет."""
    p = tmp_path / "bot.db"
    make_v0(p)
    old = state.read_snapshot(p)
    Store(p).close()
    new = state.read_snapshot(p)
    assert old == new and not {"env", "paper", "paper_plan"} & set(new)


def test_default_backup_dir_is_storage_backups_db_for_the_real_db_path(tmp_path, monkeypatch):
    real = tmp_path / "storage" / "state" / "bot.db"
    real.parent.mkdir(parents=True)
    monkeypatch.setattr(state, "DB_PATH", real)
    monkeypatch.setattr(state, "BACKUP_DIR", tmp_path / "storage" / "backups" / "db")
    make_v0(real)
    Store(real).close()
    assert (tmp_path / "storage" / "backups" / "db" / "bot_pre_v1.db").exists()


@pytest.mark.parametrize("n", [0, 1])
def test_version_pragma_is_readable_by_readonly_connections(tmp_path, n):
    p = tmp_path / "bot.db"
    if n == 0:
        make_v0(p)
    Store(p).close()
    ro = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    assert ro.execute("PRAGMA user_version").fetchone()[0] == 1
    ro.close()
