"""run_heavy: очередь, вытеснение, таймаут, исключения, отключение клиента, watchdog, независимость полос."""
import asyncio
import threading
import time

import pytest
from fastapi import HTTPException

from tradebot.web import api as api_mod, lanes as lanes_mod, service


class FakeReq:
    def __init__(self, disconnected=False):
        self.disconnected = disconnected

    async def is_disconnected(self):
        return self.disconnected


@pytest.fixture
def lanes(monkeypatch):
    """Свежие полосы с короткими таймингами (каждый тест — свой цикл событий и свои семафоры)."""
    calc = lanes_mod.Lane("calc", queue_wait=0.3, supersede=True, abandon_limit=2)
    net = lanes_mod.Lane("net", queue_wait=0.3, supersede=False, abandon_limit=1)
    monkeypatch.setattr(lanes_mod, "_calc", calc)
    monkeypatch.setattr(lanes_mod, "_net", net)
    monkeypatch.setattr(lanes_mod, "QUEUE_SLICE", 0.05)
    yield calc, net
    calc.pool.shutdown(wait=False, cancel_futures=True)
    net.pool.shutdown(wait=False, cancel_futures=True)


class Blocker:
    """fn, которая стоит, пока не отпустят событие."""
    def __init__(self, result="ok"):
        self.started, self.release, self.result = threading.Event(), threading.Event(), result

    def __call__(self):
        self.started.set()
        assert self.release.wait(10), "тест не отпустил блокировку"
        return self.result


def run(coro):
    return asyncio.run(coro)


async def wait_started(b: Blocker):
    for _ in range(200):
        if b.started.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("fn не стартовала")


def detail_of(exc: pytest.ExceptionInfo):
    assert isinstance(exc.value, HTTPException)
    return exc.value.status_code, exc.value.detail


def test_runs_and_returns_value(lanes):
    assert run(lanes_mod.run_heavy(FakeReq(), "backtest", lambda: 42)) == 42


def test_busy_returns_429_with_wait_info(lanes):
    async def go():
        b = Blocker()
        t1 = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "backtest", b))
        await wait_started(b)
        t0 = time.monotonic()
        with pytest.raises(HTTPException) as e:
            await lanes_mod.run_heavy(FakeReq(), "sweep", lambda: 1)             # другой вид — не вытесняет, просто ждёт
        status, d = detail_of(e)
        assert status == 429 and d["code"] == "busy"
        assert d["queue_wait_s"] == 0.3 and 0.2 <= d["waited_s"] <= 10 and time.monotonic() - t0 < 15
        b.release.set()
        assert await t1 == "ok"
    run(go())


def test_superseded_older_queued_request(lanes, monkeypatch):
    calc, _ = lanes
    calc.queue_wait = 5.0

    async def go():
        b = Blocker()
        t1 = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "optimize", b))
        await wait_started(b)
        t2 = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "optimize", lambda: "second"))
        await asyncio.sleep(0.05)
        t3 = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "optimize", lambda: "third"))
        with pytest.raises(HTTPException) as e:          # второй вытеснен третьим ещё в очереди, не дожидаясь слота
            await asyncio.wait_for(t2, 2)
        assert detail_of(e) == (429, {"code": "superseded", "message": "запрос вытеснен более новым"})
        b.release.set()
        assert await t1 == "ok" and await t3 == "third"
    run(go())


def test_superseded_after_acquire(lanes):
    """Запрос получил слот, но к этому моменту уже есть более новый того же вида."""
    calc, _ = lanes

    async def go():
        sem = calc.semaphore()
        await sem.acquire()                                # занимаем слот вручную
        calc.queue_wait = 2.0
        t_old = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "k", lambda: "old"))
        await asyncio.sleep(0.02)
        calc.latest["k"] = 10 ** 9                         # «пришёл более новый»
        sem.release()
        with pytest.raises(HTTPException) as e:
            await t_old
        assert detail_of(e)[1]["code"] == "superseded"
        assert await lanes_mod.run_heavy(FakeReq(), "k", lambda: "next") == "next"     # слот не утерян
    run(go())


def test_different_kinds_do_not_supersede_each_other(lanes):
    calc, _ = lanes
    calc.queue_wait = 5.0

    async def go():
        b = Blocker()
        t1 = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "a", b))
        await wait_started(b)
        t2 = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "b", lambda: "b"))
        await asyncio.sleep(0.1)
        b.release.set()
        assert await t1 == "ok" and await t2 == "b"
    run(go())


def test_timeout_releases_slot_only_when_fn_finishes(lanes, monkeypatch):
    monkeypatch.setattr(lanes_mod, "WATCHDOG_FACTOR", 1000.0)       # watchdog здесь не участвует

    async def go():
        b = Blocker()
        with pytest.raises(HTTPException) as e:
            await lanes_mod.run_heavy(FakeReq(), "backtest", b, timeout=0.1)
        assert detail_of(e)[0] == 504 and detail_of(e)[1]["code"] == "timeout"
        with pytest.raises(HTTPException) as e2:           # fn ещё работает -> слот занят
            await lanes_mod.run_heavy(FakeReq(), "backtest", lambda: 1)
        assert detail_of(e2)[1]["code"] == "busy"
        b.release.set()                                    # fn завершилась -> слот свободен
        await asyncio.sleep(0.1)
        assert await lanes_mod.run_heavy(FakeReq(), "backtest", lambda: 7) == 7
    run(go())


