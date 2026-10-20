"""Синхронизация и отмена для загрузки рыночных данных (parquet-кэш общий у бота и у веб-дашборда в одном процессе).

1) Блокировка по пути файла (`locked`): читатель-модификатор-писатель одного parquet не должен выполняться двумя потоками сразу.
   Ожидание ограничено: обычные (не веб) вызывающие, например обработка бара ботом, ждут не дольше LOCK_TIMEOUT_S и при
   занятом файле работают с тем, что уже лежит на диске (см. klines.load, derivs._incremental) — 600-секундная веб-докачка
   не может остановить бота. Веб-задание докачки оборачивается в `patient()` и ждёт терпеливо (но тоже с пределом и отменой).
2) Отмена (`bind_cancel` / `check_cancel` / `sleep`): долгие сетевые циклы проверяют флаг и выходят сразу, чтобы остановка
   процесса не ждала скачивания. Флаг привязывается к потоку (веб-полоса ставит свой Event на время задания) либо ставится
   на весь процесс через `request_shutdown()`."""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time

log = logging.getLogger("tradebot.market")

LOCK_TIMEOUT_S = 5.0          # сколько ждёт файл обычный вызывающий (бот)
PATIENT_TIMEOUT_S = 300.0     # сколько ждёт файл веб-задание докачки внутри `patient()`
_SLICE_S = 0.25               # шаг опроса отмены при ожидании/сне


class Cancelled(Exception):
    """Задание прервано флагом отмены (остановка приложения): частично загруженное не сохраняется."""


class LockTimeout(RuntimeError):
    """Файл занят другим обновлением дольше допустимого, а локальной копии для работы нет. (Не TimeoutError: тот run_heavy
    принял бы за исчерпанный срок расчёта и ответил бы 504 «расчёт дольше N с».)"""


_tl = threading.local()
_shutdown = threading.Event()
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


# ---------------------------------------------------------------- отмена
def request_shutdown() -> None:
    """Процесс завершается: любая загрузка (в любом потоке) прекращается при ближайшей проверке."""
    _shutdown.set()


def reset_shutdown() -> None:
    """Снимает общий флаг остановки (для тестов и повторного запуска в одном процессе)."""
    _shutdown.clear()


@contextlib.contextmanager
def bind_cancel(event: threading.Event):
    """На время блока флаг `event` управляет отменой загрузок в ЭТОМ потоке."""
    prev = getattr(_tl, "cancel", None)
    _tl.cancel = event
    try:
        yield
    finally:
        _tl.cancel = prev


def cancelled() -> bool:
    ev = getattr(_tl, "cancel", None)
    return _shutdown.is_set() or (ev is not None and ev.is_set())


def check_cancel() -> None:
    if cancelled():
        raise Cancelled("загрузка данных прервана (остановка приложения)")


def sleep(seconds: float) -> None:
    """time.sleep, который просыпается и бросает Cancelled, как только выставлен флаг отмены."""
    end = time.monotonic() + seconds
    while True:
        check_cancel()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, _SLICE_S))


# ---------------------------------------------------------------- блокировки по пути
@contextlib.contextmanager
def patient(timeout: float = PATIENT_TIMEOUT_S):
    """Веб-задания докачки: ждать занятый файл до `timeout`, а не LOCK_TIMEOUT_S."""
    prev = getattr(_tl, "lock_timeout", None)
    _tl.lock_timeout = timeout
    try:
        yield
    finally:
        _tl.lock_timeout = prev


def lock_timeout() -> float:
    t = getattr(_tl, "lock_timeout", None)
    return LOCK_TIMEOUT_S if t is None else t


def path_lock(path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.RLock()
        return lk


@contextlib.contextmanager
def locked(path, timeout: float | None = None):
    """`with locked(p) as got:` — got=True, если блокировка получена (держится до выхода из блока); False — файл занят дольше
    таймаута (вызывающий сам решает: читатель берёт имеющийся файл, писатель пропускает запись). Отмена прерывает ожидание."""
    lk = path_lock(path)
    limit = lock_timeout() if timeout is None else timeout
    end = time.monotonic() + limit
    got = False
    while True:
        check_cancel()
        left = end - time.monotonic()
        if lk.acquire(timeout=max(min(left, _SLICE_S), 0.0)):
            got = True
            break
        if left <= 0:
            break
    if not got:
        log.warning("файл %s занят другим обновлением дольше %.0f с", os.path.basename(os.fspath(path)), limit)
    try:
        yield got
    finally:
        if got:
            lk.release()
