"""Конфигурация из .env и константы безопасности. Бот работает ТОЛЬКО на основной сети Bybit (mainnet): тестовой сети и режима
без ордеров (PAPER) нет. Ключи нигде не логируются."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv

from tradebot import i18n
from tradebot.i18n import tr
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
    language: str = "en"            # APP_LANGUAGE: язык консоли/логов/сайта по умолчанию ("en" | "ru"); Telegram всегда русский

    def __repr__(self) -> str:  # не светим секреты
        return f"Env(owner_id={self.owner_id})"


# значения PAPER, которые раньше включали режим без ордеров: теперь это ошибка конфигурации (а не тихая торговля)
_PAPER_TRUTHY = ("1", "true", "yes", "on")


def load_env(environ: dict | None = None) -> Env:
    if environ is None:
        load_dotenv(ROOT / ".env")
        environ = os.environ
    g = lambda k: (environ.get(k) or "").strip()  # noqa: E731
    # Язык - первым: тексты всех следующих ошибок уже на нём. Нет ключа вовсе -> то, что i18n.lang() нашёл в окружении/.env;
    # пустое значение = en; неизвестное - ошибка конфигурации (fail-closed, как BYBIT_ENV).
    language = g("APP_LANGUAGE").lower() if "APP_LANGUAGE" in environ else i18n.lang()
    if language == "":
        language = "en"
    if language not in i18n.LANGS:
        raise ConfigError(tr("APP_LANGUAGE must be 'en' or 'ru' (empty = en): fix or remove it in .env"))
    i18n.set_language(language)
    token, owner = g("TELEGRAM_BOT_TOKEN"), g("TELEGRAM_OWNER_ID")
    if not token or not owner.lstrip("-").isdigit():
        raise ConfigError(tr("Set TELEGRAM_BOT_TOKEN and a numeric TELEGRAM_OWNER_ID in .env"))
    if not TOKEN_RE.fullmatch(token):
        # опечатка в токене иначе всплыла бы только как бесконечные сбои polling после старта (и перезапуски run.bat)
        raise ConfigError(tr("TELEGRAM_BOT_TOKEN has an invalid format (expected <number>:<string>, as issued by @BotFather)"))
    # Устаревшие переменные: «включающие» значения отклоняются (fail-closed), чтобы старый .env не запустил бота иначе, чем ждёт
    # владелец; «выключающие» (PAPER=0, BYBIT_ENV=mainnet, I_UNDERSTAND_MAINNET) молча игнорируются.
    if g("PAPER").lower() in _PAPER_TRUTHY:
        raise ConfigError(tr("PAPER mode is no longer supported: remove PAPER from .env"))
    bybit_env = g("BYBIT_ENV").lower()
    if bybit_env == "testnet":
        raise ConfigError(tr("testnet (BYBIT_ENV=testnet) is no longer supported: the bot runs on mainnet only, remove BYBIT_ENV from .env"))
    if bybit_env not in ("", "mainnet"):
        raise ConfigError(tr("BYBIT_ENV is no longer used (the bot runs on mainnet only): remove BYBIT_ENV from .env"))
    if not (g("BYBIT_API_KEY") and g("BYBIT_API_SECRET")):
        raise ConfigError(tr("Set BYBIT_API_KEY and BYBIT_API_SECRET in .env (permissions: Contract Orders + Positions, no Withdraw)"))
    return Env(token, int(owner), g("BYBIT_API_KEY"), g("BYBIT_API_SECRET"), g("TZ_NAME") or "Europe/Moscow", language)
