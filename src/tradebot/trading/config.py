"""Конфигурация из .env и константы безопасности. Ключи нигде не логируются."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from tradebot.paths import ROOT

SYMBOL = "BTCUSDT"
MAX_LEVERAGE = 3  # жёсткий потолок; менять только правкой кода
MAINNET_URL = "https://api.bybit.com"
TESTNET_URL = "https://api-testnet.bybit.com"
DASHBOARD_URL = "http://127.0.0.1:8000"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Env:
    bot_token: str
    owner_id: int
    bybit_env: str  # testnet | mainnet
    api_key: str
    api_secret: str
    paper: bool = True  # PAPER=1 (по умолчанию): только чтение, ни одного ордера
    tz_name: str = "Europe/Moscow"  # часовой пояс для отображения времени в Telegram

    @property
    def base_url(self) -> str:
        return MAINNET_URL if self.bybit_env == "mainnet" else TESTNET_URL

    @property
    def has_keys(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def __repr__(self) -> str:  # не светим секреты
        return f"Env(bybit_env={self.bybit_env!r}, owner_id={self.owner_id}, paper={self.paper})"


def load_env(environ: dict | None = None) -> Env:
    if environ is None:
        load_dotenv(ROOT / ".env")
        environ = os.environ
    g = lambda k: (environ.get(k) or "").strip()  # noqa: E731
    token, owner = g("TELEGRAM_BOT_TOKEN"), g("TELEGRAM_OWNER_ID")
    if not token or not owner.lstrip("-").isdigit():
        raise ConfigError("Укажите TELEGRAM_BOT_TOKEN и числовой TELEGRAM_OWNER_ID в .env")
    env = (g("BYBIT_ENV") or "testnet").lower()
    if env not in ("testnet", "mainnet"):
        raise ConfigError("BYBIT_ENV должен быть testnet или mainnet")
    if env == "mainnet" and g("I_UNDERSTAND_MAINNET") != "1":
        raise ConfigError("Для mainnet нужно ещё и I_UNDERSTAND_MAINNET=1 в .env")
    return Env(token, int(owner), env, g("BYBIT_API_KEY"), g("BYBIT_API_SECRET"), g("PAPER") != "0", g("TZ_NAME") or "Europe/Moscow")
