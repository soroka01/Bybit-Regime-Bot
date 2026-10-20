"""Аудит web/: враждебные symbol/tf на всех эндпоинтах, middleware (Sec-Fetch-Site, Content-Type, защитные заголовки),
разрешённые Host, статика с NUL-байтом, кодирование URI базы. Сети нет; реальные файлы данных не читаются."""
import json
import os
import subprocess
import sys

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from _synth import synth_market  # noqa: F401
from tradebot.market import derivs, klines
from tradebot.web import api as api_mod, schemas, service, stats_io
from tradebot.web.api import app

client = TestClient(app)
JSON = {"content-type": "application/json"}

HOSTILE = ["..\\x", "\\\\host\\share", "../../etc/passwd", "BTCUSDT\x00", "BTCUSDT\n", "btcusdt", "", " BTCUSDT", "C:\\Windows",
           "A" * 5000, "BTCUSDT/../ETHUSDT", "*", "BTCUSDT\\..\\..\\x"]
HOSTILE_TF = ["..\\1h", "1H", "", "1h\x00", "../1h", "\\\\h\\s", "7m", "1h ", "A" * 500]


@pytest.fixture
def no_disk(monkeypatch):
    """Любое обращение к parquet/stat данных = провал теста: враждебное имя не должно дойти до файловой системы."""
    touched = []
    real_read = pd.read_parquet

    def spy(path, *a, **k):
        touched.append(str(path))
        return real_read(path, *a, **k)
    monkeypatch.setattr(pd, "read_parquet", spy)
    yield touched
    assert touched == [], touched


# ---------------------------------------------------------------- W2: схемы и пути
def test_schema_symbol_literal_matches_symbols():
    from typing import get_args
    assert get_args(schemas.Symbol) == schemas.SYMBOLS
    assert service.SYMBOLS == schemas.SYMBOLS


def test_all_request_models_validate_symbol_and_tf():
    for cls, kw in ((schemas.BacktestReq, {"strategy": "x"}), (schemas.OptimizeReq, {"strategy": "x"}), (schemas.LbItemReq, {"strategy": "x"}),
                    (schemas.CachedReq, {}), (schemas.SweepReq, {"strategy": "x", "x": {"name": "a", "min": 1, "max": 2}})):
        for s in HOSTILE:
            with pytest.raises(Exception):
                cls(symbol=s, **kw)
        for tf in HOSTILE_TF:
            with pytest.raises(Exception):
                cls(tf=tf, **kw)
    with pytest.raises(Exception):
        schemas.FetchReq(symbol="..\\x", tf="1h")
    with pytest.raises(Exception):
        schemas.FetchReq(symbol="BTCUSDT", tf="..\\1h")
    with pytest.raises(Exception):
        schemas.LegReq(strategy="x", tf="..\\1h")
    for sym in schemas.SYMBOLS:
        assert schemas.CachedReq(symbol=sym, tf="1w").tf == "1w"


@pytest.mark.parametrize("sym", HOSTILE)
def test_post_endpoints_reject_hostile_symbol(sym, no_disk):
    legs = [{"strategy": "EMA cross", "tf": "1h"}]
    bodies = {
        "/api/backtest": {"strategy": "EMA cross", "symbol": sym},
        "/api/sweep": {"strategy": "EMA cross", "symbol": sym, "x": {"name": "fast", "min": 5, "max": 9, "n": 2}},
        "/api/portfolio": {"legs": legs, "symbol": sym},
        "/api/optimize": {"strategy": "EMA cross", "symbol": sym},
        "/api/leaderboard/cached": {"symbol": sym},
        "/api/leaderboard/item": {"strategy": "EMA cross", "symbol": sym},
        "/api/data/update": {"symbol": sym, "tf": "1h"},
        "/api/data/fetch": {"symbol": sym, "tf": "1h"},
    }
    for url, body in bodies.items():
        r = client.post(url, json=body)
        assert r.status_code == 422, (url, sym, r.status_code, r.text[:200])


