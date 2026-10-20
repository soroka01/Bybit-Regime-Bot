"""Аудит Telegram G5: воркер уведомлений — RetryAfter не расходует попытки, error/trade ретраятся до ~5 минут, потеря логируется."""
import asyncio
import logging
from types import SimpleNamespace as NS

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter

from helpers import run
from tradebot.telegram import notify as nt
from tradebot.telegram.notify import Notifier
from tradebot.trading.state import Store


class Bot:
    """Бот, который первые N отправок падает заданным исключением."""

    def __init__(self, errors=()):
        self.errors, self.sent = list(errors), []

    async def send_message(self, chat, text, **kw):
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(text)


def ra(n):
    return TelegramRetryAfter(method=NS(), message="flood", retry_after=n)


def net():
    return TelegramNetworkError(method=NS(), message="net down")


@pytest.fixture
def mk(tmp_path):
    def make(errors):
        clk = NS(t=0.0)
        sleeps = []

        async def sleep(d):
            sleeps.append(d)
            clk.t += d

        n = Notifier(Store(tmp_path / "n.db"), 1, clock=lambda: clk.t, sleep=sleep)
        n.attach(Bot(errors))
        return n, sleeps
    return make


def test_retry_after_does_not_consume_attempts_and_is_honoured(mk):
    n, sleeps = mk([ra(5), ra(7), ra(9), ra(11)])              # четыре лимита подряд: прежний код сдался бы после третьего
    assert run(n._deliver("info", "x")) is True
    assert n.bot.sent == ["x"] and sleeps == [5, 7, 9, 11]


def test_retry_after_pause_capped_at_60(mk):
    n, sleeps = mk([ra(900)])
    assert run(n._deliver("info", "x")) is True
    assert sleeps == [nt.MAX_SLEEP_S]


def test_info_gives_up_after_three_network_failures_and_logs(mk, caplog):
    n, sleeps = mk([net()] * 5)
    with caplog.at_level(logging.ERROR, logger="notify"):
        assert run(n._deliver("info", "x")) is False
    assert not n.bot.sent and len(n.bot.errors) == 2 and len(sleeps) == 2
    assert any("отброшено" in r.message for r in caplog.records)


@pytest.mark.parametrize("level", ["error", "trade"])
def test_error_and_trade_retry_through_long_outage(mk, level):
    n, sleeps = mk([net()] * 8)                                 # намного больше трёх попыток
    assert run(n._deliver(level, "x")) is True
    assert n.bot.sent == ["x"] and len(sleeps) == 8
    assert sleeps == sorted(sleeps) and max(sleeps) <= 30        # нарастающая пауза с потолком 30 с


def test_error_dropped_after_window_and_final_drop_logged(mk, caplog):
    n, sleeps = mk([net()] * 1000)
    with caplog.at_level(logging.ERROR, logger="notify"):
        assert run(n._deliver("error", "x")) is False
    assert not n.bot.sent and nt.RETRY_WINDOW_S - 60 <= sum(sleeps) <= nt.RETRY_WINDOW_S      # ~5 минут, не больше
    assert any("не доставлено" in r.message and "error" in r.message for r in caplog.records)


def test_deterministic_client_errors_dropped_immediately(mk):
    n, sleeps = mk([TelegramForbiddenError(method=NS(), message="Forbidden: bot was blocked by the user")])
    assert run(n._deliver("error", "x")) is False and not sleeps
    n, sleeps = mk([TelegramBadRequest(method=NS(), message="Bad Request: chat not found")])
    assert run(n._deliver("trade", "x")) is False and not sleeps


def test_html_parse_error_still_falls_back_to_plain_text(mk):
    n, sleeps = mk([TelegramBadRequest(method=NS(), message="Bad Request: can't parse entities")])
    assert run(n._deliver("info", "<b>x</b>")) is True and n.bot.sent == ["x"] and not sleeps


def test_worker_is_fifo_and_survives_dropped_message(mk):
    n, sleeps = mk([TelegramForbiddenError(method=NS(), message="Forbidden")])
    n("info", "первое")
    n("info", "второе")

    async def go():
        t = asyncio.create_task(n.worker())
        for _ in range(50):
            await asyncio.sleep(0)
        t.cancel()
    run(go())
    assert len(n.bot.sent) == 1 and "второе" in n.bot.sent[0]
