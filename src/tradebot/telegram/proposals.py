"""Предложения стратегий с сайта («В бота»): веб-поток кладёт строку в таблицу proposals (state.submit_proposals), а здесь
владельцу уходит уведомление с кнопкой «Открыть» — карточка с проверкой и выбором веса открывается в панели (legflow.LegFlow).

Опрос таблицы раз в POLL_S секунд (один дешёвый SELECT): отдельный канал «веб-поток -> цикл событий» не нужен, а работает и
дашборд, запущенный отдельно от бота (python -m tradebot.web)."""
from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramBadRequest

from tradebot.telegram.panel import is_parse_error
from tradebot.telegram.texts import strip_html

log = logging.getLogger("proposals")
POLL_S = 3.0


class ProposalWatcher:
    def __init__(self, ctl, owner_id: int, poll_s: float = POLL_S):
        self.ctl, self.owner_id, self.poll_s = ctl, owner_id, poll_s
        self.bot = None
        self._ready = asyncio.Event()

    def attach(self, bot) -> None:
        self.bot = bot
        self._ready.set()

    async def _send(self, text: str, markup) -> None:
        try:
            await self.bot.send_message(self.owner_id, text, reply_markup=markup, parse_mode="HTML")
        except TelegramBadRequest as e:
            if not is_parse_error(e):
                raise
            await self.bot.send_message(self.owner_id, strip_html(text), reply_markup=markup, parse_mode=None)

    async def tick(self) -> int:
        """Объявляет новые предложения С САЙТА (из присланного в чат текста карточка уже в панели). Проверка окна — до уведомления,
        чтобы в нём был вердикт; статус new -> announced забирается атомарно уже после неё: если за это время владелец решил
        судьбу предложения, оно не воскреснет. Возвращает число отправленных уведомлений."""
        st = self.ctl.st
        st.expire_proposals()
        n = 0
        for row in st.proposals("new", source="web"):
            pid = row["id"]
            await self.ctl.ensure_check(pid)
            if not st.claim_proposal(pid):
                continue
            note = self.ctl.notice(pid)
            try:
                if note is not None:
                    await self._send(*note)
            except BaseException:
                st.unclaim_proposal(pid)                # сбой Telegram -> повтор на следующем тике
                raise
            n += 1
        return n

    async def run(self) -> None:
        await self._ready.wait()
        fails = 0
        while True:
            try:
                await self.tick()
                fails = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001  уведомления о предложениях никогда не мешают торговле
                fails += 1
                if fails in (1, 10) or fails % 100 == 0:
                    log.warning("proposals tick (сбоев подряд: %d): %s: %s", fails, type(e).__name__, e)
            await asyncio.sleep(min(self.poll_s * 2 ** min(fails, 6), 300.0))      # при сбоях — реже: до 5 минут
