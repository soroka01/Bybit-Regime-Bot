"""Точка входа: один процесс — раннер + мониторинг + Telegram-polling + (опционально) веб на 127.0.0.1:8000.
Запуск: python -m tradebot.app (или run.bat). Управление — только через Telegram.
Проверка без запуска: python -m tradebot.app --check (см. tradebot/check.py)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys
import threading

from tradebot.i18n import tr
from tradebot.paths import LOGS, STATE
from tradebot.telegram import messages as M

log = logging.getLogger("app")
EXIT_LOCKED, EXIT_CONFIG = 3, 4


class AlreadyRunning(Exception):
    pass


def acquire_lock(path=STATE / "bot.lock"):
    """Эксклюзивная блокировка файла на всё время жизни процесса (отпускается ОС при падении)."""
    f = open(path, "a+b")
    try:
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise AlreadyRunning() from None
    return f


def disable_quickedit() -> None:
    """Выделение мышью в консоли Windows ставит процесс на паузу — отключаем QuickEdit."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)  # -QUICK_EDIT, +EXTENDED_FLAGS
    except Exception:
        pass


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(LOGS / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    sh = logging.StreamHandler()
    for h in (fh, sh):
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[fh, sh], force=True)
    # httpx пишет полные URL запросов; ключи в заголовках, но подстрахуемся
    for n in ("httpx", "httpcore", "aiogram.event"):
        logging.getLogger(n).setLevel(logging.WARNING)


def start_web():
    """Дашборд (строит другой разработчик): импорт мягкий, при ошибке просто продолжаем."""
    try:
        from tradebot.web.api import app
        import uvicorn
    except Exception as e:
        log.info(tr("web dashboard unavailable (%s: %s) — continuing without it"), type(e).__name__, e)
        return None
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning"))
    threading.Thread(target=server.run, name="web", daemon=True).start()
    log.info(tr("dashboard: http://127.0.0.1:8000"))
    return server


async def shutdown_data(client) -> dict:
    """Данные остановки: про стоп говорим только если он подтверждён чтением с биржи."""
    try:
        pos = await asyncio.wait_for(client.position(), 10)
    except Exception:
        return {"kind": "shutdown", "unknown": True}
    if pos is None:
        return {"kind": "shutdown", "pos": None}
    return {"kind": "shutdown", "pos": {"side": pos.side, "qty": pos.qty}, "stop": pos.stop}


async def startup_data(store, client) -> dict:
    """Данные сообщения о запуске: счёт и позиция читаются с биржи (best effort, с коротким таймаутом)."""
    d = {"kind": "startup", "enabled": bool(store.settings()["enabled"]), "killed": bool(store.get("killed", False)),
         "equity": None, "pos": None, "pos_known": True}
    try:
        d["equity"] = await asyncio.wait_for(client.equity(), 10)
    except Exception:
        d["equity"] = store.get("equity")
    try:
        pos = await asyncio.wait_for(client.position(), 10)
        d["pos"] = {"side": pos.side, "qty": pos.qty, "upnl": pos.upnl} if pos else None
    except Exception:
        d["pos_known"] = False
    return d


def finished_task_error(done) -> BaseException | None:
    """Исключение, с которым надо упасть, когда одна из задач amain завершилась. Инвариант: любая рабочая задача (раннер,
    монитор, polling, ...) живёт до остановки. Завершилась сама — с ошибкой, без неё или будучи отменённой — процесс НЕ должен
    тихо продолжать без неё (торговля/мониторинг мертвы): падаем (код 1), и run.bat перезапускает бота. None — штатная остановка
    (завершилась только задача "stop")."""
    stopping = any(t.get_name() == "stop" for t in done)
    for t in done:
        if t.get_name() == "stop":
            continue
        if t.cancelled():
            exc: BaseException | None = RuntimeError(tr("task {name} was cancelled").format(name=t.get_name()))
        else:
            exc = t.exception()
        if exc is None and not stopping:
            exc = RuntimeError(tr("task {name} finished without an error although nobody asked it to stop").format(name=t.get_name()))
        if exc is not None:
            log.error(tr("task %s failed: %r"), t.get_name(), exc)
            return exc
    return None