@pytest.mark.parametrize("tf", HOSTILE_TF)
def test_post_endpoints_reject_hostile_tf(tf, no_disk):
    bodies = {
        "/api/backtest": {"strategy": "EMA cross", "tf": tf},
        "/api/sweep": {"strategy": "EMA cross", "tf": tf, "x": {"name": "fast", "min": 5, "max": 9, "n": 2}},
        "/api/portfolio": {"legs": [{"strategy": "EMA cross", "tf": tf}]},
        "/api/optimize": {"strategy": "EMA cross", "tf": tf},
        "/api/leaderboard/cached": {"tf": tf},
        "/api/leaderboard/item": {"strategy": "EMA cross", "tf": tf},
        "/api/data/update": {"symbol": "BTCUSDT", "tf": tf},
        "/api/data/fetch": {"symbol": "BTCUSDT", "tf": tf},
    }
    for url, body in bodies.items():
        r = client.post(url, json=body)
        assert r.status_code == 422, (url, tf, r.status_code, r.text[:200])


@pytest.mark.parametrize("sym", HOSTILE)
def test_get_endpoints_reject_hostile_symbol(sym, no_disk):
    for url, extra in (("/api/leaderboard/plan", {}), ("/api/data/status", {}), ("/api/walkforward", {}), ("/api/walkforward/windows", {}),
                       ("/api/walkforward/curve", {"tf": "1h", "strategy": "s", "variant": "v"})):
        r = client.get(url, params={"symbol": sym, **extra})
        assert r.status_code == 422, (url, sym, r.status_code, r.text[:200])


@pytest.mark.parametrize("tf", HOSTILE_TF)
def test_get_endpoints_reject_hostile_tf(tf, no_disk):
    for url, extra in (("/api/leaderboard/plan", {}), ("/api/data/status", {}),
                       ("/api/walkforward/curve", {"strategy": "s", "variant": "v"})):
        r = client.get(url, params={"tf": tf, **extra})
        assert r.status_code == 422, (url, tf, r.status_code, r.text[:200])


def test_valid_values_still_work_on_every_endpoint(synth_market):
    assert client.get("/api/leaderboard/plan", params={"symbol": "BTCUSDT", "tf": "4h"}).status_code == 200
    assert client.get("/api/data/status", params={"symbol": "BTCUSDT", "tf": "1h"}).status_code == 200
    assert client.post("/api/backtest", json={"strategy": "EMA cross", "tf": "1h", "days": 60}).status_code == 200
    assert client.post("/api/leaderboard/cached", json={"tf": "1h"}).status_code == 200
    assert client.post("/api/leaderboard/item", json={"tf": "1h", "strategy": "EMA cross", "days": 60}).status_code == 200


@pytest.mark.parametrize("sym", HOSTILE)
def test_inner_path_builders_reject_hostile_values_directly(sym, no_disk):
    """Защита второго рубежа: даже минуя схемы (бот, скрипты) враждебное имя не попадает в путь и в stat/read_parquet."""
    with pytest.raises(ValueError):
        service.data_fingerprint(sym, "1h", "EMA cross")
    with pytest.raises(ValueError):
        service.data_sig(sym, "1h")
    with pytest.raises(ValueError):
        service._files(sym, "1h")
    with pytest.raises(ValueError):
        service.get_df(sym, "1h")
    with pytest.raises(ValueError):
        service.get_df_lite(sym, "1h")
    with pytest.raises(ValueError):
        service.data_status(sym, "1h")
    with pytest.raises(ValueError):
        service.lb_plan(sym, "1h")
    with pytest.raises(ValueError):
        klines._path(sym, "1h")
    with pytest.raises(ValueError):
        derivs.funding(sym, 10, False)


@pytest.mark.parametrize("tf", HOSTILE_TF)
def test_inner_path_builders_reject_hostile_tf(tf, no_disk):
    for fn in (lambda: service.data_fingerprint("BTCUSDT", tf, "EMA cross"), lambda: service.data_sig("BTCUSDT", tf),
               lambda: service._files("BTCUSDT", tf), lambda: klines._path("BTCUSDT", tf), lambda: service.get_df("BTCUSDT", tf)):
        with pytest.raises(ValueError):
            fn()


