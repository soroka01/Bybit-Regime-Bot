"""Тесты веб-API (FastAPI TestClient). Сеть не используется: данные только из локального кэша."""
import pytest
from fastapi.testclient import TestClient

from tradebot.paths import MARKET, RESULTS
from tradebot.web.api import app

client = TestClient(app)


def _needs(cond: bool, reason: str):
    """Тест на живых данных из storage/: пропускается без кэша и помечен realdata (по умолчанию не запускается)."""
    def deco(fn):
        return pytest.mark.realdata(pytest.mark.skipif(not cond, reason=reason)(fn))
    return deco


needs_data = _needs((MARKET / "BTCUSDT_1h.parquet").exists(), "нет локального кэша BTCUSDT")
needs_wf = _needs((RESULTS / "wf_BTCUSDT.csv").exists(), "нет результатов walk-forward")


def test_meta():
    r = client.get("/api/meta")
    assert r.status_code == 200
    m = r.json()
    names = {s["name"] for s in m["strategies"]}
    assert {"EMA cross", "OI flush", "Funding contrarian"} <= names
    p = next(s for s in m["strategies"] if s["name"] == "EMA cross")["params"]["fast"]
    assert p["min"] < p["default"] < p["max"] and p["kind"] == "int"
    assert m["defaults"]["fee"] == 0.001
    assert client.get("/api/meta", headers={"If-None-Match": r.headers["etag"]}).status_code == 304


def test_validation_and_whitelist():
    assert client.post("/api/backtest", json={"strategy": "EMA cross", "days": 5}).status_code == 422
    assert client.post("/api/backtest", json={"strategy": "EMA cross", "leverage": 500}).status_code == 422
    assert client.post("/api/backtest", json={"strategy": "nope"}).status_code == 422
    assert client.post("/api/backtest", json={"strategy": "EMA cross", "symbol": "../../etc"}).status_code == 422
    assert client.get("/api/walkforward?lev=4").status_code == 422


def test_missing_data_is_409_not_network():
    r = client.post("/api/backtest", json={"strategy": "EMA cross", "symbol": "ETHUSDT", "tf": "1h"})
    if (MARKET / "ETHUSDT_1h.parquet").exists():
        pytest.skip("ETH уже скачан")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "data_missing"


