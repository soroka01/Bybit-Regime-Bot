"""Дисковый кэш рейтинга/подбора: попадание, инвалидация по данным и по коду, force, битые файлы, purge, /cached, meta."""
import sqlite3

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tradebot.market import klines
from tradebot.web import api as api_mod, cache, service
from tradebot.web.api import app

client = TestClient(app)
ITEM = {"symbol": "BTCUSDT", "tf": "1h", "strategy": "EMA cross", "days": 365}


def _bars(end, n, seed=1):
    idx = pd.date_range(end=end, periods=n, freq="h", tz="UTC").rename("ts")
    c = 100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, 0.004, n)))
    return pd.DataFrame({"open": c, "high": c * 1.002, "low": c * 0.998, "close": c, "volume": 1.0, "turnover": 1.0}, index=idx)


@pytest.fixture
def market(tmp_path, monkeypatch):
    d = tmp_path / "market"
    d.mkdir()
    monkeypatch.setattr(klines, "DATA_DIR", d)
    monkeypatch.setattr(service, "MARKET", d)
    service.invalidate_data()
    service._info_cache.clear()
    api_mod._cache.clear()
    end = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=1)
    klines._atomic_parquet(_bars(end, 1500), klines._path("BTCUSDT", "1h"))
    long_idx = pd.date_range(end=end + pd.Timedelta(hours=1), periods=1600, freq="h", tz="UTC").rename("ts")
    for name, col, val in (("funding", "rate", 1e-4), ("oi_1h", "oi", 1.0), ("lsr_1h", "ratio", 1.0)):
        klines._atomic_parquet(pd.DataFrame({col: np.full(len(long_idx), val)}, index=long_idx), d / f"BTCUSDT_{name}.parquet")
    return d


@pytest.fixture
def spy(monkeypatch):
    calls = {"n": 0}
    real = service._optimize

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(service, "_optimize", counting)
    return calls


def post_item(**extra):
    api_mod._cache.clear()           # изолируем именно ДИСКОВЫЙ кэш от памяти процесса
    return client.post("/api/leaderboard/item", json={**ITEM, **extra})


def test_hit_avoids_compute_and_force_bypasses(market, spy):
    r1 = post_item()
    assert r1.status_code == 200, r1.text
    assert r1.json()["from_cache"] is False and spy["n"] == 1
    r2 = post_item()
    assert r2.json()["from_cache"] is True and spy["n"] == 1                # попадание: расчёта нет
    assert r2.json()["result"] == r1.json()["result"]
    r3 = post_item(force=True)
    assert r3.json()["from_cache"] is False and spy["n"] == 2               # force обходит кэш
    assert post_item().json()["from_cache"] is True and spy["n"] == 2       # а результат force лежит в кэше


def test_settings_are_part_of_the_key(market, spy):
    post_item()
    post_item(leverage=2)
    post_item(long_only=True)
    post_item(variants=True)
    assert spy["n"] == 1 + 1 + 1 + 4


def test_data_change_invalidates(market, spy):
    post_item()
    last = pd.read_parquet(klines._path("BTCUSDT", "1h")).index[-1]
    klines._atomic_parquet(_bars(last + pd.Timedelta(hours=3), 1503), klines._path("BTCUSDT", "1h"))   # +3 новых свечи
    r = post_item()
    assert r.json()["from_cache"] is False and spy["n"] == 2


def test_unused_derivative_series_do_not_invalidate(market, spy):
    post_item()                                               # EMA cross: OI/LSR не используются
    d = market / "BTCUSDT_oi_1h.parquet"
    df = pd.read_parquet(d)
    klines._atomic_parquet(pd.concat([df, df.iloc[-1:].set_axis(df.index[-1:] + pd.Timedelta(hours=1))]), d)
    assert post_item().json()["from_cache"] is True and spy["n"] == 1
    r = client.post("/api/leaderboard/item", json={**ITEM, "strategy": "OI trend"})   # а OI-стратегия — использует
    assert r.status_code == 200
    n = spy["n"]
    df = pd.read_parquet(d)
    klines._atomic_parquet(pd.concat([df, df.iloc[-1:].set_axis(df.index[-1:] + pd.Timedelta(hours=1))]), d)
    api_mod._cache.clear()
    assert client.post("/api/leaderboard/item", json={**ITEM, "strategy": "OI trend"}).json()["from_cache"] is False
    assert spy["n"] == n + 1


def test_code_hash_change_invalidates(market, spy, monkeypatch):
    post_item()
    monkeypatch.setattr(cache, "code_hash", lambda: "another-code-version")
    assert post_item().json()["from_cache"] is False and spy["n"] == 2
    monkeypatch.setattr(cache.optimize, "CACHE_VERSION", 999)
    assert post_item().json()["from_cache"] is False and spy["n"] == 3


