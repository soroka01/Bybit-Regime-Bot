"""Уведомления владельцу через очередь: сбой Telegram не должен влиять на торговлю.
Структурные события (open/close/stopout/daily/... из telegram/messages.py) собираются там же; простые тексты — «иконка + текст».
Все данные экранируются (HTML). Дубли предупреждений/ошибок за 10 минут не повторяются, итог суток и сигналы — без звука."""
from __future__ import annotations

import asyncio
import logging
import re
import time

from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter,
                                 TelegramServerError)

from tradebot.telegram import keyboards as kb
from tradebot.telegram import messages as M
from tradebot.telegram import texts as tx
from tradebot.trading.state import Store

log = logging.getLogger("notify")
ICONS = {"info": "ℹ️", "trade": "💱", "warn": "⚠️", "error": "🚨", "signal": "📈", "daily": "📅"}
DEDUP_S = 600.0
PERSISTENT_LEVELS = ("error", "trade")     # такие сообщения нельзя терять при сбое сети: повторяем до RETRY_WINDOW_S
RETRY_WINDOW_S = 300.0                     # сколько всего пытаемся доставить одно сообщение
MAX_SLEEP_S = 60.0                         # потолок одной паузы (в том числе по retry_after от Telegram)
MAX_LEN = 500


def _norm(text: str) -> str:
    """Ключ для дедупликации: без цифр (цены/время меняются, суть — нет)."""
    return re.sub(r"[\d.]+", "#", text)[:120]


class Notifier:
    """Вызывается синхронно как notify(level, text[, data]): пишет событие в БД и кладёт сообщение в очередь.
    data — структурное событие (словарь с ключом kind); без него текст выводится как есть."""

    def __init__(self, store: Store, owner_id: int, maxsize: int = 200, clock=time.monotonic, sleep=asyncio.sleep):
        self.store, self.owner_id, self.clock, self.sleep = store, owner_id, clock, sleep
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize)
        self.bot = None
        self._ready = asyncio.Event()
        self._seen: dict[tuple[str, str], float] = {}
        self.suppressed = 0

    def fmt(self, level: str, text: str, data: dict | None = None) -> str:
        """HTML-текст уведомления. Сырой текст обрезается ДО экранирования/тегов, поэтому теги не ломаются."""
        if data is not None:
            try:
                return M.build(data)
            except Exception as e:  # битые данные не должны терять уведомление: показываем plain-текст
                log.warning("notify: не удалось собрать %s: %s", data.get("kind"), type(e).__name__)
        text = tx.trunc(text.strip(), MAX_LEN)
        lines = [tx.esc(ln) for ln in text.split("\n")]
        lines[0] = f"{ICONS.get(level, '')} {lines[0]}".strip()
        return "\n".join(lines)

    def _dup(self, level: str, text: str) -> bool:
        if level not in ("warn", "error"):
            return False
        now = self.clock()
        self._seen = {k: t for k, t in self._seen.items() if now - t < DEDUP_S}
        key = (level, _norm(text))
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def __call__(self, level: str, text: str, data: dict | None = None) -> None:
        try:
            self.store.add_event(level, text)
            if data is None and self._dup(level, text):      # структурные события (сделки, стоп-ауты) не склеиваем
                self.suppressed += 1
                return
            self.queue.put_nowait((level, self.fmt(level, text, data)))
        except Exception as e:  # никогда не пробрасываем в торговый код
            log.warning("notify dropped: %s", type(e).__name__)

    def attach(self, bot) -> None:
        self.bot = bot
        self._ready.set()

    def _kwargs(self, level: str) -> dict:
        kw: dict = {}
        if level == "trade":
            kw["reply_markup"] = kb.trade_notice_menu()
        if level in ("daily", "signal"):
            kw["disable_notification"] = True
        return kw

    async def _send(self, level: str, text: str) -> None:
        """HTML; если Telegram не разобрал сущности — один повтор простым текстом без тегов."""
        try:
            await self.bot.send_message(self.owner_id, text, parse_mode="HTML", **self._kwargs(level))
        except TelegramBadRequest as e:
            if "parse entities" not in str(e).lower():
                raise
            log.warning("notify: ошибка разбора HTML — повтор простым текстом")
            await self.bot.send_message(self.owner_id, tx.strip_html(text), parse_mode=None, **self._kwargs(level))

    @staticmethod
    def _transient(e: Exception) -> bool:
        """Сбой, который может пройти сам (сеть, 5xx, лимит, неизвестная ошибка). Ответ Telegram «запрос неверен / запрещён /
        не найдено» повтором не лечится — такое сообщение сразу считается недоставленным, очередь не стоит."""
        if isinstance(e, (TelegramRetryAfter, TelegramNetworkError, TelegramServerError)):
            return True
        return not isinstance(e, TelegramAPIError)

    async def _deliver(self, level: str, text: str) -> bool:
        """Доставка одного сообщения. TelegramRetryAfter ждём целиком (не больше MAX_SLEEP_S за раз) и в число попыток не
        считаем. Остальные сбои: error/trade повторяем с нарастающей паузой до RETRY_WINDOW_S, прочие уровни — 3 попытки."""
        persistent = level in PERSISTENT_LEVELS
        t0, fails = self.clock(), 0
        while True:
            try:
                await self._send(level, text)
                return True
            except asyncio.CancelledError:
                raise
            except TelegramRetryAfter as e:
                wait = min(max(float(e.retry_after), 1.0), MAX_SLEEP_S)
                log.warning("telegram: лимит запросов, жду %.0f с (повтор не считается попыткой)", wait)
            except Exception as e:
                if not self._transient(e):
                    log.error("notify: сообщение уровня %s не доставлено (%s) — отброшено", level, type(e).__name__)
                    return False
                fails += 1
                if not persistent and fails >= 3:
                    log.error("notify: сообщение уровня %s не доставлено за 3 попытки (%s) — отброшено", level, type(e).__name__)
                    return False
                wait = min(2.0 * 2 ** (fails - 1), 30.0)
                log.warning("telegram send failed (%s), retry in %ss", type(e).__name__, wait)
            if self.clock() + wait - t0 > RETRY_WINDOW_S:
                log.error("notify: сообщение уровня %s не доставлено за %.0f с — отброшено", level, RETRY_WINDOW_S)
                return False
            await self.sleep(wait)

    async def worker(self) -> None:
        await self._ready.wait()
        while True:
            level, text = await self.queue.get()
            await self._deliver(level, text)
            await self.sleep(0.05)

    async def flush(self, timeout: float = 5.0) -> None:
        """Best-effort дослать очередь при остановке."""
        async def _drain():
            while not self.queue.empty() and self.bot is not None:
                level, text = self.queue.get_nowait()
                await self._send(level, text)
        try:
            await asyncio.wait_for(_drain(), timeout)
        except Exception:
            pass
