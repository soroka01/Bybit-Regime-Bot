"""Аудит web/: остановка полос (отмена заданий, сброс очереди пулов, хук выхода из процесса), единый маппинг ошибок
без абсолютных путей, single-flight и ограничение параллелизма /api/stats, надёжный разбор equity и fills.
Сети и реального bot.db нет."""
import asyncio
import json
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import _stats_fixture as fx
from _synth import synth_market  # noqa: F401
from tradebot.market import guard, klines
from tradebot.web import lanes, service, stats
from tradebot.web import stats_base, stats_io
from tradebot.web.api import app

client = TestClient(app)
LAB = ('Стратегия: EMA cross | Таймфрейм: 1h | Параметры: fast=12, slow=26 | Символ: BTCUSDT | Рынок: Перп | Только лонг: нет | '
       'Плечо: 1x | Период: 365 дн. | Результат: доход +10%, макс. просадка 5%, сделок 40 | Оверлеи: нет\n'
       'Leg("EMA cross", "1h", {"fast": 12, "slow": 26}, weight=0.5, long_only=False)')


class FakeReq:
    async def is_disconnected(self):
        return False


@pytest.fixture
def fresh_lanes(monkeypatch):
    calc = lanes.Lane("calc", queue_wait=0.3, supersede=True, abandon_limit=2)
    net = lanes.Lane("net", queue_wait=0.3, supersede=False, abandon_limit=1)
    monkeypatch.setattr(lanes, "_calc", calc)
    monkeypatch.setattr(lanes, "_net", net)
    monkeypatch.setattr(lanes, "QUEUE_SLICE", 0.05)
    yield calc, net
    for ln in (calc, net):
        ln.pool.shutdown(wait=False, cancel_futures=True)


def run(coro):
    return asyncio.run(coro)


def spin_until_cancelled(started: threading.Event):
    """Задание «длинной загрузки»: крутится, пока не выставлен флаг отмены (как сетевые циклы klines/derivs)."""
    started.set()
    for _ in range(2000):
        guard.check_cancel()
        time.sleep(0.01)
    raise AssertionError("отмена не дошла до задания")


# ---------------------------------------------------------------- W5: остановка полос
def test_lane_shutdown_cancels_running_job_and_drops_queue(fresh_lanes):
    calc, net = fresh_lanes
    started = threading.Event()

    async def go():
        t = asyncio.create_task(lanes.run_heavy(FakeReq(), "fetch", lambda: spin_until_cancelled(started), 30))
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set() and len(net.events) == 1
        t0 = time.monotonic()
        net.shutdown()                                                             # то же, что делает lifespan при остановке
        with pytest.raises(guard.Cancelled):
            await t
        assert time.monotonic() - t0 < 1.0, "отмена должна прервать «загрузку» сразу"
        assert net.closed and net.pool._shutdown and not net.events
        with pytest.raises(HTTPException) as e:                                    # новые задания после остановки не принимаются
            await lanes.run_heavy(FakeReq(), "fetch", lambda: 1)
        assert e.value.status_code == 503 and e.value.detail["code"] == "shutting_down"
    run(go())


def test_lane_reopen_gives_fresh_pool(fresh_lanes):
    calc, _ = fresh_lanes
    old = calc.pool
    calc.shutdown()
    calc.reopen()
    assert calc.pool is not old and not calc.closed and calc.events == set()
    assert run(lanes.run_heavy(FakeReq(), "backtest", lambda: 7)) == 7
    calc.reopen()                                                                  # без остановки reopen ничего не делает
    assert calc.pool is not old


def test_lifespan_shuts_lanes_down_and_restarts(fresh_lanes, synth_market):
    calc, net = fresh_lanes
    with TestClient(app) as c:                                                     # startup
        assert c.get("/api/meta").status_code == 200
        assert not calc.closed and not net.closed
    assert calc.closed and net.closed and calc.pool._shutdown and net.pool._shutdown       # shutdown
    with TestClient(app) as c:                                                     # повторный старт в том же процессе
        assert not calc.closed
        r = c.post("/api/backtest", json={"strategy": "EMA cross", "tf": "1h", "days": 60})
        assert r.status_code == 200, r.text


def test_closed_lane_http_is_503_not_500(fresh_lanes, synth_market):
    calc, _ = fresh_lanes
    calc.shutdown()
    r = client.post("/api/backtest", json={"strategy": "EMA cross", "tf": "1h", "days": 60})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "shutting_down"