async def amain(env) -> None:
    from tradebot.telegram.bot import build
    from tradebot.telegram.handlers import Controller
    from tradebot.telegram.notify import Notifier
    from tradebot.telegram.proposals import ProposalWatcher
    from tradebot.trading.config import MAINNET_URL
    from tradebot.trading.exchange import BybitClient
    from tradebot.trading.executor import Executor
    from tradebot.trading.market_feed import MarketFeed
    from tradebot.trading.runner import Runner
    from tradebot.trading.state import Store

    store = Store()
    # номер в orderLinkId не должен повториться даже на старой/восстановленной из копии БД (дубль принялся бы за «ордер уже есть»)
    store.seed_order_seq()
    notify = Notifier(store, env.owner_id)
    if store.migration_error:         # копия перед миграцией БД не удалась: данные целы, бот на старой схеме, миграцию повторит следующий запуск
        notify("warn", f"Миграция базы не выполнена: {store.migration_error}. Бот работает на старой схеме и повторит миграцию при следующем запуске.")
    client = BybitClient(MAINNET_URL, env.api_key, env.api_secret)
    executor = Executor(client, store, notify)
    feed = MarketFeed()
    runner = Runner(store, feed, executor, notify, tz_name=env.tz_name)
    ctl = Controller(store, executor, runner, env.tz_name)
    bot, dp, panel = build(env.bot_token, env.owner_id, ctl)
    notify.attach(bot)
    watcher = ProposalWatcher(ctl, env.owner_id)      # «В бота» на сайте -> уведомление в Telegram
    watcher.attach(bot)
    web = start_web()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", signal.SIGTERM)):
        try:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        except ValueError:
            pass

    try:
        await client.sync_time()
    except Exception as e:
        log.warning(tr("time sync: %s"), type(e).__name__)
    await executor.setup()
    M.emit(notify, "info", await startup_data(store, client))

    tasks = [asyncio.create_task(c, name=n) for n, c in (
        ("notify", notify.worker()), ("proposals", watcher.run()), ("runner", runner.run()), ("monitor", runner.monitor()),
        ("polling", dp.start_polling(bot, handle_signals=False, allowed_updates=["message", "callback_query"])),
        ("stop", stop.wait()))]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        exc = finished_task_error(done)
        if exc is not None:
            raise exc
    finally:
        log.info(tr("stopping..."))
        runner.stop.set()
        panel.cancel()
        locked = False
        try:  # дождаться завершения идущей торговой операции, не обрывая ордер на середине
            await asyncio.wait_for(executor.lock.acquire(), 30)
            locked = True
        except Exception:
            log.warning(tr("trading operation did not finish within 30 s"))
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        M.emit(notify, "info", await shutdown_data(client))
        if locked:
            executor.lock.release()
        await notify.flush()
        if web is not None:
            web.should_exit = True
        for closer in (bot.session.close, client.close, feed.close):
            try:
                await closer()
            except Exception:
                pass
        store.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tradebot.app",
                                 description=tr("BTCUSDT trading bot for Bybit (mainnet only, real money)"))
    ap.add_argument("--check", action="store_true",
                    help=tr("check .env, the API key permissions and the account settings (read-only) and exit; the bot is not started: "
                            "no bot.db, no bot.lock, no Telegram, no orders. Exit codes: 0 - all good, 4 - a problem found, 1 - exchange unavailable"))
    args = ap.parse_args(argv)
    if args.check:
        from tradebot.check import check_main
        return check_main()
    disable_quickedit()
    from tradebot.trading.config import ConfigError, load_env
    # Блокировка — ДО открытия лога: второй экземпляр не должен открывать/ротировать bot.log работающего бота (на Windows
    # ротация файла, открытого другим процессом, ломает запись у обоих).
    try:
        lock = acquire_lock()
    except AlreadyRunning:
        print(tr("The bot is already running (storage/state/bot.lock is held). Close the other instance."))
        return EXIT_LOCKED
    setup_logging()
    try:
        env = load_env()
    except ConfigError as e:
        print(tr("Configuration error: {error}").format(error=e))
        lock.close()
        return EXIT_CONFIG
    log.info(tr("starting"))
    try:
        asyncio.run(amain(env))
    except KeyboardInterrupt:
        pass
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
