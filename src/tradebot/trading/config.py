"""Конфигурация из .env и константы безопасности. Бот работает ТОЛЬКО на основной сети Bybit (mainnet): тестовой сети и режима
без ордеров (PAPER) нет. Ключи нигде не логируются."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv

from tradebot.paths import ROOT

SYMBOL = "BTCUSDT"
MAX_LEVERAGE = 3  # жёсткий потолок; менять только правкой кода
MAINNET_URL = "https://api.bybit.com"
# формат токена Telegram-бота от @BotFather: <числовой id>:<секрет>; проверяется только вид, значение нигде не выводится
TOKEN_RE = re.compile(r"[0-9]{5,}:[A-Za-z0-9_-]{20,}")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Env:
    bot_token: str
    owner_id: int
    api_key: str
    api_secret: str
    tz_name: str = "Europe/Moscow"  # часовой пояс для отображения времени в Telegram

    def __repr__(self) -> str:  # не светим секреты
        return f"Env(owner_id={self.owner_id})"


# значения PAPER, которые раньше включали режим без ордеров: теперь это ошибка конфигурации (а не тихая торговля)
_PAPER_TRUTHY = ("1", "true", "yes", "on")


def load_env(environ: dict | None = None) -> Env:
    if environ is None:
        load_dotenv(ROOT / ".env")
        environ = os.environ
    g = lambda k: (environ.get(k) or "").strip()  # noqa: E731
    token, owner = g("TELEGRAM_BOT_TOKEN"), g("TELEGRAM_OWNER_ID")
    if not token or not owner.lstrip("-").isdigit():
        raise ConfigError("Укажите TELEGRAM_BOT_TOKEN и числовой TELEGRAM_OWNER_ID в .env")
    if not TOKEN_RE.fullmatch(token):
        # опечатка в токене иначе всплыла бы только как бесконечные сбои polling после старта (и перезапуски run.bat)
        raise ConfigError("TELEGRAM_BOT_TOKEN имеет неверный формат (ожидается <число>:<строка>, как выдаёт @BotFather)")
    # Устаревшие переменные: «включающие» значения отклоняются (fail-closed), чтобы старый .env не запустил бота иначе, чем ждёт
    # владелец; «выключающие» (PAPER=0, BYBIT_ENV=mainnet, I_UNDERSTAND_MAINNET) молча игнорируются.
    if g("PAPER").lower() in _PAPER_TRUTHY:
        raise ConfigError("режим PAPER больше не поддерживается: удалите PAPER из .env")
    bybit_env = g("BYBIT_ENV").lower()
    if bybit_env == "testnet":
        raise ConfigError("тестовая сеть (BYBIT_ENV=testnet) больше не поддерживается: бот работает только на mainnet, удалите BYBIT_ENV из .env")
    if bybit_env not in ("", "mainnet"):
        raise ConfigError("BYBIT_ENV больше не используется (бот работает только на mainnet): удалите BYBIT_ENV из .env")
    if not (g("BYBIT_API_KEY") and g("BYBIT_API_SECRET")):
        raise ConfigError("Укажите BYBIT_API_KEY и BYBIT_API_SECRET в .env (права: Contract Orders + Positions, без Withdraw)")
    return Env(token, int(owner), g("BYBIT_API_KEY"), g("BYBIT_API_SECRET"), g("TZ_NAME") or "Europe/Moscow")