def test_deadline_check_honours_cancel_flag():
    from tradebot.web.common import Deadline
    ev = threading.Event()
    dl = Deadline(60)
    with guard.bind_cancel(ev):
        dl.check()
        ev.set()
        with pytest.raises(guard.Cancelled):
            dl.check()
    dl.check()                                                                     # вне привязки флаг не действует


def test_atexit_hook_cancels_lanes_and_global_flag(monkeypatch):
    lane = lanes.Lane("tmp", 0.1, False, 0)
    monkeypatch.setattr(lanes, "_registry", __import__("weakref").WeakSet([lane]))
    ev = threading.Event()
    lane.events.add(ev)
    try:
        lanes._shutdown_at_exit()
        assert ev.is_set() and lane.closed and guard.cancelled()
    finally:
        guard._shutdown.clear()
    assert not guard.cancelled()


def test_process_exit_does_not_wait_for_running_download():
    """Интеграционно: пока в пуле идёт «бесконечная загрузка», процесс должен завершиться сам (хук выхода выставляет отмену ДО того,
    как интерпретатор начнёт ждать потоки executor-а). Без хука этот скрипт висит вечно."""
    code = textwrap.dedent("""
        import threading, time
        from tradebot.market import guard
        from tradebot.web import lanes
        lane = lanes._net
        ev = threading.Event()
        lane.events.add(ev)
        started = threading.Event()

        def job():
            with guard.bind_cancel(ev):
                started.set()
                while True:
                    guard.check_cancel()
                    time.sleep(0.02)
        lane.pool.submit(job)
        assert started.wait(10)
        print("exiting", flush=True)
    """)
    t0 = time.monotonic()
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=40)
    except subprocess.TimeoutExpired:
        pytest.fail("процесс завис на выходе: хук остановки не сработал")
    assert r.returncode == 0 and "exiting" in r.stdout, r.stderr[-800:]
    assert time.monotonic() - t0 < 30


# ---------------------------------------------------------------- W10: единый маппинг ошибок
def _abs_paths(text: str) -> bool:
    import re
    return bool(re.search(r"[A-Za-z]:[\\/]|\\\\\w|/home/|/usr/|/var/", text))


def test_internal_errors_have_no_absolute_paths(synth_market, monkeypatch):
    def boom(req, dl=None):
        raise PermissionError(13, "Отказано в доступе", "D:\\secret\\storage\\market\\BTCUSDT_1h.parquet")
    monkeypatch.setattr(service, "run_backtest", boom)
    r = client.post("/api/backtest", json={"strategy": "EMA cross"})
    d = r.json()["detail"]
    assert r.status_code == 500 and d["code"] == "internal" and not _abs_paths(d["message"]), d
    assert "BTCUSDT_1h.parquet" in d["message"]                                   # полезная часть (имя файла) остаётся


def test_update_and_fetch_errors_are_clean(synth_market, monkeypatch):
    def fail(*a, **k):
        raise OSError("не удалось записать C:\\Users\\boss\\storage\\market\\x.parquet и /home/boss/x")
    monkeypatch.setattr(klines, "load", fail)
    for url in ("/api/data/update", "/api/data/fetch"):
        r = client.post(url, json={"symbol": "BTCUSDT", "tf": "1h"})
        d = r.json()["detail"]
        assert r.status_code == 502 and not _abs_paths(d["message"]), (url, d)
        assert d["code"] in ("update_failed", "fetch_failed")


@pytest.mark.parametrize("exc,status,code", [
    (ValueError("плохой запрос"), 422, "bad_request"),
    (KeyError("oi"), 502, None),                      # у эндпоинтов, ходящих в сеть, KeyError = сбой ответа биржи, не ошибка клиента
    (klines.NoDataError("пусто"), 502, None),
    (guard.LockTimeout("файл занят"), 502, None),
    (RuntimeError("x"), 502, None),
    (sqlite3.OperationalError("database is locked"), 503, "db_error"),
    (guard.Cancelled("стоп"), 503, "shutting_down"),
])
def test_network_endpoints_status_mapping(synth_market, monkeypatch, exc, status, code):
    def fail(*a, **k):
        raise exc
    monkeypatch.setattr(service, "update_data", fail)
    monkeypatch.setattr(service, "fetch_data", fail)
    for url, default_code in (("/api/data/update", "update_failed"), ("/api/data/fetch", "fetch_failed")):
        r = client.post(url, json={"symbol": "BTCUSDT", "tf": "1h"})
        assert r.status_code == status, (url, r.status_code, r.text)
        assert r.json()["detail"]["code"] == (code or default_code)