def test_lb_item_and_fingerprint_never_see_hostile_values(synth_market, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "data_fingerprint", lambda *a, **k: calls.append(a) or "fp")
    for sym in HOSTILE:
        r = client.post("/api/leaderboard/item", json={"strategy": "EMA cross", "symbol": sym})
        assert r.status_code == 422
    assert calls == []


# ---------------------------------------------------------------- W3: middleware
def _post(headers=None, **kw):
    return client.post("/api/backtest", headers={**JSON, **(headers or {})}, content=kw.pop("content", b'{"strategy":"nope"}'))


@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site", " SAME-SITE "])
def test_sec_fetch_site_cross_and_same_site_are_forbidden(site):
    for url in ("/api/backtest", "/api/data/update", "/api/data/fetch", "/api/bot/proposals", "/api/leaderboard/cached"):
        r = client.post(url, content=b"{}", headers={**JSON, "Sec-Fetch-Site": site})
        assert r.status_code == 403, (url, site)
        assert r.json() == {"detail": "cross-site запрещён"}


@pytest.mark.parametrize("site", ["same-origin", "none", ""])
def test_sec_fetch_site_same_origin_and_none_pass(site):
    assert _post({"Sec-Fetch-Site": site}).status_code == 422                      # дошло до валидации стратегии


def test_sec_fetch_site_only_checked_for_state_changing_methods():
    assert client.get("/api/meta", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_post_body_requires_json_content_type():
    r = client.post("/api/backtest", content=b'{"strategy":"nope"}', headers={"content-type": "text/plain"})
    assert r.status_code == 415 and "application/json" in r.json()["detail"]
    r = client.post("/api/backtest", content=b"strategy=nope", headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    r = client.post("/api/backtest", content=b'{"strategy":"nope"}')                # httpx без content-type
    assert r.status_code == 415
    assert _post({"content-type": "application/json; charset=utf-8"}).status_code == 422
    assert _post({"content-type": "Application/JSON"}).status_code == 422
    assert client.post("/api/backtest", json={"strategy": "nope"}).status_code == 422


def test_post_without_body_is_not_blocked_by_content_type():
    r = client.post("/api/backtest")                                                 # тела нет: 422 «поле обязательно», не 415
    assert r.status_code == 422


def test_origin_guard_still_works_with_new_checks():
    assert _post({"Origin": "http://evil.example"}).status_code == 403
    assert _post({"Origin": "http://testserver"}).status_code == 422
    assert _post({"Origin": "null"}).status_code == 403


def test_security_headers_on_every_kind_of_response(synth_market):
    expect = {"x-frame-options": "DENY", "content-security-policy": "frame-ancestors 'none'",
              "x-content-type-options": "nosniff", "referrer-policy": "no-referrer"}
    responses = [
        client.get("/api/meta"),                                                     # 200 JSON
        client.get("/"),                                                             # статика / заглушка
        client.get("/definitely/missing.js"),                                        # 404
        client.post("/api/backtest", json={"strategy": "x", "days": 1}),             # 422
        _post({"Origin": "http://evil.example"}),                                    # 403 от middleware
        client.post("/api/backtest", content=b"{}", headers={"content-type": "text/plain"}),     # 415
        client.get("/api/meta", headers={"Host": "evil.example"}),                   # 400 от TrustedHost
        client.get("/api/meta", headers={"Accept-Encoding": "gzip"}),
    ]
    for r in responses:
        for k, v in expect.items():
            assert r.headers.get(k) == v, (r.request.url, r.status_code, k, dict(r.headers))
    assert [r.status_code for r in responses][:6] == [200, responses[1].status_code, 404, 422, 403, 415]
    assert "script-src" not in "".join(r.headers.get("content-security-policy", "") for r in responses)   # SPA не ломаем


def test_security_headers_do_not_override_existing_ones():
    from tradebot.web.security import SecurityHeaders

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"x-frame-options", b"SAMEORIGIN")]})
        await send({"type": "http.response.body", "body": b""})
    seen = []

    async def send(msg):
        seen.append(msg)
    import asyncio
    asyncio.run(SecurityHeaders(inner)({"type": "http", "method": "GET", "headers": []}, None, send))
    hdr = dict(seen[0]["headers"])
    assert hdr[b"x-frame-options"] == b"SAMEORIGIN" and hdr[b"x-content-type-options"] == b"nosniff"


