"""Аудит web/cache.py и research.py: карантин БД только при реальной порче, хэш кода, ключ optimize до чтения кадра."""
import sqlite3
import types

import pytest
from fastapi.testclient import TestClient

from _synth import synth_market  # noqa: F401
from tradebot.web import api as api_mod, cache, service
from tradebot.web.api import app

client = TestClient(app)
KW = dict(kind="lb", symbol="BTCUSDT", tf="1h", strategy="s", data_sig="x")


def _corrupt_files():
    return list(cache.DB_PATH.parent.glob("*.corrupt"))


# ---------------------------------------------------------------- W6
def test_garbage_db_file_is_quarantined_and_cache_restarts():
    cache.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache.DB_PATH.write_bytes(b"this is definitely not a sqlite database" * 50)
    assert cache.get("k") is None
    assert _corrupt_files()                                                       # испорченный файл отложен
    cache.put("k", {"x": 1}, **KW)
    assert cache.get("k") == {"x": 1}


def test_locked_database_does_not_quarantine(monkeypatch):
    cache.put("k", {"x": 1}, **KW)
    real = cache._connect

    def locked():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(cache, "_connect", locked)
    assert cache.get("k") is None                                                 # промах, но без последствий
    cache.put("k2", {"x": 2}, **KW)                                               # запись молча пропущена
    monkeypatch.setattr(cache, "_connect", real)
    assert not _corrupt_files()
    assert cache.get("k") == {"x": 1} and cache.get("k2") is None                 # данные целы


@pytest.mark.parametrize("msg", ["database or disk is full", "disk I/O error", "unable to open database file"])
def test_other_operational_errors_do_not_quarantine(monkeypatch, msg):
    cache.put("k", {"x": 1}, **KW)

    def boom(con):
        raise sqlite3.OperationalError(msg)
    assert cache._run(boom, "dflt") == "dflt"
    assert not _corrupt_files() and cache.get("k") == {"x": 1}


def test_is_corrupt_uses_sqlite_error_codes():
    e = sqlite3.DatabaseError("x")
    assert cache._is_corrupt(e) is False                                          # кода нет (не от sqlite) -> не порча
    for code, expected in ((sqlite3.SQLITE_CORRUPT, True), (sqlite3.SQLITE_NOTADB, True), (sqlite3.SQLITE_BUSY, False),
                           (sqlite3.SQLITE_FULL, False), (sqlite3.SQLITE_IOERR, False), (sqlite3.SQLITE_CORRUPT | (1 << 8), True)):
        e = sqlite3.DatabaseError("x")
        e.sqlite_errorcode = code
        assert cache._is_corrupt(e) is expected, code


# ---------------------------------------------------------------- W7
def test_code_hash_file_list_covers_result_producing_modules():
    names = set(cache._CODE_FILES)
    assert {"core/engine.py", "core/optimize.py", "core/strategies.py", "core/indicators.py", "core/portfolio.py", "market/derivs.py",
            "market/klines.py", "web/backtests.py", "web/research.py"} <= names
    root = cache.Path(cache.optimize.__file__).parent.parent
    assert all((root / n).is_file() for n in names), [n for n in names if not (root / n).is_file()]


def _fake_tree(tmp_path, monkeypatch, files):
    for rel, text in files.items():
        p = tmp_path / "tradebot" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text)
    fake = types.SimpleNamespace(__file__=str(tmp_path / "tradebot" / "core" / "optimize.py"), CACHE_VERSION=5)
    monkeypatch.setattr(cache, "optimize", fake)
    monkeypatch.setattr(cache, "_CODE_FILES", tuple(files))
    cache.code_hash.cache_clear()


def test_code_hash_changes_with_any_listed_file_and_ignores_line_endings(tmp_path, monkeypatch):
    files = {"core/optimize.py": b"a = 1\n", "web/backtests.py": b"b = 2\n", "market/derivs.py": b"c = 3\n"}
    try:
        _fake_tree(tmp_path, monkeypatch, files)
        h0 = cache.code_hash()
        (tmp_path / "tradebot" / "web" / "backtests.py").write_bytes(b"b = 2\r\n")        # только окончания строк
        cache.code_hash.cache_clear()
        assert cache.code_hash() == h0
        for rel in files:
            (tmp_path / "tradebot" / rel).write_bytes(files[rel] + b"# edit\n")
            cache.code_hash.cache_clear()
            assert cache.code_hash() != h0, rel
            (tmp_path / "tradebot" / rel).write_bytes(files[rel])
        cache.code_hash.cache_clear()
        assert cache.code_hash() == h0
        (tmp_path / "tradebot" / "market" / "derivs.py").unlink()                           # нет файла — тоже иной хэш, без исключения
        cache.code_hash.cache_clear()
        assert cache.code_hash() != h0
    finally:
        monkeypatch.undo()
        cache.code_hash.cache_clear()


# ---------------------------------------------------------------- W8
def test_optimize_cache_hit_does_not_load_the_frame(synth_market, monkeypatch):
    body = {"tf": "1h", "strategy": "EMA cross", "days": 60, "top": 2}
    first = client.post("/api/optimize", json=body).json()
    assert first["from_cache"] is False
    api_mod._cache.clear()                                                         # память процесса сброшена, дисковый кэш остаётся
    loads = []
    real = service.get_df
    monkeypatch.setattr(service, "get_df", lambda *a, **k: loads.append(a) or real(*a, **k))
    second = client.post("/api/optimize", json=body).json()
    assert second["from_cache"] is True and loads == []
    first.pop("from_cache"), second.pop("from_cache")
    assert first == second
    monkeypatch.setattr(cache, "get", lambda key: None)                            # промах -> кадр читается
    api_mod._cache.clear()
    assert client.post("/api/optimize", json=body).status_code == 200 and len(loads) == 1


def test_optimize_missing_data_is_still_409(monkeypatch, tmp_path):
    from tradebot.market import klines
    d = tmp_path / "empty"
    d.mkdir()
    monkeypatch.setattr(klines, "DATA_DIR", d)
    monkeypatch.setattr(service, "MARKET", d)
    service.invalidate_data()
    service._info_cache.clear()
    r = client.post("/api/optimize", json={"tf": "1h", "strategy": "EMA cross", "days": 60})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "data_missing"