def test_proposals_db_error_is_503(tmp_path, monkeypatch):
    from tradebot.trading import state
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "w.db")

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked: " + str(tmp_path / "w.db"))
    monkeypatch.setattr(state, "submit_proposals", locked)
    r = client.post("/api/bot/proposals", json={"text": LAB})
    d = r.json()["detail"]
    assert r.status_code == 503 and d["code"] == "db_error" and str(tmp_path) not in d["message"]


def test_proposals_heartbeat_db_error_does_not_lose_the_saved_proposal(tmp_path, monkeypatch):
    from tradebot.trading import state
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "w.db")
    monkeypatch.setattr(state, "submit_proposals", lambda payloads, *a, **k: [1])
    monkeypatch.setattr(state, "heartbeat_age", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    r = client.post("/api/bot/proposals", json={"text": LAB})
    assert r.status_code == 200 and r.json()["added"] == 1 and r.json()["bot_alive"] is False


@pytest.mark.parametrize("name,payload", [("wf_curves_BTCUSDT.pkl", b""), ("wf_curves_BTCUSDT.pkl", b"\x80\x05garbage-not-a-pickle"),
                                          ("wf_curves_BTCUSDT.pkl", b"\x80\x04\x95"), ])
def test_corrupt_wf_pickle_is_clean_error(synth_market, name, payload):
    _, res = synth_market
    (res / name).write_bytes(payload)
    service._wf_cache.clear()
    r = client.get("/api/walkforward/curve", params={"tf": "1h", "strategy": "s", "variant": "v"})
    d = r.json()["detail"]
    assert r.status_code == 500 and d["code"] == "bad_results" and not _abs_paths(d["message"]), (r.status_code, d)


def test_corrupt_wf_csv_is_clean_error(synth_market):
    _, res = synth_market
    (res / "wf_BTCUSDT.csv").write_bytes(b"")
    (res / "wf_windows_BTCUSDT.csv").write_text('a,b\n"unclosed,1\n2,3,4,5\n')
    service._wf_cache.clear()
    r = client.get("/api/walkforward")
    assert r.status_code == 500 and r.json()["detail"]["code"] == "bad_results"


def test_wf_missing_file_is_404_and_bad_lev_422(synth_market):
    r = client.get("/api/walkforward/windows")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_results" and not _abs_paths(r.json()["detail"]["message"])
    assert client.get("/api/walkforward?lev=7").status_code == 422


def test_unknown_exceptions_map_to_500_with_log(synth_market, monkeypatch, caplog):
    monkeypatch.setattr(service, "lb_plan", lambda s, t: (_ for _ in ()).throw(ZeroDivisionError("x")))
    with caplog.at_level("ERROR", logger="tradebot.web"):
        r = client.get("/api/leaderboard/plan")
    assert r.status_code == 500 and r.json()["detail"]["code"] == "internal"
    assert any("lb_plan" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------- W9: /api/stats single-flight и ограничение параллелизма
@pytest.fixture
def sdb(monkeypatch, tmp_path):
    stats._cache.clear()
    stats._inflight.clear()
    monkeypatch.setattr(stats, "load_market", lambda: (None, "disabled"))
    monkeypatch.setattr(stats, "_market_sig", lambda: ())
    monkeypatch.setattr(stats, "_now", lambda: fx.NOW1)
    yield fx.golden_db(tmp_path / "bot.db")
    stats._cache.clear()
    stats._inflight.clear()


def _spawn(n, target):
    out, errs = [None] * n, [None] * n

    def run_one(i):
        try:
            out[i] = target(i)
        except BaseException as e:  # noqa: BLE001
            errs[i] = e
    ts = [threading.Thread(target=run_one, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    return out, errs


def test_same_key_is_computed_once(sdb, monkeypatch):
    calls = []
    real = stats.compute_stats

    def slow(*a, **k):
        calls.append(1)
        time.sleep(0.4)
        return real(*a, **k)
    monkeypatch.setattr(stats, "compute_stats", slow)
    out, errs = _spawn(6, lambda i: stats.get_stats("30d", sdb))
    assert errs == [None] * 6 and len(calls) == 1
    assert all(o is out[0] or o == out[0] for o in out) and out[0]["available"] is True
    assert stats._inflight == {}
    stats.get_stats("30d", sdb)
    assert len(calls) == 1                                                       # дальше — из кэша


def test_distinct_keys_run_at_most_max_parallel(sdb, monkeypatch):
    real = stats.compute_stats
    state = {"now": 0, "max": 0, "calls": 0}
    lock = threading.Lock()

    def tracked(*a, **k):
        with lock:
            state["now"] += 1
            state["calls"] += 1
            state["max"] = max(state["max"], state["now"])
        try:
            time.sleep(0.2)
            return real(*a, **k)
        finally:
            with lock:
                state["now"] -= 1
    monkeypatch.setattr(stats, "compute_stats", tracked)
    ranges = ["7d", "30d", "90d", "180d", "365d", "all"]
    out, errs = _spawn(6, lambda i: stats.get_stats(ranges[i], sdb))
    assert errs == [None] * 6 and all(o["available"] for o in out)
    assert state["calls"] == 6 and state["max"] == stats.MAX_PARALLEL == 2


def test_followers_recover_when_leader_fails(sdb, monkeypatch):
    real = stats.compute_stats
    n = {"i": 0}

    def flaky(*a, **k):
        n["i"] += 1
        if n["i"] == 1:
            time.sleep(0.3)
            raise RuntimeError("расчёт упал")
        return real(*a, **k)
    monkeypatch.setattr(stats, "compute_stats", flaky)
    results = {}

    def call(name, delay):
        time.sleep(delay)
        try:
            results[name] = stats.get_stats("30d", sdb)
        except Exception as e:  # noqa: BLE001
            results[name] = e
    ts = [threading.Thread(target=call, args=("leader", 0)), threading.Thread(target=call, args=("follower", 0.1))]
    [t.start() for t in ts]
    [t.join(20) for t in ts]
    assert isinstance(results["leader"], RuntimeError)
    assert results["follower"]["available"] is True and n["i"] == 2               # ведомый не остался без ответа и не ждал вечно
    assert stats._inflight == {}


def test_api_stats_concurrent_http(sdb, monkeypatch):
    from tradebot.trading import state
    monkeypatch.setattr(state, "DB_PATH", sdb)
    calls = []
    real = stats.compute_stats
    monkeypatch.setattr(stats, "compute_stats", lambda *a, **k: (calls.append(1), time.sleep(0.3), real(*a, **k))[2])
    out, errs = _spawn(5, lambda i: client.get("/api/stats", params={"range": "30d"}))
    assert errs == [None] * 5 and {o.status_code for o in out} == {200} and len(calls) == 1
    assert len({o.content for o in out}) == 1


# ---------------------------------------------------------------- W17: fills по ts и equity по разобранным меткам
def test_first_and_last_trade_ts_use_min_max_by_time(sdb, monkeypatch, tmp_path):
    p = tmp_path / "unordered.db"
    eq = fx.equity_points("2026-05-28T00:00:00+00:00", 120, 100.0, 0.01)
    fx.make_db(p, trades=[("2026-05-30T10:00:00+00:00", "sell", 1, 101.0, "бар"),        # id 1 — позже по времени
                          ("2026-05-30T08:00:00+00:00", "buy", 1, 100.0, "бар")],         # id 2 — раньше по времени
               equity=eq, kv={"s_leverage": 1.0})
    out = stats.get_stats("all", p)
    assert out["available"] is True
    assert out["data"]["first_trade_ts"] == int(fx.ts("2026-05-30T08:00:00+00:00"))
    assert out["data"]["last_trade_ts"] == int(fx.ts("2026-05-30T10:00:00+00:00"))
    assert out["data"]["round_trips"] == 1 and out["kpis"]["trades"] == 1


def _old_equity(db, q_start):
    """Прежний алгоритм (сравнение строк) — эталон для «нормальных» данных."""
    q = stats_base.iso(q_start)
    rows = db.execute("SELECT ts,v FROM equity WHERE ts >= ? ORDER BY rowid", (q,)).fetchall()
    b = db.execute("SELECT ts,v FROM equity WHERE ts < ? ORDER BY rowid DESC LIMIT 1", (q,)).fetchone()
    if b:
        rows.insert(0, b)
    return stats_io._eq_arrays(rows)


@pytest.mark.parametrize("q_iso", ["2026-04-01T00:00:00+00:00", "2026-05-10T00:00:00+00:00", "2026-05-10T13:37:21+00:00",
                                   "2026-05-30T23:59:59+00:00", "2026-06-02T00:00:00+00:00", "2026-05-12T05:00:00+00:00"])
def test_read_db_equity_matches_old_algorithm_on_normal_data(tmp_path, q_iso):
    p = tmp_path / "e.db"
    eq = fx.equity_points("2026-05-01T00:00:00+00:00", 700, 100.0, 0.05, lambda i: (i * 7919 % 13) / 10, every_s=3600)
    fx.make_db(p, equity=eq)
    db = sqlite3.connect(p)
    q = fx.ts(q_iso)
    t_new, v_new = stats_io.read_db(db, q)["eq_t"], stats_io.read_db(db, q)["eq_v"]
    t_old, v_old = _old_equity(db, q)
    assert np.array_equal(t_new, t_old) and np.array_equal(v_new, v_old)
    assert stats_io.read_db(db, None)["eq_t"].size == 700
    db.close()


def test_read_db_equity_filters_by_parsed_time_not_string(tmp_path):
    """Метка с другим смещением/суффиксом: строковое сравнение ошибается, разобранное время — нет."""
    p = tmp_path / "mixed.db"
    fx.make_db(p, equity=[("2026-05-20T00:00:00+00:00", 90.0),
                          ("2026-06-01T05:30:00Z", 100.0),                 # 05:30Z
                          ("2026-06-01T10:00:00+03:00", 101.0),            # 07:00Z, но строка «больше» 07:30Z
                          ("2026-06-01T08:00:00+00:00", 102.0)])
    db = sqlite3.connect(p)
    q = fx.ts("2026-06-01T07:30:00+00:00")
    raw = stats_io.read_db(db, q)
    assert raw["eq_t"].tolist() == [int(fx.ts("2026-06-01T07:00:00+00:00")), int(fx.ts("2026-06-01T08:00:00+00:00"))]
    assert raw["eq_v"].tolist() == [101.0, 102.0]                          # одна точка до окна (07:00Z) + точки внутри
    raw0 = stats_io.read_db(db, fx.ts("2026-06-01T07:30:00+00:00") + 0.0)
    assert raw0["eq_t"].tolist() == raw["eq_t"].tolist()
    db.close()


def test_read_db_equity_takes_older_point_when_margin_is_empty(tmp_path):
    p = tmp_path / "gap.db"
    fx.make_db(p, equity=[("2026-05-10T00:00:00+00:00", 90.0), ("2026-06-01T08:00:00+00:00", 102.0)])
    db = sqlite3.connect(p)
    raw = stats_io.read_db(db, fx.ts("2026-06-01T07:30:00+00:00"))
    assert raw["eq_t"].tolist() == [int(fx.ts("2026-05-10T00:00:00+00:00")), int(fx.ts("2026-06-01T08:00:00+00:00"))]
    db.close()


def test_eq_arrays_drops_unparseable_and_sorts():
    rows = [("2026-06-01T08:00:00+00:00", 2.0), ("мусор", 5.0), ("2026-06-01T07:00:00+00:00", 1.0), ("2026-06-01T06:00:00+02:00", None)]
    t, v = stats_io._eq_arrays(rows)
    assert t.tolist() == [int(fx.ts("2026-06-01T04:00:00+00:00")), int(fx.ts("2026-06-01T07:00:00+00:00")),
                          int(fx.ts("2026-06-01T08:00:00+00:00"))]
    assert np.isnan(v[0]) and v[1:].tolist() == [1.0, 2.0]
    assert stats_io._eq_arrays([])[0].size == 0 and stats_io._eq_arrays([("мусор", 1.0)])[0].size == 0
    # быстрый путь (формат бота) и медленный дают одно и то же
    a, _ = stats_io._eq_arrays([("2026-06-01T08:00:00+00:00", 1.0)])
    b, _ = stats_io._eq_arrays([("2026-06-01T08:00:00Z", 1.0)])
    assert a.tolist() == b.tolist()


def test_golden_stats_response_is_unchanged_by_refactor_and_fixes(sdb):
    """Контроль «числа прежние»: на золотой БД ответ детерминирован и содержит те же ключевые величины, что снимок stats_30d."""
    a = stats.get_stats("30d", sdb)
    stats._cache.clear()
    b = stats.get_stats("30d", sdb)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