def test_exception_releases_slot_and_propagates(lanes):
    def boom():
        raise RuntimeError("упало")

    async def go():
        for _ in range(2):
            with pytest.raises(RuntimeError, match="упало"):
                await lanes_mod.run_heavy(FakeReq(), "backtest", boom)
        assert await lanes_mod.run_heavy(FakeReq(), "backtest", lambda: 1) == 1
    run(go())


def test_disconnected_client_is_499_and_slot_released(lanes):
    called = []

    async def go():
        with pytest.raises(HTTPException) as e:
            await lanes_mod.run_heavy(FakeReq(disconnected=True), "backtest", lambda: called.append(1))
        assert detail_of(e)[0] == 499 and detail_of(e)[1]["code"] == "cancelled"
        assert not called
        assert await lanes_mod.run_heavy(FakeReq(), "backtest", lambda: 1) == 1
    run(go())


def test_watchdog_swaps_semaphore(lanes, caplog):
    calc, _ = lanes
    calc.queue_wait = 3.0

    async def go():
        b = Blocker()
        with pytest.raises(HTTPException):
            await lanes_mod.run_heavy(FakeReq(), "backtest", b, timeout=0.1)     # 504; fn «зависла»
        old = calc.sem
        t0 = time.monotonic()
        # пока watchdog не сработал (2 x 0.1 с), слот занят; ожидающий подхватит ЗАМЕНЁННЫЙ семафор
        assert await lanes_mod.run_heavy(FakeReq(), "backtest", lambda: "after-watchdog") == "after-watchdog"
        assert 0.1 < time.monotonic() - t0 < 15                                   # верхний порог с запасом на нагрузку
        assert calc.sem is not old and calc.abandoned == 1
        b.release.set()                                                         # зомби доработал
        await asyncio.sleep(0.1)
        assert calc.abandoned == 0
        assert await lanes_mod.run_heavy(FakeReq(), "backtest", lambda: 3) == 3
    with caplog.at_level("ERROR", logger="tradebot.web"):
        run(go())
    assert any("семафор заменён" in r.message for r in caplog.records)


def test_watchdog_respects_abandon_limit(lanes):
    _, net = lanes

    async def go():
        blockers = [Blocker(), Blocker()]
        for b in blockers:
            try:
                await lanes_mod.run_heavy(FakeReq(), "fetch", b, timeout=0.05)
            except HTTPException:
                pass
            await asyncio.sleep(0.15)                                           # watchdog (0.1 с) успевает
        assert net.abandoned == 1                                              # лимит 1: второй зомби слот не освободит
        with pytest.raises(HTTPException) as e:
            await lanes_mod.run_heavy(FakeReq(), "fetch", lambda: 1)
        assert detail_of(e)[1]["code"] == "busy"
        for b in blockers:
            b.release.set()
        await asyncio.sleep(0.2)
        assert net.abandoned == 0
        assert await lanes_mod.run_heavy(FakeReq(), "fetch", lambda: 5) == 5
    run(go())


def test_calc_and_network_lanes_are_independent(lanes):
    calc, net = lanes

    async def go():
        b = Blocker()
        t = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "optimize", b))
        await wait_started(b)
        assert await lanes_mod.run_heavy(FakeReq(), "update", lambda: "net-ok") == "net-ok"   # расчёт не блокирует сеть
        b.release.set()
        await t
        n = Blocker()
        t = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "fetch", n))
        await wait_started(n)
        assert await lanes_mod.run_heavy(FakeReq(), "backtest", lambda: "calc-ok") == "calc-ok"   # сеть не блокирует расчёт
        with pytest.raises(HTTPException) as e:                                              # а два сетевых — по очереди
            await lanes_mod.run_heavy(FakeReq(), "update", lambda: 1)
        assert detail_of(e)[1]["code"] == "busy"
        n.release.set()
        await t
    run(go())
    assert calc.sem is not net.sem


def test_network_requests_of_same_kind_do_not_supersede(lanes):
    _, net = lanes
    net.queue_wait = 5.0

    async def go():
        b = Blocker()
        t1 = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "fetch", b))
        await wait_started(b)
        t2 = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "fetch", lambda: "eth"))
        await asyncio.sleep(0.1)
        t3 = asyncio.create_task(lanes_mod.run_heavy(FakeReq(), "fetch", lambda: "btc-4h"))
        await asyncio.sleep(0.1)
        b.release.set()
        assert await t1 == "ok" and await t2 == "eth" and await t3 == "btc-4h"
    run(go())


def test_pool_sizes_are_explicit():
    assert lanes_mod._calc.pool._max_workers == 1 + lanes_mod._calc.abandon_limit
    assert lanes_mod._net.pool._max_workers == 1 + lanes_mod._net.abandon_limit
    assert lanes_mod._calc.pool is not lanes_mod._net.pool


def test_http_busy_body_contains_wait_info(lanes, monkeypatch):
    """Через настоящий эндпоинт: 429 busy несёт waited_s/queue_wait_s для подсказки в UI."""
    from fastapi.testclient import TestClient
    calc, _ = lanes
    calc.queue_wait = 0.1
    b = Blocker()
    monkeypatch.setattr(service, "run_backtest", lambda req, dl=None: b())
    client = TestClient(api_mod.app)
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(1) as ex:
        first = ex.submit(client.post, "/api/backtest", json={"strategy": "EMA cross", "days": 60})
        assert b.started.wait(5)
        second = client.post("/api/backtest", json={"strategy": "EMA cross", "days": 90})
        b.release.set()
        first.result(10)
    d = second.json()["detail"]
    assert second.status_code == 429 and d["code"] == "busy" and d["queue_wait_s"] == 0.1 and d["waited_s"] >= 0