@needs_data
def test_backtest_small():
    body = {"strategy": "EMA cross", "tf": "4h", "days": 180, "params": {"fast": 9999, "slow": 50}, "overlays": {"htf": 50}}
    r = client.post("/api/backtest", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["params"]["fast"] <= 100            # параметр зажат в диапазон сетки x1.5
    assert len(j["t"]) == len(j["equity"]) == len(j["bh"]) == len(j["drawdown"]) <= 1600
    assert {"total_return", "max_drawdown", "sharpe", "trades"} <= set(j["metrics"])
    assert j["monthly"] and "markers" in j
    assert client.post("/api/backtest", json=body).headers["x-cache"] == "hit"


@needs_data
def test_portfolio_and_sweep():
    legs = client.get("/api/meta").json()["legs"]
    r = client.post("/api/portfolio", json={"legs": legs, "days": 180, "leverage": 2})
    assert r.status_code == 200, r.text
    assert set(r.json()["strips"]["legs"]) == {"SMA cross@4h", "OI flush@1h", "Regime switch (CHOP)@6h"}
    r = client.post("/api/sweep", json={"strategy": "EMA cross", "tf": "4h", "days": 180,
                                        "x": {"name": "fast", "min": 5, "max": 20, "n": 3},
                                        "y": {"name": "slow", "min": 30, "max": 100, "n": 3}})
    assert r.status_code == 200, r.text
    assert len(r.json()["grids"]["total_return"]) == 3
    bad = client.post("/api/sweep", json={"strategy": "EMA cross", "x": {"name": "fast", "min": 1, "max": 9, "n": 25},
                                          "y": {"name": "slow", "min": 1, "max": 9, "n": 25}})
    assert bad.status_code == 422


@needs_wf
def test_walkforward():
    rows = client.get("/api/walkforward?lev=1").json()
    assert rows and {"tf", "strategy", "variant", "total_return"} <= set(rows[0])
    row = rows[0]
    c = client.get("/api/walkforward/curve", params={"lev": 1, "tf": row["tf"], "strategy": row["strategy"], "variant": row["variant"]})
    assert c.status_code == 200 and len(c.json()["t"]) == len(c.json()["equity"])
    assert client.get("/api/walkforward/curve", params={"lev": 1, "tf": "x", "strategy": "y", "variant": "z"}).status_code == 422
    assert client.get("/api/walkforward/windows?lev=2").json()


def test_live_fallback(monkeypatch):
    import sys
    import types

    mod = types.ModuleType("tradebot.trading.state")
    mod.read_snapshot = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    monkeypatch.setitem(sys.modules, "tradebot.trading.state", mod)
    import tradebot.trading as pkg
    monkeypatch.setattr(pkg, "state", mod, raising=False)
    assert client.get("/api/live").json()["available"] is False

    mod.read_snapshot = lambda: {"enabled": True, "api_key": "SECRET", "legs": [{"key": "a@1h"}], "x": float("nan")}
    j = client.get("/api/live").json()
    assert j["available"] is True and "api_key" not in j and j["legs"][0]["key"] == "a@1h" and j["x"] is None


def test_static_index_and_no_traversal():
    assert client.get("/").status_code == 200
    assert client.get("/..%2f..%2fpyproject.toml").status_code in (404, 200)  # не отдаёт файлы вне web/dist
    assert b"[project]" not in client.get("/..%2f..%2fpyproject.toml").content


@needs_data
def test_optimize_endpoint():
    body = {"tf": "4h", "strategy": "EMA cross", "days": 365, "criterion": "robust", "top": 3}
    r = client.post("/api/optimize", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["n_combos"] == 36 and 1 <= len(j["results"]) <= 3
    row = j["results"][0]
    assert {"params", "is", "oos", "full", "robust_score", "verdict"} <= set(row)
    assert {"is_range", "oos_range", "buy_hold_oos", "elapsed"} <= set(j)
    assert client.post("/api/optimize", json={**body, "criterion": "yolo"}).status_code == 422
    assert client.post("/api/optimize", json={**body, "top": 99}).status_code == 422
    assert client.post("/api/optimize", json={**body, "strategy": "zzz"}).status_code == 422
    assert client.post("/api/optimize", json={**body, "symbol": "ETHUSDT"}).status_code in (409, 200)


@needs_data
def test_leaderboard():
    plan = client.get("/api/leaderboard/plan", params={"symbol": "BTCUSDT", "tf": "4h"}).json()
    assert plan["klines"] and any(s["name"] == "OI flush" for s in plan["strategies"])
    r = client.post("/api/leaderboard/item", json={"tf": "4h", "strategy": "Supertrend", "days": 730})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["result"]["verdict"] in ("robust", "overfit", "loss") and 10 < len(j["result"]["curve"]["t"]) <= 120
    assert client.get("/api/leaderboard/plan", params={"symbol": "XXX", "tf": "4h"}).status_code == 422
    assert client.post("/api/leaderboard/item", json={"tf": "4h", "strategy": "Supertrend", "leverage": 101}).status_code == 422
    if not (MARKET / "ETHUSDT_4h.parquet").exists():
        assert client.get("/api/leaderboard/plan", params={"symbol": "ETHUSDT", "tf": "4h"}).json()["strategies"][0]["ready"] is False
        r = client.post("/api/leaderboard/item", json={"symbol": "ETHUSDT", "tf": "4h", "strategy": "Supertrend"})
        assert r.status_code == 409


# ---------------------------------------------------------------- свежесть данных / обновление (без сети)
@pytest.fixture
def fake_market(tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd

    from tradebot.market import derivs, klines
    from tradebot.web import api as api_mod, service

    monkeypatch.setattr(klines, "DATA_DIR", tmp_path)
    monkeypatch.setattr(service, "MARKET", tmp_path)
    service.invalidate_data()
    api_mod._cache.clear()
    step = pd.Timedelta(hours=1)
    expected = pd.Timestamp.now(tz="UTC").floor(step) - step

    def bars(end, n):
        idx = pd.date_range(end=end, periods=n, freq="h")
        c = 100 * np.exp(np.cumsum(np.random.default_rng(len(idx)).normal(0, 0.004, n)))
        return pd.DataFrame({"open": c, "high": c * 1.002, "low": c * 0.998, "close": c, "volume": 1.0, "turnover": 1.0}, index=idx.rename("ts"))

    klines._atomic_parquet(bars(expected - 5 * step, 800), klines._path("BTCUSDT", "1h"))
    long_idx = pd.date_range(end=expected + step, periods=1000, freq="h").rename("ts")
    for name, col in (("funding", "rate"), ("oi_1h", "oi"), ("lsr_1h", "ratio")):
        klines._atomic_parquet(pd.DataFrame({col: np.full(len(long_idx), 1e-4 if col == "rate" else 1.0)}, index=long_idx),
                               tmp_path / f"BTCUSDT_{name}.parquet")
    calls = {"enrich": 0}

    def fake_load(symbol, tf, days=1095, refresh=True):
        old = pd.read_parquet(klines._path(symbol, tf))
        new = bars(expected, len(old) + 5)
        klines._atomic_parquet(new, klines._path(symbol, tf))
        return new

    real_enrich = derivs.enrich

    def fake_enrich(df, symbol="BTCUSDT", refresh=True, days=1095):
        if refresh:                      # «сетевой» вариант подменён; чтение из кэша (refresh=False) — настоящее
            calls["enrich"] += 1
            return df
        return real_enrich(df, symbol, refresh, days)

    monkeypatch.setattr(klines, "load", fake_load)
    monkeypatch.setattr(derivs, "enrich", fake_enrich)
    return calls


def test_data_status_levels(fake_market):
    j = client.get("/api/data/status", params={"symbol": "BTCUSDT", "tf": "1h"}).json()
    assert j["exists"] and j["bars"] == 800 and j["behind_bars"] == 5 and j["level"] == "bad"
    assert j["derivs"]["funding"] and j["derivs"]["oi"] and j["derivs"]["lsr"]
    assert client.get("/api/data/status", params={"symbol": "ETHUSDT", "tf": "1h"}).json()["exists"] is False
    assert client.get("/api/data/status", params={"symbol": "BTCUSDT", "tf": "7m"}).status_code == 422


def test_data_update_adds_bars_and_invalidates_caches(fake_market, monkeypatch):
    from tradebot.web import api as api_mod

    body = {"strategy": "EMA cross", "tf": "1h", "days": 30}
    first = client.post("/api/backtest", json=body)
    assert first.status_code == 200, first.text
    assert client.post("/api/backtest", json=body).headers["x-cache"] == "hit"
    api_mod._cache["sentinel"] = {"x": 1}
    r = client.post("/api/data/update", json={"symbol": "BTCUSDT", "tf": "1h"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["added"] == 5 and j["status"]["behind_bars"] == 0 and j["status"]["level"] == "ok" and j["warning"] is None
    assert fake_market["enrich"] == 1
    assert "sentinel" not in api_mod._cache                    # кэш расчётов сброшен
    again = client.post("/api/backtest", json=body)
    assert again.headers["x-cache"] == "miss"                  # пересчитано на новых данных
    assert again.json()["data_range"]["end"] > first.json()["data_range"]["end"]
    assert client.post("/api/data/update", json={"symbol": "BTCUSDT", "tf": "9x"}).status_code == 422
    assert client.post("/api/data/update", json={"symbol": "DOGE", "tf": "1h"}).status_code == 422


def test_data_update_network_error_is_clean(fake_market, monkeypatch):
    from tradebot.market import klines

    path = klines._path("BTCUSDT", "1h")
    before = path.read_bytes()
    monkeypatch.setattr(klines, "load", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("нет сети")))
    r = client.post("/api/data/update", json={"symbol": "BTCUSDT", "tf": "1h"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "update_failed" and "нет сети" in r.json()["detail"]["message"]
    assert path.read_bytes() == before                         # файл не тронут


@needs_data
def test_sweep_1d_profile_for_single_param_strategy():
    body = {"strategy": "Momentum (ROC)", "tf": "4h", "days": 365, "x": {"name": "n", "min": 1, "max": 5000, "n": 6}}
    r = client.post("/api/sweep", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["y"] is None and j["grids"] == {}
    xs = j["x"]["values"]
    assert 2 <= len(xs) <= 6 and xs == sorted(xs)
    meta_n = next(s for s in client.get("/api/meta").json()["strategies"] if s["name"] == "Momentum (ROC)")["params"]["n"]
    assert min(xs) >= meta_n["min"] and max(xs) <= meta_n["max"]          # зажато в диапазон слайдера (сетка x1.5)
    assert len(j["points"]) == len(xs)
    assert {"total_return", "sharpe", "calmar", "max_drawdown", "trades", "win_rate"} <= set(j["points"][0])
    # 1D для двухпараметрической стратегии тоже допустим (второй параметр берётся из params)
    r2 = client.post("/api/sweep", json={"strategy": "EMA cross", "tf": "4h", "days": 180, "params": {"slow": 80},
                                         "x": {"name": "fast", "min": 5, "max": 20, "n": 4}})
    assert r2.status_code == 200 and r2.json()["y"] is None
    # неизвестный параметр и одинаковые оси в 2D -> 422
    assert client.post("/api/sweep", json={**body, "x": {"name": "zzz", "min": 1, "max": 5, "n": 3}}).status_code == 422
    same = {"strategy": "EMA cross", "tf": "4h", "x": {"name": "fast", "min": 5, "max": 9, "n": 3}, "y": {"name": "fast", "min": 5, "max": 9, "n": 3}}
    assert client.post("/api/sweep", json=same).status_code == 422


@needs_data
def test_short_oos_flags_in_responses():
    r = client.post("/api/leaderboard/item", json={"tf": "4h", "strategy": "Supertrend", "days": 180})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["short_oos"] is True and 55 < j["oos_days"] < 65
    assert j["result"] is None or j["result"]["verdict"] in ("few", "loss")
    o = client.post("/api/optimize", json={"tf": "4h", "strategy": "EMA cross", "days": 180, "top": 2}).json()
    assert o["short_oos"] is True and o["oos_days"] < 90
    assert client.post("/api/optimize", json={"tf": "4h", "strategy": "EMA cross", "days": 1095, "top": 1}).json()["short_oos"] is False
