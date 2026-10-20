"""Аудит Telegram: панель (G2 повторы и ограничение fallback, G3 один цикл автообновления, G9 пометка старой панели при старте)."""
import asyncio
from types import SimpleNamespace as NS

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter

from tradebot.telegram import keyboards as kb
from tradebot.telegram import panel as panel_mod
from tradebot.telegram.panel import Panel, Screen
from tradebot.trading.state import Store


def run(c):
    return asyncio.run(c)


class Bot:
    def __init__(self):
        self.sent, self.edits, self.deleted = [], [], []
        self.edit_errors: list[Exception] = []
        self.send_errors: list[Exception] = []
        self._n = 100

    async def send_message(self, chat, text, **kw):
        if self.send_errors:
            raise self.send_errors.pop(0)
        self._n += 1
        self.sent.append((chat, text))
        return NS(message_id=self._n)

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None, parse_mode=None):
        await asyncio.sleep(0)
        if self.edit_errors:
            raise self.edit_errors.pop(0)
        self.edits.append((message_id, text))

    async def delete_message(self, chat, msg): self.deleted.append(msg)
    async def pin_chat_message(self, chat, msg, disable_notification=True): pass


def bad(msg):
    return TelegramBadRequest(method=NS(), message=msg)


def screen(text, live=False):
    return Screen(text, kb.info_menu("h", "sg"), live=live, view=("sg",) if live else ())


@pytest.fixture
def pan(tmp_path):
    st = Store(tmp_path / "p.db")
    clk, sleeps = NS(t=0.0), []

    async def render(parts):
        return screen("home")

    async def sleep(d):
        sleeps.append(d)
        clk.t += d
        await asyncio.sleep(0)

    return Panel(st, render, clock=lambda: clk.t, sleep=sleep), Bot(), sleeps


def test_edit_retries_once_after_retry_after_and_keeps_screen(pan):
    p, bot, sleeps = pan

    async def go():
        await p.open(bot, 1, screen("a"))
        mid = p.ref["msg"]
        bot.edit_errors.append(TelegramRetryAfter(method=NS(), message="flood", retry_after=4))
        await p.show(bot, 1, screen("b"))
        assert sleeps == [4] and bot.edits == [(mid, "b")]
        assert len(bot.sent) == 1 and p.ref["msg"] == mid                 # правка прошла после паузы, новой панели нет
    run(go())


def test_retry_after_pause_is_capped(pan):
    p, bot, sleeps = pan

    async def go():
        await p.open(bot, 1, screen("a"))
        bot.edit_errors.append(TelegramRetryAfter(method=NS(), message="flood", retry_after=900))
        await p.show(bot, 1, screen("b"))
    run(go())
    assert sleeps == [panel_mod.RETRY_CAP_S]


def test_network_error_retried_once_without_new_message(pan):
    p, bot, sleeps = pan

    async def go():
        await p.open(bot, 1, screen("a"))
        mid = p.ref["msg"]
        bot.edit_errors.append(TelegramNetworkError(method=NS(), message="timeout"))
        await p.show(bot, 1, screen("b"))
        assert bot.edits == [(mid, "b")] and len(bot.sent) == 1
    run(go())


def test_persistent_failures_never_open_a_new_panel_and_do_not_raise(pan):
    p, bot, _ = pan

    async def go():
        await p.open(bot, 1, screen("a"))
        mid = p.ref["msg"]
        bot.edit_errors += [TelegramRetryAfter(method=NS(), message="f", retry_after=1)] * 2        # и повтор не прошёл
        await p.show(bot, 1, screen("b"))
        bot.edit_errors += [TelegramNetworkError(method=NS(), message="x")] * 2
        await p.show(bot, 1, screen("c"))
        bot.edit_errors += [RuntimeError("boom")]
        await p.show(bot, 1, screen("d"))
        assert len(bot.sent) == 1 and p.ref["msg"] == mid and not bot.deleted
    run(go())


