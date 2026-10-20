"""Quick-fix'ы веб-бэкенда (аудит, шаг 2): версия данных, кэш, NaN в запросах, пустые файлы, отпечатки, middleware."""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from _synth import synth_market  # noqa: F401
from tradebot.market import klines
from tradebot.web import api as api_mod, backtests, cache, common, data, lanes
from tradebot.web.api import app

client = TestClient(app)
JSON = {"content-type": "application/json"}


# ---------------------------------------------------------------- B2: data_version
def test_data_version_ignores_cache_dir_and_directories(synth_market, monkeypatch):
    market, res = synth_market
    monkeypatch.setattr(cache, "DB_PATH", res / "cache" / "web_cache.sqlite3")     # как в проде: БД внутри RESULTS
    v0 = data.data_version()
    assert v0
    key = cache.make_key("t", a=1)
    cache.put(key, {"x": 1}, kind="lb", symbol="BTCUSDT", tf="1h", strategy="s", data_sig="x")
    assert cache.get(key) == {"x": 1}
    cache.get_many([key, "nope"])
    cache.stats()
    assert (res / "cache").is_dir()
    assert data.data_version() == v0                                              # WAL/каталоги версию не меняют
    (res / "subdir").mkdir()
    assert data.data_version() == v0


def test_data_version_tracks_inputs(synth_market):
    market, res = synth_market
    v0 = data.data_version()
    p = klines._path("BTCUSDT", "1h")
    klines._atomic_parquet(pd.read_parquet(p).iloc[:-1], p)
    v1 = data.data_version()
    assert v1 != v0
    (res / "wf_BTCUSDT.csv").write_text("a\n1\n")
    assert data.data_version() != v1
    (res / "other.txt").write_text("x")
    v2 = data.data_version()
    (res / "other.txt").write_text("y")
    assert data.data_version() == v2                                              # посторонние файлы не важны


def test_heavy_hit_survives_cache_access(synth_market, monkeypatch):
    _, res = synth_market
    monkeypatch.setattr(cache, "DB_PATH", res / "cache" / "web_cache.sqlite3")
    body = {"strategy": "EMA cross", "tf": "1h", "days": 60}
    assert client.post("/api/backtest", json=body).headers["x-cache"] == "miss"
    client.post("/api/leaderboard/item", json={"tf": "1h", "strategy": "EMA cross", "days": 60})   # трогает sqlite-кэш
    assert client.post("/api/backtest", json=body).headers["x-cache"] == "hit"


# ---------------------------------------------------------------- B3: cache.put не падает
def test_cache_put_never_raises():
    kw = dict(kind="lb", symbol="BTCUSDT", tf="1h", strategy="s", data_sig="x")
    cache.put("nan", {"x": float("nan")}, **kw)                  # allow_nan=False -> ValueError внутри защищённой части
    cache.put("obj", {"x": object()}, **kw)                      # TypeError
    assert cache.get("nan") is None and cache.get("obj") is None
    cache.put("ok", {"x": 1}, **kw)
    assert cache.get("ok") == {"x": 1}


