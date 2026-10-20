"""Полосы тяжёлых заданий дашборда: Lane (семафор + свой пул потоков + правила очереди), `run_heavy` и ошибки API.
Вынесено из api.py без изменения поведения. Подменяемые в тестах значения (_calc, _net, QUEUE_SLICE, WATCHDOG_FACTOR)
читаются в api.py (там тонкая обёртка run_heavy) и передаются сюда параметрами — подмена api_mod.<имя> продолжает действовать."""
from __future__ import annotations

import asyncio
import itertools
import logging
import re
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException, Request

from tradebot.market import guard

log = logging.getLogger("tradebot.web")
HEAVY_TIMEOUT = 60.0
QUEUE_WAIT = 20.0
FETCH_TIMEOUT = 600.0

_ids = itertools.count(1)
WATCHDOG_FACTOR = 2.0       # задание дольше WATCHDOG_FACTOR * timeout считается зависшим: семафор заменяется свежим
QUEUE_SLICE = 1.0           # ожидающий запрос просыпается раз в секунду: проверить «вытеснен ли» и «заменён ли семафор»
NET_QUEUE_WAIT = 3.0        # загрузка данных: второй fetch/update не ждёт десятки секунд — сразу «занято»


class Lane:
    """Полоса тяжёлых заданий: свой семафор (1 одновременное задание), свой пул потоков и свои правила очереди.
    Расчёты и сетевые задания живут в РАЗНЫХ полосах и друг друга не блокируют.
    Размер пула = 1 активное задание + abandon_limit «брошенных» watchdog-ом потоков (поток убить нельзя; он доработает
    сам, а полоса за это время уже принимает новое задание)."""

    def __init__(self, name: str, queue_wait: float, supersede: bool, abandon_limit: int):
        self.name, self.queue_wait, self.supersede, self.abandon_limit = name, queue_wait, supersede, abandon_limit
        self.pool = self._new_pool()
        self.sem: asyncio.Semaphore | None = None     # создаётся лениво, уже внутри цикла событий
        self.latest: dict[str, int] = {}              # последний id запроса по виду: «последний побеждает»
        self.abandoned = 0                            # сколько брошенных потоков ещё работает
        self.events: set[threading.Event] = set()     # флаги отмены идущих заданий (см. market.guard)
        self.closed = False                           # приложение остановлено: новые задания не принимаются
        _registry.add(self)

    def _new_pool(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=1 + self.abandon_limit, thread_name_prefix=f"web-{self.name}")

    def semaphore(self) -> asyncio.Semaphore:
        if self.sem is None:
            self.sem = asyncio.Semaphore(1)
        return self.sem

    def cancel_all(self) -> None:
        """Выставляет флаг отмены всем идущим заданиям: сетевые циклы и Deadline.check выходят сразу."""
        for ev in list(self.events):
            ev.set()

    def shutdown(self) -> None:
        """Остановка приложения: отмена идущих заданий, очередь пула сбрасывается, ожидания нет — остановка бота не ждёт
        10-минутной загрузки (шаг ждали бы сами потоки пула: concurrent.futures join-ит их при выходе из процесса)."""
        self.closed = True
        self.cancel_all()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def reopen(self) -> None:
        """Повторный старт приложения в том же процессе (например, тесты с несколькими TestClient): свежий пул."""
        if self.closed:
            self.pool = self._new_pool()
            self.sem = None
            self.closed = False


_registry: "weakref.WeakSet[Lane]" = weakref.WeakSet()


def _shutdown_at_exit() -> None:
    """Выполняется ДО того, как интерпретатор ждёт потоки пулов: выставляет отмену всем заданиям и всем загрузкам процесса,
    чтобы выход (в том числе остановка бота, в процессе которого живёт дашборд) не ждал долгую докачку данных."""
    guard.request_shutdown()
    for lane in list(_registry):
        try:
            lane.shutdown()
        except Exception:  # noqa: BLE001
            pass


try:                                  # приватный, но стабильный с Python 3.9 хук: срабатывает раньше join-а потоков executor-а
    threading._register_atexit(_shutdown_at_exit)
except Exception:  # noqa: BLE001
    import atexit
    atexit.register(_shutdown_at_exit)


_calc = Lane("calc", QUEUE_WAIT, supersede=True, abandon_limit=2)    # backtest/sweep/portfolio/optimize/leaderboard
_net = Lane("net", NET_QUEUE_WAIT, supersede=False, abandon_limit=1)  # fetch/update (разные символы/тф не вытесняют друг друга)
NET_KINDS = {"fetch", "update"}


