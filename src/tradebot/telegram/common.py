"""Общие константы и мелкие функции экранов Telegram (без aiogram и без Controller): их импортируют все модули-миксины."""
from __future__ import annotations

from tradebot.telegram import texts as tx
from tradebot.telegram.texts import esc
from tradebot.trading.executor import SIZING_EQUITY_FRACTION

STALE_CONFIRM_MSG = "Подтверждение устарело"
DONE_CONFIRM_MSG = "Уже выполнено"          # повторный тап «Подтвердить» сразу после успешного
EXECUTING_MSG = "Выполняю…"            # ранний ответ на callback для долгих подтверждённых действий
PRESET_WEIGHTS = (0, 25, 50, 75, 100)
TOAST_MAX = 200  # лимит Telegram на текст answerCallbackQuery
RESERVE_PCT = round((1 - SIZING_EQUITY_FRACTION) * 100)       # технический резерв, %: 3
WORK_PCT = round(SIZING_EQUITY_FRACTION * 100)                # в работе, %: 97
VIEW_HOME = ("h",)       # callback идемпотентного просмотра экрана: по нему идёт автообновление панели
VIEW_POS = ("pos",)
VIEW_SIGNALS = ("sg",)
ONBOARDING = (
    "👋 <b>Бот автоматической торговли BTCUSDT</b> (фьючерс Bybit)\n\n"
    "1️⃣ Каждый час считает сигналы стратегий и определяет целевую позицию.\n"
    "2️⃣ Если торговля включена — приводит позицию на бирже к цели; размер считается от всего баланса счёта "
    f"(технический резерв {RESERVE_PCT}%), на бирже всегда стоит защитный стоп.\n"
    "3️⃣ Всё управление — кнопками в одном сообщении-панели; любое важное действие подтверждается."
)


def P(x: float) -> str:
    """Доля -> целые проценты: 0.5 -> «50%»."""
    return f"{round(x * 100)}%"


def kb_quote(desc: str) -> str:
    """Описание стратегии в <blockquote>; пустое — пропускается."""
    return tx.bq(esc(desc)) if desc else ""