def test_corrupted_entry_and_corrupted_db_are_ignored(market, spy):
    post_item()
    con = sqlite3.connect(cache.DB_PATH)
    con.execute("UPDATE items SET payload='{broken json'")
    con.commit()
    con.close()
    assert post_item().json()["from_cache"] is False and spy["n"] == 2      # битая строка = промах, пересчёт
    assert post_item().json()["from_cache"] is True                         # и снова исправный кэш
    cache.DB_PATH.write_bytes(b"this is not a sqlite database" * 50)        # убит весь файл
    for suffix in ("-wal", "-shm"):
        (cache.DB_PATH.parent / (cache.DB_PATH.name + suffix)).unlink(missing_ok=True)
    r = post_item()
    assert r.status_code == 200 and r.json()["from_cache"] is False and spy["n"] == 3
    assert post_item().json()["from_cache"] is True                         # БД пересоздана


def test_cached_endpoint_split_and_no_compute(market, spy):
    body = {"symbol": "BTCUSDT", "tf": "1h", "days": 365}
    empty = client.post("/api/leaderboard/cached", json=body).json()
    assert empty["items"] == [] and "EMA cross" in empty["missing"] and spy["n"] == 0
    post_item()
    j = client.post("/api/leaderboard/cached", json=body).json()
    assert [i["strategy"] for i in j["items"]] == ["EMA cross"] and all(i["from_cache"] for i in j["items"])
    assert "EMA cross" not in j["missing"] and "SMA cross" in j["missing"] and spy["n"] == 1
    assert j["data_last"] and j["fingerprint"] and j["plan"]["klines"] is True
    other = client.post("/api/leaderboard/cached", json={**body, "leverage": 2}).json()
    assert other["items"] == []
    assert client.post("/api/leaderboard/cached", json={**body, "tf": "9x"}).status_code == 422
    assert client.post("/api/leaderboard/cached", json={**body, "leverage": 101}).status_code == 422


def test_update_purges_stale_entries(market, spy, monkeypatch):
    post_item()
    body = {"symbol": "BTCUSDT", "tf": "1h", "days": 365}
    assert len(client.post("/api/leaderboard/cached", json=body).json()["items"]) == 1
    assert cache.stats()["entries"] == 1

    def fake_load(symbol, tf, days=1095, refresh=True):
        old = pd.read_parquet(klines._path(symbol, tf))
        new = _bars(old.index[-1] + pd.Timedelta(hours=2), len(old) + 2)
        klines._atomic_parquet(new, klines._path(symbol, tf))
        return new

    from tradebot.market import derivs
    monkeypatch.setattr(klines, "load", fake_load)
    monkeypatch.setattr(derivs, "enrich", lambda df, *a, **k: df)
    r = client.post("/api/data/update", json={"symbol": "BTCUSDT", "tf": "1h"})
    assert r.status_code == 200 and r.json()["added"] == 2 and r.json()["purged"] == 1
    assert cache.stats()["entries"] == 0
    assert client.post("/api/leaderboard/cached", json=body).json()["items"] == []


def test_eviction_bounds(market, monkeypatch):
    monkeypatch.setattr(cache, "MAX_ENTRIES", 5)
    for i in range(9):
        cache.put(f"k{i}", {"i": i}, kind="lb", symbol="BTCUSDT", tf="1h", strategy="s", data_sig="x")
    assert cache.stats()["entries"] == 5
    assert cache.get("k8") == {"i": 8} and cache.get("k0") is None           # вытеснены самые старые


def test_optimize_uses_same_cache(market, spy):
    body = {"tf": "1h", "strategy": "EMA cross", "days": 365, "top": 2}
    a = client.post("/api/optimize", json=body).json()
    api_mod._cache.clear()
    b = client.post("/api/optimize", json=body).json()
    assert a["from_cache"] is False and b["from_cache"] is True and spy["n"] == 1
    assert a["results"] == b["results"]


def test_meta_reflects_newly_cached_file(market):
    before = client.get("/api/meta", headers={"Cache-Control": "no-cache"}).json()
    assert "4h" not in before["klines_available"]["BTCUSDT"] and "1h" in before["klines_available"]["BTCUSDT"]
    klines._atomic_parquet(_bars(pd.Timestamp.now(tz="UTC").floor("4h") - pd.Timedelta(hours=4), 300), klines._path("BTCUSDT", "4h"))
    after = client.get("/api/meta").json()
    assert "4h" in after["klines_available"]["BTCUSDT"]
    assert "4h" not in after["available"]["BTCUSDT"]           # деривативов 4h-гранулярности ещё нет
    assert client.get("/api/meta").headers["etag"] != client.get("/api/meta").headers.get("x", "")
