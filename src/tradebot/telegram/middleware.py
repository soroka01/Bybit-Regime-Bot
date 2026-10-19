"""Доступ только владельцу в личном чате: всё остальное молча игнорируется (и сообщения, и callback-запросы)."""
from __future__ import annotations

from aiogram import BaseMiddleware


class OwnerOnly(BaseMiddleware):
    def __init__(self, owner_id: int):
        self.owner_id = owner_id

    async def __call__(self, handler, event, data):
        # event — Update; пропускаем только message и callback_query
        msg, cb = getattr(event, "message", None), getattr(event, "callback_query", None)
        if msg is not None and cb is None:
            user, chat = msg.from_user, msg.chat
        elif cb is not None and msg is None:
            user, chat = cb.from_user, getattr(cb.message, "chat", None)
        else:
            return None
        if user is None or chat is None or user.id != self.owner_id or chat.type != "private" or chat.id != self.owner_id:
            return None
        return await handler(event, data)
