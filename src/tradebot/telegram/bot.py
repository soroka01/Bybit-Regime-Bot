"""Сборка aiogram Bot/Dispatcher: middleware владельца + роутер меню + панель."""
from __future__ import annotations

from aiogram import Bot, Dispatcher

from tradebot.telegram.handlers import Controller, build_router
from tradebot.telegram.middleware import OwnerOnly
from tradebot.telegram.panel import Panel


def build(token: str, owner_id: int, ctl: Controller) -> tuple[Bot, Dispatcher, Panel]:
    bot = Bot(token)
    dp = Dispatcher()
    dp.update.outer_middleware(OwnerOnly(owner_id))
    panel = Panel(ctl.st, ctl.handle)
    dp.include_router(build_router(ctl, panel))
    return bot, dp, panel