def _lane_for(kind: str, calc: Lane, net: Lane) -> Lane:
    return net if kind in NET_KINDS else calc


_PATH_RE = re.compile(r"""(?:(?<!\w)[A-Za-z]:[\\/]|\\\\)[^\s'"<>|*?]*|(?<![\w/.:~-])/(?:[\w.\-~]+/)+[\w.\-~]+""")


def scrub_paths(msg: str) -> str:
    """В сообщениях об ошибках не должно быть абсолютных путей (диск, UNC, /home/...): остаётся только последнее имя."""
    def short(m):
        tail = re.split(r"[\\/]", m.group(0).rstrip("\\/"))[-1]
        return "…/" + tail if tail else "…"
    return _PATH_RE.sub(short, msg)


def _err(status: int, code: str, msg: str, **extra):
    return HTTPException(status, detail={"code": code, "message": scrub_paths(msg), **extra})


async def _acquire(lane: Lane, kind: str, my: int, queue_slice: float) -> asyncio.Semaphore:
    """Ждёт свободный слот не дольше lane.queue_wait. Раз в QUEUE_SLICE проверяет, не вытеснен ли запрос более новым
    и не подменил ли watchdog семафор (иначе ожидающие остались бы на старом, который держит зависший поток)."""
    t0 = time.monotonic()
    end = t0 + lane.queue_wait
    while True:
        sem = lane.semaphore()
        try:
            await asyncio.wait_for(sem.acquire(), min(max(end - time.monotonic(), 0.0), queue_slice))
            return sem
        except asyncio.TimeoutError:
            pass
        if lane.supersede and lane.latest.get(kind) != my:
            raise _err(429, "superseded", "запрос вытеснен более новым")
        if time.monotonic() >= end:
            waited = time.monotonic() - t0
            raise _err(429, "busy", "сервер занят другим расчётом" if lane.name == "calc" else "уже идёт загрузка данных",
                       waited_s=round(waited, 1), queue_wait_s=lane.queue_wait)


async def run_heavy(request: Request, kind: str, fn, timeout: float, *, calc: Lane, net: Lane, queue_slice: float,
                    watchdog_factor: float):
    """Выполняет fn() в потоке под семафором своей полосы. fn не должен менять общее состояние без блокировок.
    Таймаут отвечает клиенту 504, но слот освобождается только когда fn реально завершилась; если она не
    завершилась за WATCHDOG_FACTOR*timeout — watchdog подменяет семафор свежим (и пишет в лог)."""
    lane, my = _lane_for(kind, calc, net), next(_ids)
    if lane.closed:
        raise _err(503, "shutting_down", "сервер останавливается")
    if lane.supersede:
        lane.latest[kind] = my
    sem = await _acquire(lane, kind, my, queue_slice)
    handed_off = False
    try:
        if lane.supersede and lane.latest.get(kind) != my:
            raise _err(429, "superseded", "запрос вытеснен более новым")
        if await request.is_disconnected():
            raise _err(499, "cancelled", "клиент отменил запрос")
        loop = asyncio.get_running_loop()
        cancel = threading.Event()
        lane.events.add(cancel)

        def call():
            with guard.bind_cancel(cancel):
                return fn()
        fut = loop.run_in_executor(lane.pool, call)
        state = {"abandoned": False}

        def _watchdog():
            if fut.done():
                return
            if lane.abandoned >= lane.abandon_limit:
                log.error("%s/%s: задание не завершилось за %.0f с, но брошенных потоков уже %d — слот остаётся занятым",
                          lane.name, kind, timeout * watchdog_factor, lane.abandoned)
                return
            lane.abandoned += 1
            state["abandoned"] = True
            if lane.sem is sem:
                lane.sem = asyncio.Semaphore(1)
            log.error("%s/%s: задание не завершилось за %.0f с (таймаут %.0f с) — семафор заменён, поток продолжает работать",
                      lane.name, kind, timeout * watchdog_factor, timeout)
        wd = loop.call_later(timeout * watchdog_factor, _watchdog)

        def _done(f):
            lane.events.discard(cancel)
            wd.cancel()
            if state["abandoned"]:
                lane.abandoned -= 1
            sem.release()
            if not f.cancelled():
                f.exception()  # «забираем» исключение, чтобы не было предупреждений
        fut.add_done_callback(_done)
        handed_off = True
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            raise _err(504, "timeout", f"расчёт дольше {int(timeout)} с")
    finally:
        if not handed_off:
            sem.release()
