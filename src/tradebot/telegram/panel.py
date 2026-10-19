"""Панель управления: ОДНО сообщение, правится на месте; автообновление только read-only экранов."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from tradebot.telegram.texts import fit_html, strip_html

log = logging.getLogger("panel")
PANEL_REFRESH_S = 3        # период автообновления
SLOW_REFRESH_S = 10        # после TelegramRetryAfter
SLOW_FOR_S = 60
IDLE_S = 300               # без нажатий дольше — режим ожидания
IDLE_NOTE = "\n\n<i>💤 Панель в режиме ожидания — нажмите ↻</i>"
MAX_LEN = 4000             # лимит Telegram 4096 после разбора; небольшой запас


@dataclass
class Screen:
    text: str
    markup: InlineKeyboardMarkup
    toast: str | None = None   # всплывающее уведомление на callback
    alert: bool = False
    live: bool = False         # можно автообновлять (только чтение: Главная, Позиция, Сигналы)
    view: tuple[str, ...] = ()  # callback экрана (идемпотентный просмотр), по нему идёт автообновление


def _sig(text: str, markup: InlineKeyboardMarkup) -> int:
    return hash((text, markup.model_dump_json()))


def is_parse_error(e: Exception) -> bool:
    """Telegram не смог разобрать HTML-сущности («can't parse entities…»)."""
    return isinstance(e, TelegramBadRequest) and "parse entities" in str(e).lower()


def _is_gone(e: TelegramBadRequest) -> bool:
    m = str(e).lower()
    return "not found" in m or "can't be edited" in m or "message to edit" in m or "deleted" in m


class Panel:
    def __init__(self, store, render: Callable[[tuple[str, ...]], Awaitable[Screen]], clock=time.monotonic,
                 sleep=asyncio.sleep):
        self.store, self.render, self.clock, self.sleep = store, render, clock, sleep
        self._task: asyncio.Task | None = None
        self._hash: int | None = None
        self._last_tap = clock()

    # ---------- идентификатор панели ----------
    @property
    def ref(self) -> dict | None:
        return self.store.get("panel")

    def is_panel(self, chat_id: int, msg_id: int) -> bool:
        r = self.ref
        return bool(r) and r["chat"] == chat_id and r["msg"] == msg_id

    def touch(self) -> None:
        self._last_tap = self.clock()

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    # ---------- отправка/правка (HTML; при ошибке разбора — один повтор простым текстом без тегов) ----------
    @staticmethod
    async def _send(bot, chat_id: int, text: str, markup):
        try:
            return await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
        except TelegramBadRequest as e:
            if not is_parse_error(e):
                raise
            log.warning("send: ошибка разбора HTML — повтор простым текстом")
            return await bot.send_message(chat_id, strip_html(text), reply_markup=markup, parse_mode=None)

    @staticmethod
    async def _edit(bot, chat_id: int, msg_id: int, text: str, markup) -> None:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=markup, parse_mode="HTML")
        except TelegramBadRequest as e:
            if not is_parse_error(e):
                raise
            log.warning("edit: ошибка разбора HTML — повтор простым текстом")
            await bot.edit_message_text(strip_html(text), chat_id=chat_id, message_id=msg_id, reply_markup=markup,
                                        parse_mode=None)

    async def open(self, bot, chat_id: int, screen: Screen) -> None:
        """/start: отправить НОВУЮ панель, закрепить, старую удалить."""
        self.cancel()
        old = self.ref
        text = fit_html(screen.text, MAX_LEN)
        msg = await self._send(bot, chat_id, text, screen.markup)
        self.store.set("panel", {"chat": chat_id, "msg": msg.message_id})
        self._hash = _sig(text, screen.markup)
        self.touch()
        if old and old.get("msg") != msg.message_id:
            try:
                await bot.delete_message(old["chat"], old["msg"])
            except Exception:
                pass
        try:
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        except Exception:
            pass
        self._maybe_start(bot, chat_id, screen)

    async def show(self, bot, chat_id: int, screen: Screen) -> None:
        """Правит панель на месте. «Not modified» — молча; потерянное сообщение — отправить новую панель."""
        self.cancel()
        self.touch()
        ref = self.ref
        text = fit_html(screen.text, MAX_LEN)
        if ref and ref["chat"] == chat_id:
            try:
                await self._edit(bot, chat_id, ref["msg"], text, screen.markup)
                self._hash = _sig(text, screen.markup)
                self._maybe_start(bot, chat_id, screen)
                return
            except TelegramBadRequest as e:
                if "not modified" in str(e).lower():
                    self._hash = _sig(text, screen.markup)
                    self._maybe_start(bot, chat_id, screen)
                    return
                log.debug("edit failed (%s) — отправляю новую панель", e)
        await self.open(bot, chat_id, screen)

    # ---------- автообновление ----------
    def _maybe_start(self, bot, chat_id: int, screen: Screen) -> None:
        if screen.live and screen.view:
            self._task = asyncio.create_task(self._loop(bot, chat_id, screen.view), name="panel-refresh")

    async def _loop(self, bot, chat_id: int, parts: tuple[str, ...]) -> None:
        ref = self.ref
        if not ref:
            return
        msg_id, interval, slow_until = ref["msg"], PANEL_REFRESH_S, 0.0
        while True:
            try:
                await self.sleep(interval)
                now = self.clock()
                if now - self._last_tap >= IDLE_S:
                    s = await self.render(parts)
                    await self._edit(bot, chat_id, msg_id, fit_html(s.text, MAX_LEN - len(IDLE_NOTE)) + IDLE_NOTE, s.markup)
                    self._hash = None
                    return
                if now >= slow_until:
                    interval = PANEL_REFRESH_S
                s = await self.render(parts)
                text = fit_html(s.text, MAX_LEN)
                sig = _sig(text, s.markup)
                if sig == self._hash:
                    continue
                await self._edit(bot, chat_id, msg_id, text, s.markup)
                self._hash = sig
            except asyncio.CancelledError:
                raise
            except TelegramRetryAfter as e:
                await self.sleep(float(e.retry_after))
                interval, slow_until = SLOW_REFRESH_S, self.clock() + SLOW_FOR_S
            except TelegramBadRequest as e:
                if "not modified" in str(e).lower():
                    continue
                if _is_gone(e):
                    return
                log.debug("panel refresh: %s", e)
            except Exception as e:  # автообновление никогда не мешает торговле
                log.debug("panel refresh failed: %s", type(e).__name__)