# ---------------------------------------------------------------- W12: Host testserver только под тестами
def test_testserver_host_allowed_under_tests():
    assert os.environ.get("TRADEBOT_ALLOW_TESTSERVER") == "1" and "testserver" in api_mod.ALLOWED_HOSTS
    assert client.get("/api/meta").status_code == 200


def test_testserver_host_rejected_without_flag():
    code = (
        "import os; os.environ.pop('TRADEBOT_ALLOW_TESTSERVER', None)\n"
        "from tradebot.web import api\n"
        "assert 'testserver' not in api.ALLOWED_HOSTS, api.ALLOWED_HOSTS\n"
        "assert set(api.ALLOWED_HOSTS) == {'127.0.0.1', 'localhost', '[::1]'}\n"
        "from fastapi.testclient import TestClient\n"
        "c = TestClient(api.app)\n"
        "assert c.get('/api/meta').status_code == 400\n"                                       # Host: testserver
        "assert c.get('/api/meta', headers={'Host': '127.0.0.1:8000'}).status_code == 200\n"
        "assert c.get('/api/meta', headers={'Host': 'localhost'}).status_code == 200\n"
        "print('ok')\n")
    env = {k: v for k, v in os.environ.items() if k != "TRADEBOT_ALLOW_TESTSERVER"}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-800:]


# ---------------------------------------------------------------- W13: статика и URI БД
@pytest.mark.parametrize("path", ["/a%00b", "/%00", "/x%00/../index.html", "/..%5c..%5cWindows", "/%2e%2e/%2e%2e/etc/passwd",
                                  "/" + "a" * 3000, "/C:/Windows/win.ini", "/a%3Fb", "/con", "/nul.txt"])
def test_static_route_never_500s(path):
    r = client.get(path)
    assert r.status_code in (200, 404), (path, r.status_code)
    assert r.headers["x-frame-options"] == "DENY"


def test_static_serves_only_inside_dist(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>spa</h1>")
    (tmp_path / "secret.txt").write_text("nope")
    monkeypatch.setattr(api_mod, "WEB_DIST", dist)
    assert "spa" in client.get("/").text
    assert client.get("/index.html").status_code == 200
    assert client.get("/../secret.txt").status_code in (200, 404) and "nope" not in client.get("/../secret.txt").text
    assert client.get("/%2e%2e/secret.txt").status_code == 404
    assert client.get("/nul%00.txt").status_code == 404


@pytest.mark.parametrize("name", ["plain", "with space", "hash#tag", "per%cent", "кириллица", "a&b=c", "semi;colon"])
def test_db_uri_quotes_the_path(tmp_path, name):
    import sqlite3
    d = tmp_path / name
    d.mkdir()
    p = d / "bot.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE t(x)")
    c.execute("INSERT INTO t VALUES (42)")
    c.commit()
    c.close()
    uri = stats_io._db_uri(p)
    assert uri.startswith("file:") and uri.endswith("?mode=ro") and "?" not in uri[:-8] and "#" not in uri
    ro = sqlite3.connect(uri, uri=True)
    assert ro.execute("SELECT x FROM t").fetchone() == (42,)
    with pytest.raises(sqlite3.OperationalError):                                  # режим read-only сохранён
        ro.execute("INSERT INTO t VALUES (1)")
    ro.close()


def test_db_uri_encodes_query_and_fragment_characters():
    """«?» и «#» нельзя положить в имя папки на Windows, но на других ОС можно: они не должны обрезать путь URI."""
    from pathlib import Path
    uri = stats_io._db_uri(Path("/data/a?b#c d/bot.db"))
    assert uri == "file:/data/a%3Fb%23c%20d/bot.db?mode=ro"
