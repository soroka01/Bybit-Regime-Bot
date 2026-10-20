"""Роутер aiogram: /start, /menu, текст «Копировать для бота», callback-кнопки панели."""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from tradebot.telegram import keyboards as kb
from tradebot.telegram import texts as tx
from tradebot.telegram.common import EXECUTING_MSG, ONBOARDING, TOAST_MAX, VIEW_HOME
from tradebot.telegram.panel import Panel, Screen
from tradebot.telegram.texts import esc

if TYPE_CHECKING:
    from tradebot.telegram.handlers import Controller

log = logging.getLogger("tg")
SLOW_CONFIRMED = ("cl", "pn", "lv", "pk")     # подтверждённые действия с обращением к бирже: отвечаем на callback до выполнения
PARSE_FAIL = "не удалось разобрать текст"


async def _answer(q: CallbackQuery, text: str | None, alert: bool = False) -> bool:
    """answerCallbackQuery: текст усечён до лимита Telegram (200); сбой (запрос устарел, сеть) не должен мешать показу панели."""
    try:
        await q.answer(tx.trunc(text, TOAST_MAX) if text else text, show_alert=alert)
        return True
    except Exception as e:
        log.warning("answerCallbackQuery не удался: %s", type(e).__name__)
        return False


async def _respond(q: CallbackQuery, panel: Panel, chat_id: int, s: Screen, answered: bool = False) -> None:
    """Ответ на нажатие: всплывающая подсказка (если ещё не отвечали), затем экран в панель — ВСЕГДА, даже если ответ не прошёл."""
    if not answered:
        await _answer(q, s.toast, s.alert)
    await panel.show(q.bot, chat_id, s)


def build_router(ctl: Controller, panel: Panel) -> Router:
    r = Router()

    async def open_panel(m: Message, onboarding: bool) -> None:
        panel.touch()
        if onboarding and not ctl.st.get("onboarded", False):
            hint = "Торговля выключена — включите её кнопкой, когда будете готовы."
            await m.answer(f"{ONBOARDING}\n\n<i>{hint}</i>", parse_mode="HTML")
            ctl.st.set("onboarded", True)
        await panel.open(m.bot, m.chat.id, ctl.home())

    @r.message(Command("start"))
    async def start(m: Message):
        await open_panel(m, True)

    @r.message(Command("menu"))
    async def menu(m: Message):
        await open_panel(m, False)

    @r.message(F.text.contains("Leg("))
    async def leg_text(m: Message):
        """Текст «Копировать для бота» с сайта: разбираем, проверяем окно, показываем карточку в панели."""
        panel.touch()
        try:
            ids, errors = ctl.propose_from_text(m.text or "")
        except ValueError as e:
            ids, errors = [], [str(e)]
        except Exception:                    # любой неожиданный сбой разбора (в т.ч. OverflowError/RecursionError) — только ответ в панели
            log.exception("leg_text: разбор упал")
            ids, errors = [], [PARSE_FAIL]
        try:
            await m.delete()                 # панель одна: присланный текст в чате не оставляем
        except Exception:
            pass
        if not ids:
            await panel.show(m.bot, m.chat.id, ctl.add_screen(error="; ".join(errors)))
            return
        await panel.show(m.bot, m.chat.id, ctl.checking_screen(len(ids)))
        try:
            for pid in ids:
                await ctl.ensure_check(pid)
            s = await ctl.draft_screen(ids[0]) if len(ids) == 1 else ctl.add_screen(toast=f"Предложений: {len(ids)}")
            if errors:
                s.toast = tx.trunc("Не принято: " + "; ".join(errors), TOAST_MAX)
        except Exception as e:
            log.exception("leg_text failed")
            s = ctl.add_screen(error=f"не удалось подготовить карточку: {type(e).__name__}")
        await panel.show(m.bot, m.chat.id, s)

    @r.callback_query()
    async def cb(q: CallbackQuery):
        chat_id = q.message.chat.id if q.message else q.from_user.id
        msg_id = q.message.message_id if q.message else 0
        panel.touch()
        parts = kb.parse_cb(q.data)
        stale = not panel.is_panel(chat_id, msg_id)
        notice = stale and parts is not None and parts[0] == "ad" and len(parts) == 2     # «Открыть» под уведомлением с сайта
        answered = False
        try:
            if parts is None or (stale and parts != ("np",) and not notice):
                s = await ctl.handle(VIEW_HOME)
                s.toast = "Неизвестная кнопка — обновил панель" if parts is None else "Это старое меню — обновил панель"
            else:
                if parts[-1] == "y" and parts[0] in SLOW_CONFIRMED and ctl.is_armed(":".join(parts[:-1])):
                    answered = await _answer(q, EXECUTING_MSG)      # долгое действие на бирже: отпускаем «часики» сразу
                s = await ctl.handle(parts)
        except Exception as e:
            log.exception("handler failed")
            if not answered:
                await _answer(q, f"Ошибка: {type(e).__name__}", True)
                return
            s = ctl.home()                    # подсказка уже потрачена: ошибку показываем строкой в панели
            s = dataclasses.replace(s, text=s.text + f"\n\n⚠️ <b>Ошибка:</b> {esc(type(e).__name__)}", toast=None)
        await _respond(q, panel, chat_id, s, answered)
        if notice and q.message:
            try:
                await q.message.delete()     # уведомление своё дело сделало: карточка уже в панели
            except Exception:
                pass

    return r
