"""Сборка aiogram Bot/Dispatcher: middleware владельца + роутер меню + панель."""
from __future__ import annotations

from aiogram import Bot, Dispatcher

from tradebot.telegram.handlers import Controller
from tradebot.telegram.middleware import OwnerOnly
from tradebot.telegram.panel import Panel
from tradebot.telegram.router import build_router


def build(token: str, owner_id: int, ctl: Controller) -> tuple[Bot, Dispatcher, Panel]:
    bot = Bot(token)
    dp = Dispatcher()
    dp.update.outer_middleware(OwnerOnly(owner_id))
    panel = Panel(ctl.st, ctl.handle)
    dp.include_router(build_router(ctl, panel))

    async def _mark_old_panel(bot: Bot) -> None:
        await panel.mark_idle_on_start(bot)      # панель из прошлого запуска не должна выглядеть живой

    dp.startup.register(_mark_old_panel)
    return bot, dp, panel
