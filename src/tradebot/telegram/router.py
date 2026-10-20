"""Роутер aiogram: /start, /menu, текст «Копировать для бота», callback-кнопки панели."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from tradebot.telegram import keyboards as kb
from tradebot.telegram import texts as tx
from tradebot.telegram.common import ONBOARDING, RETIRED_CAP_MSG, STALE_BUTTON_MSG, TOAST_MAX, VIEW_HOME
from tradebot.telegram.panel import Panel
from tradebot.telegram.texts import esc

if TYPE_CHECKING:
    from tradebot.telegram.handlers import Controller

log = logging.getLogger("tg")


def build_router(ctl: Controller, panel: Panel) -> Router:
    r = Router()

    async def open_panel(m: Message, onboarding: bool) -> None:
        panel.touch()
        if onboarding and not ctl.st.get("onboarded", False):
            warn = ctl.config_warning()
            hint = "Торговля выключена — включите её кнопкой, когда будете готовы."
            await m.answer(f"{ONBOARDING}\n\n" + (f"<b>{esc(warn)}</b>\n" if warn else "") + f"<i>{hint}</i>", parse_mode="HTML")
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
        try:
            if parts is None or (stale and parts != ("np",) and not notice):
                s = await ctl.handle(VIEW_HOME)
                toast = "Неизвестная кнопка — обновил панель" if parts is None else "Это старое меню — обновил панель"
                if parts is None and isinstance(q.data, str) and q.data.split(":")[0] == "cap":
                    toast = RETIRED_CAP_MSG       # кнопка из старого сообщения: настройка капитала удалена
                elif parts is None and q.data == "pp":
                    toast = STALE_BUTTON_MSG      # кнопка удалённого экрана о режиме запуска
            else:
                s = await ctl.handle(parts)
                toast = s.toast
        except Exception as e:
            log.exception("handler failed")
            await q.answer(f"Ошибка: {type(e).__name__}", show_alert=True)
            return
        await q.answer(toast, show_alert=s.alert)
        await panel.show(q.bot, chat_id, s)
        if notice and q.message:
            try:
                await q.message.delete()     # уведомление своё дело сделало: карточка уже в панели
            except Exception:
                pass

    return r
