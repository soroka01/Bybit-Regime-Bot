"""Общие помощники тестов (не фикстуры): запуск корутин, получатель уведомлений Sink, BybitClient на httpx.MockTransport,
нажатие кнопки меню Telegram. Подключаются как `from helpers import ...` (каталог tests/ уже в sys.path, как у _synth и _stats_fixture)."""
import asyncio

import httpx
from aiogram.exceptions import TelegramBadRequest
from types import SimpleNamespace as NS

from tradebot.telegram import keyboards as kb
from tradebot.trading.exchange import BybitClient


def run(coro):
    return asyncio.run(coro)


class Sink:
    """Получатель уведомлений исполнителя: копит (level, text); data (третий аргумент) игнорирует."""

    def __init__(self):
        self.msgs = []

    def __call__(self, level, text, *data):
        self.msgs.append((level, text))

    def has(self, s, level=None):
        return any(s in t and (level is None or lv == level) for lv, t in self.msgs)

    def count(self, s, level=None):
        """Сколько сообщений содержит подстроку s (и, если задан, имеет уровень level)."""
        return sum(1 for lv, t in self.msgs if s in t and (level is None or lv == level))

    def count_level(self, level):
        return sum(1 for lv, _ in self.msgs if lv == level)


def bybit_client(handler, key="K", secret="S", synced=False):
    """BybitClient на MockTransport(handler): сети нет. synced=True пропускает синхронизацию времени с биржей."""
    c = BybitClient("https://x", key, secret, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    if synced:
        c._synced = 1e18
    return c


def h(c, data):
    """Нажатие кнопки с callback_data=data на Controller c; возвращает экран."""
    return run(c.handle(kb.parse_cb(data)))


def bad(msg):
    """Ошибка Telegram «Bad Request» с текстом msg."""
    return TelegramBadRequest(method=NS(), message=msg)