def test_cache_write_failure_does_not_500(synth_market, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("диск")
    monkeypatch.setattr(cache, "_connect", boom)
    r = client.post("/api/leaderboard/item", json={"tf": "1h", "strategy": "EMA cross", "days": 60})
    assert r.status_code == 200 and r.json()["from_cache"] is False


# ---------------------------------------------------------------- B6: NaN/Infinity в запросах
@pytest.mark.parametrize("url,raw", [
    ("/api/sweep", '{"strategy":"EMA cross","x":{"name":"fast","min":NaN,"max":5}}'),
    ("/api/sweep", '{"strategy":"EMA cross","x":{"name":"fast","min":1,"max":Infinity}}'),
    ("/api/sweep", '{"strategy":"EMA cross","x":{"name":"fast","min":-Infinity,"max":5}}'),
    ("/api/backtest", '{"strategy":"EMA cross","params":{"fast":NaN}}'),
    ("/api/backtest", '{"strategy":"EMA cross","fee":NaN}'),
    ("/api/backtest", '{"strategy":"EMA cross","overlays":{"stop":Infinity}}'),
    ("/api/portfolio", '{"legs":[{"strategy":"EMA cross","tf":"1h","params":{"fast":Infinity}}]}'),
    ("/api/portfolio", '{"legs":[{"strategy":"EMA cross","tf":"1h","weight":NaN}]}'),
])
def test_nan_inf_in_requests_are_422(url, raw):
    r = client.post(url, content=raw, headers=JSON)
    assert r.status_code == 422, (raw, r.text)


# ---------------------------------------------------------------- B8: пустые файлы / KeyError
def test_empty_parquet_is_422_with_clear_message(synth_market):
    p = klines._path("BTCUSDT", "1h")
    klines._atomic_parquet(pd.read_parquet(p).iloc[:0], p)
    for url, body in (("/api/backtest", {"strategy": "EMA cross", "tf": "1h", "days": 60}),
                      ("/api/optimize", {"strategy": "EMA cross", "tf": "1h", "days": 60}),
                      ("/api/sweep", {"strategy": "EMA cross", "tf": "1h", "x": {"name": "fast", "min": 5, "max": 9, "n": 2}}),
                      ("/api/leaderboard/item", {"strategy": "EMA cross", "tf": "1h"})):
        r = client.post(url, json=body)
        assert r.status_code == 422, (url, r.status_code, r.text)
        assert "файл данных пуст" in r.json()["detail"]["message"]


def test_slice_of_empty_frame_is_clean_error():
    with pytest.raises(data.DataEmpty):
        backtests._slice(pd.DataFrame({"close": []}, index=pd.DatetimeIndex([], tz="UTC")), 30)


def test_keyerror_is_mapped_with_context(monkeypatch):
    def boom(req, dl=None):
        raise KeyError("oi")
    monkeypatch.setattr(backtests, "run_backtest", boom)
    r = client.post("/api/backtest", json={"strategy": "EMA cross"})
    assert r.status_code == 422
    msg = r.json()["detail"]["message"]
    assert "oi" in msg and msg != "'oi'"


# ---------------------------------------------------------------- B4: отпечаток данных
def _touch_series(path, nrows=1):
    df = pd.read_parquet(path)
    step = df.index[-1] - df.index[-2]
    extra = df.iloc[-nrows:].copy()
    extra.index = extra.index + step * nrows
    klines._atomic_parquet(pd.concat([df, extra]), path)


def test_fingerprint_deriv_strategies_cover_oi_and_lsr(synth_market):
    market, _ = synth_market
    from tradebot.core.strategies import STRATEGIES
    deriv = sorted({"Funding contrarian", "Long/short ratio contrarian", "OI trend", "OI flush"} |
                   {n for n, s in STRATEGIES.items() if s.derivative})
    assert len(deriv) >= 4
    before = {n: data.data_fingerprint("BTCUSDT", "1h", n) for n in [*deriv, "EMA cross"]}
    _touch_series(market / "BTCUSDT_oi_1h.parquet")
    mid = {n: data.data_fingerprint("BTCUSDT", "1h", n) for n in before}
    assert all(mid[n] != before[n] for n in deriv)                         # OI меняет отпечаток ВСЕХ деривативных
    assert mid["EMA cross"] == before["EMA cross"]                          # обычная — нет
    _touch_series(market / "BTCUSDT_lsr_1h.parquet")
    after = {n: data.data_fingerprint("BTCUSDT", "1h", n) for n in before}
    assert all(after[n] != mid[n] for n in deriv) and after["EMA cross"] == before["EMA cross"]
    _touch_series(market / "BTCUSDT_oi_4h.parquet")                         # другая гранулярность не влияет на 1h
    assert data.data_fingerprint("BTCUSDT", "1h", "OI flush") == after["OI flush"]


def test_derivative_flag_strategies_are_in_deriv_set():
    from tradebot.core.strategies import STRATEGIES
    assert {n for n, s in STRATEGIES.items() if s.derivative} <= data.DERIV_STRATEGIES


# ---------------------------------------------------------------- B9/B10: кэш закодированных байтов
def test_memory_hit_returns_identical_bytes(synth_market):
    body = {"strategy": "EMA cross", "tf": "1h", "days": 60}
    a = client.post("/api/backtest", json=body)
    b = client.post("/api/backtest", json=body)
    assert a.headers["x-cache"] == "miss" and b.headers["x-cache"] == "hit"
    assert a.content == b.content and b.headers["content-type"] == "application/json"
    assert all(isinstance(v, tuple) and isinstance(v[1], bytes) for v in api_mod._cache.values())


def test_lb_item_memory_hit_reports_from_cache(synth_market):
    body = {"tf": "1h", "strategy": "EMA cross", "days": 60}
    a = client.post("/api/leaderboard/item", json=body)
    b = client.post("/api/leaderboard/item", json=body)
    assert (a.json()["from_cache"], b.json()["from_cache"]) == (False, True)
    assert b.headers["x-cache"] == "hit"
    ja, jb = a.json(), b.json()
    ja.pop("from_cache"), jb.pop("from_cache")
    assert ja == jb
    o1 = client.post("/api/optimize", json={"tf": "1h", "strategy": "EMA cross", "days": 60, "top": 2}).json()
    o2 = client.post("/api/optimize", json={"tf": "1h", "strategy": "EMA cross", "days": 60, "top": 2}).json()
    assert (o1["from_cache"], o2["from_cache"]) == (False, True)


def test_force_bypasses_memory_cache(synth_market):
    body = {"tf": "1h", "strategy": "EMA cross", "days": 60, "force": True}
    a = client.post("/api/leaderboard/item", json=body)
    b = client.post("/api/leaderboard/item", json=body)
    assert a.headers["x-cache"] == b.headers["x-cache"] == "miss"
    assert b.json()["from_cache"] is False


# ---------------------------------------------------------------- B7: ASGI-middleware, C1: gzip
def test_same_origin_guard():
    body = {"strategy": "nope"}
    assert client.post("/api/backtest", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/backtest", json=body, headers={"Origin": "http://testserver"}).status_code == 422
    assert client.post("/api/backtest", json=body).status_code == 422                       # без Origin (скрипты) — можно
    r = client.post("/api/backtest", json=body, headers={"Origin": "https://testserver:9"})
    assert r.status_code == 403 and r.json() == {"detail": "cross-origin запрещён"}
    assert client.get("/api/meta", headers={"Origin": "http://evil.example"}).status_code == 200   # GET не проверяется
    assert client.post("/api/data/update", json={"symbol": "BTCUSDT", "tf": "1h"},
                       headers={"Origin": "http://evil.example"}).status_code == 403


def test_host_allow_list():
    assert client.get("/api/meta", headers={"Host": "evil.example"}).status_code == 400


def test_gzip_and_etag():
    r = client.get("/api/meta", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
    assert r.json()["strategies"]
    etag = r.headers["etag"]
    r2 = client.get("/api/meta", headers={"Accept-Encoding": "gzip", "If-None-Match": etag})
    assert r2.status_code == 304
    plain = client.get("/api/meta", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers and plain.headers["etag"] == etag
    small = client.get("/api/leaderboard/plan", params={"symbol": "BTCUSDT", "tf": "1h"}, headers={"Accept-Encoding": "gzip"})
    assert small.status_code == 200


# ---------------------------------------------------------------- C6: LRU кадров
def test_df_cache_is_lru_bounded(monkeypatch):
    monkeypatch.setattr(data, "DF_CACHE_SIZE", 3)
    data.invalidate_data()
    with data._df_lock:
        for i in range(5):
            data._df_put(("S", f"t{i}"), (i,), pd.DataFrame())
        assert list(data._df_cache) == [("S", "t2"), ("S", "t3"), ("S", "t4")]
    data.invalidate_data()


def test_get_df_hit_refreshes_lru(synth_market, monkeypatch):
    monkeypatch.setattr(data, "DF_CACHE_SIZE", 2)
    data.get_df("BTCUSDT", "1h")
    data.get_df("BTCUSDT", "4h")
    data.get_df("BTCUSDT", "1h")                        # 1h снова «свежий»
    with data._df_lock:
        data._df_put(("X", "y"), (0,), pd.DataFrame())
        assert ("BTCUSDT", "1h") in data._df_cache and ("BTCUSDT", "4h") not in data._df_cache
    data.invalidate_data()


# ---------------------------------------------------------------- шаг 3 (c): Deadline в backtest/portfolio
def test_backtest_and_portfolio_honour_deadline(synth_market):
    from tradebot.web.schemas import BacktestReq, PortfolioReq
    expired = common.Deadline(-1)
    with pytest.raises(TimeoutError):
        backtests.run_backtest(BacktestReq(strategy="EMA cross", tf="1h", days=60, overlays={"stop": 3.0}), expired)
    legs = client.get("/api/meta").json()["legs"]
    with pytest.raises(TimeoutError):
        backtests.run_portfolio(PortfolioReq(legs=legs, days=60), expired)
    assert backtests.run_backtest(BacktestReq(strategy="EMA cross", tf="1h", days=60))["metrics"]      # без Deadline — как раньше


def test_expired_deadline_maps_to_504(synth_market, monkeypatch):
    monkeypatch.setattr(lanes, "HEAVY_TIMEOUT", -1.0)
    for url, body in (("/api/backtest", {"strategy": "EMA cross", "tf": "1h", "days": 60}),
                      ("/api/portfolio", {"legs": client.get("/api/meta").json()["legs"], "days": 60})):
        r = client.post(url, json=body)
        assert r.status_code == 504 and r.json()["detail"]["code"] == "timeout", (url, r.text)