def test_deterministic_bad_request_does_not_spam_new_panels(pan):
    p, bot, _ = pan

    async def go():
        await p.open(bot, 1, screen("a"))
        mid = p.ref["msg"]
        for _ in range(3):                                                  # три тапа подряд, правка всегда отклоняется
            bot.edit_errors.append(bad("Bad Request: MESSAGE_TOO_LONG"))
            await p.show(bot, 1, screen("b"))
        assert len(bot.sent) == 1 and p.ref["msg"] == mid
        bot.edit_errors.append(bad("Bad Request: message to edit not found"))   # а вот потерянное сообщение — новая панель
        await p.show(bot, 1, screen("c"))
        assert len(bot.sent) == 2 and p.ref["msg"] != mid
    run(go())


def test_open_retries_send_after_retry_after(pan):
    p, bot, sleeps = pan
    bot.send_errors.append(TelegramRetryAfter(method=NS(), message="f", retry_after=2))

    async def go():
        await p.open(bot, 1, screen("a"))
        assert len(bot.sent) == 1 and p.ref and sleeps == [2]
    run(go())


def test_transient_failure_on_live_screen_is_repaired_by_refresh_loop(pan):
    p, bot, sleeps = pan

    async def go():
        await p.open(bot, 1, screen("a"))
        mid = p.ref["msg"]
        bot.edit_errors.append(RuntimeError("net"))
        await p.show(bot, 1, screen("home", live=True))                    # правка упала -> хэш сброшен
        assert not bot.edits
        await asyncio.sleep(0.01)
        await asyncio.sleep(0.01)
        assert (mid, "home") in bot.edits                                   # цикл сам дорисовал экран
        p.cancel()
    run(go())


def test_two_concurrent_shows_leave_exactly_one_refresh_loop(pan):
    p, bot, _ = pan

    async def go():
        await p.open(bot, 1, screen("a"))
        await asyncio.gather(p.show(bot, 1, screen("x", live=True)), p.show(bot, 1, screen("y", live=True)),
                             p.show(bot, 1, screen("z", live=True)))
        loops = [t for t in asyncio.all_tasks() if t.get_name() == "panel-refresh" and not t.done()]
        assert len(loops) == 1 and loops[0] is p._task
        p.cancel()
        await asyncio.sleep(0.01)
        assert not [t for t in asyncio.all_tasks() if t.get_name() == "panel-refresh" and not t.done()]
    run(go())


def test_maybe_start_cancels_previous_loop(pan):
    p, bot, _ = pan

    async def go():
        await p.open(bot, 1, screen("a", live=True))
        first = p._task
        p._maybe_start(bot, 1, screen("a", live=True))
        await asyncio.sleep(0)
        assert first.cancelled() or first.cancelling() or first.done()
        assert p._task is not first
        p.cancel()
    run(go())


def test_old_panel_marked_idle_on_start(pan):
    p, bot, _ = pan

    async def go():
        await p.mark_idle_on_start(bot)                                    # панели ещё не было — ничего не делаем
        assert not bot.edits
        p.store.set("panel", {"chat": 1, "msg": 55})
        await p.mark_idle_on_start(bot)
        assert len(bot.edits) == 1 and bot.edits[0][0] == 55
        assert "режиме ожидания" in bot.edits[0][1] and "home" in bot.edits[0][1]
        bot.edit_errors.append(bad("Bad Request: message to edit not found"))
        await p.mark_idle_on_start(bot)                                    # сбой правки не пробрасывается
        assert len(bot.edits) == 1 and not bot.sent
    run(go())


def test_build_registers_startup_hook(tmp_path):
    from aiogram import Bot as AioBot
    from tradebot.telegram.bot import build
    from tradebot.telegram.handlers import Controller
    from fakes.exchange import DryRunExchange
    from tradebot.trading.executor import Executor
    st = Store(tmp_path / "b.db")
    ctl = Controller(st, Executor(DryRunExchange(equity=1000), st, lambda *a: None, "testnet"), NS(trigger=lambda: None), "testnet")
    bot, dp, panel = build("123:abc", 1, ctl)
    calls = []

    async def fake(b):
        calls.append(b)
    panel.mark_idle_on_start = fake
    assert isinstance(bot, AioBot)
    run(dp.emit_startup(bot=bot, dispatcher=dp, bots=[bot]))
    assert calls == [bot]
