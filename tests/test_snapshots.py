"""Золотые снимки ответов API на детерминированных синтетических данных (фиксированная конечная дата).
Рефакторинг веб-бэкенда не должен менять ни байта этих payload (если это не оговорено явно)."""
import functools

import pytest
from fastapi.testclient import TestClient

from _snap import check
from _synth import NOW, synth_market  # noqa: F401
from tradebot.web import service
from tradebot.web.api import app

client = TestClient(app)


@pytest.fixture
def snap_market(synth_market, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service, "data_status", functools.partial(service.data_status, now=NOW))   # «сейчас» зафиксировано
    return synth_market[0]


def post(url, body):
    r = client.post(url, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_snap_meta(snap_market):
    r = client.get("/api/meta")
    assert r.status_code == 200
    check("meta", r.json())


def test_snap_data_status(snap_market):
    check("data_status", client.get("/api/data/status", params={"symbol": "BTCUSDT", "tf": "1h"}).json())


def test_snap_backtest(snap_market):
    check("backtest_ema", post("/api/backtest", {"strategy": "EMA cross", "tf": "1h", "days": 60,
                                                 "params": {"fast": 12, "slow": 50}, "overlays": {"htf": 50, "stop": 3.0}}))
    check("backtest_oi_flush", post("/api/backtest", {"strategy": "OI flush", "tf": "1h", "days": 90, "long_only": True,
                                                      "leverage": 2}))


def test_snap_backtest_spot(snap_market):
    """Спот: плечо 20 в запросе игнорируется (1x), только лонг, без фандинга, комиссия 0.10 %; ответ несёт market/effective."""
    check("backtest_spot", post("/api/backtest", {"strategy": "EMA cross", "tf": "1h", "days": 60, "params": {"fast": 12, "slow": 50},
                                                  "overlays": {"htf": 50, "stop": 3.0}, "market": "spot", "leverage": 20}))


def test_snap_portfolio(snap_market):
    legs = client.get("/api/meta").json()["legs"]
    check("portfolio", post("/api/portfolio", {"legs": legs, "days": 90, "leverage": 2}))


def test_snap_sweep(snap_market):
    check("sweep_1d", post("/api/sweep", {"strategy": "EMA cross", "tf": "1h", "days": 60, "params": {"slow": 60},
                                          "x": {"name": "fast", "min": 5, "max": 20, "n": 4}}))
    check("sweep_2d", post("/api/sweep", {"strategy": "EMA cross", "tf": "1h", "days": 60,
                                          "x": {"name": "fast", "min": 5, "max": 20, "n": 3},
                                          "y": {"name": "slow", "min": 30, "max": 100, "n": 3}}))


def test_snap_optimize(snap_market):
    check("optimize", post("/api/optimize", {"tf": "1h", "strategy": "EMA cross", "days": 120, "top": 3}))


def test_snap_leaderboard_item(snap_market):
    check("lb_item", post("/api/leaderboard/item", {"tf": "1h", "strategy": "Supertrend", "days": 120}))


def test_snap_stats(snap_market, monkeypatch, tmp_path):
    """/api/stats на детерминированной синтетической bot.db (золотая БД, «сейчас» = NOW1, свечи synth_market)."""
    import _stats_fixture as fx
    from tradebot.trading import state
    from tradebot.web import stats
    stats._cache.clear()
    monkeypatch.setattr(stats, "_now", lambda: fx.NOW1)
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "bot.db")
    fx.golden_db(state.DB_PATH)
    for rk in ("7d", "all"):
        r = client.get("/api/stats", params={"range": rk})
        assert r.status_code == 200, r.text
        check(f"stats_{rk}", r.json())
    check("stats_unavailable", stats.unavailable("30d", "no_db", "x", fx.NOW1))
