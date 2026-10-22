"""`python -m tradebot.app --check`: проверка конфигурации, ключа и аккаунта БЕЗ запуска бота. Только чтение: подписанные и
публичные GET-запросы к Bybit; не открывает bot.db, не берёт bot.lock, не пишет в bot.log, не шлёт сообщения в Telegram, не
отправляет ордера и не меняет настройки аккаунта (setup_account НЕ вызывается). Секреты (токен, ключ, секрет) не печатаются.

Коды выхода: 0 - всё в порядке; 4 - что-то не так в конфигурации/ключе/настройках аккаунта (бота в таком виде запускать нельзя);
1 - биржа недоступна (сеть, 5xx, лимиты): проверить не удалось. Правила блокировки ключа те же, что при старте бота
(exchange.key_block_reason): ключ только на чтение или с правом вывода (Withdraw) торговлю блокирует."""
from __future__ import annotations

import asyncio
from typing import Callable

from tradebot.i18n import tr
from tradebot.trading.config import MAINNET_URL, MAX_LEVERAGE, ConfigError, load_env
from tradebot.trading.exchange import BybitClient, ExchangeError, NetworkError, key_block_reason

EXIT_OK, EXIT_NETWORK, EXIT_PROBLEM = 0, 1, 4


# маркеры строк отчёта: язык известен только во время вызова, поэтому функции, а не константы
def OK() -> str:  # noqa: N802
    return tr("[OK]       ")


def WARN() -> str:  # noqa: N802
    return tr("[WARN]     ")


def BAD() -> str:  # noqa: N802
    return tr("[ERROR]    ")


async def run_check(client, out: Callable[[str], None] = print) -> int:
    """Проверки по порядку; клиент - любой объект с методами BybitClient (в тестах - на MockTransport). Возвращает код выхода.
    Сеть недоступна -> печатается то, что уже проверено, и код 1; остальные ошибки биржи/настроек - проблема (код 4)."""
    problems = 0

    def bad(text: str) -> None:
        nonlocal problems
        problems += 1
        out(BAD() + text)

    out(tr("Check (read-only: no orders, no account setting changes on the exchange; bot.db is not opened)"))
    try:
        await client.sync_time()
        out(OK() + tr("exchange time: clock offset {offset:+d} ms").format(offset=client.offset_ms))

        try:
            ki = await client.key_info()
        except NetworkError:
            raise
        except ExchangeError as e:
            bad(tr("API key rejected by the exchange or its permissions could not be read: {error}").format(error=e))
        else:
            reason = key_block_reason(ki)
            if reason == "read_only":
                bad(tr("API key is read-only: trading is blocked. Create a key with Contract Orders + Positions (Read-Write) permissions"))
            elif reason == "withdraw":
                bad(tr("API key has the Withdraw permission: trading is blocked. Issue a key without Withdraw"))
            else:
                out(OK() + tr("API key: trading allowed (write access enabled), no Withdraw permission"))

        try:
            acc = await client.read_account()
        except NetworkError:
            raise
        except ExchangeError as e:
            bad(tr("account settings could not be read: {error}").format(error=e))
        else:
            lev = acc["leverage"]
            line = tr("leverage {lev:g}x, {margin} margin, {mode} mode").format(
                lev=lev, margin=tr("isolated") if acc["isolated"] else tr("NOT isolated"),
                mode=tr("one-way") if acc["one_way"] else tr("NOT one-way"))
            if not acc["isolated"] or not acc["one_way"]:
                bad(line + tr(". The bot will try to switch this itself on startup, but the exchange may refuse while a position is open; "
                              "switch it manually in the Bybit app (Isolated Margin, One-Way Mode)"))
            elif lev > MAX_LEVERAGE:
                bad(line + tr(". This is above the bot's ceiling ({ceiling}x): on startup the bot will set its own leverage").format(ceiling=MAX_LEVERAGE))
            else:
                out(OK() + tr("account: ") + line)

        try:
            eq, bal = await client.equity(), await client.sizing_balance()
        except NetworkError:
            raise
        except ExchangeError as e:
            bad(tr("balance could not be read (a unified UTA account with USDT is required): {error}").format(error=e))
        else:
            out(OK() + tr("balance: equity {equity:.2f} $, position sizing base {base:.2f} $").format(equity=eq, base=bal))
            if not bal > 0:
                bad(tr("the balance used for position sizing is zero: the bot cannot open a position"))

        try:
            pos = await client.position()
        except NetworkError:
            raise
        except ExchangeError as e:
            bad(tr("position could not be read: {error}").format(error=e))
        else:
            if pos is None:
                out(OK() + tr("position: none"))
            else:
                stop = (tr("exchange stop {stop:g}").format(stop=pos.stop) if pos.stop
                        else tr("NO exchange stop (the bot will set it on startup)"))
                out((OK() if pos.stop else WARN()) + tr("position: {side} {qty:g} BTC, entry {entry:g}, {stop}").format(
                    side=tr("long") if pos.side == "long" else tr("short"), qty=pos.qty, entry=pos.entry, stop=stop))
    except NetworkError as e:
        out(BAD() + tr("exchange unavailable: {error}. The check did not finish").format(error=e))
        return EXIT_NETWORK
    except (ExchangeError, KeyError, IndexError, AttributeError, TypeError, ValueError) as e:      # ответ биржи не того вида (например, не JSON Bybit)
        out(BAD() + tr("exchange response could not be parsed ({exc}): the check did not finish").format(exc=type(e).__name__))
        return EXIT_NETWORK
    if problems:
        out(tr("Result: {problems} problem(s) - the bot should not be started as is (code {code})").format(problems=problems, code=EXIT_PROBLEM))
        return EXIT_PROBLEM
    out(tr("Result: all good (code {code})").format(code=EXIT_OK))
    return EXIT_OK


def check_main(environ: dict | None = None, client_factory: Callable[[str, str, str], object] | None = None,
               out: Callable[[str], None] = print) -> int:
    """Точка входа `--check`. environ - словарь переменных (в тестах), иначе .env проекта. client_factory(url, key, secret) - подмена
    клиента в тестах. Конфигурация проверяется теми же правилами, что при старте (устаревшие PAPER/BYBIT_ENV=testnet - ошибка)."""
    try:
        env = load_env(environ)
    except ConfigError as e:
        out(tr("Configuration error: {error}").format(error=e))
        return EXIT_PROBLEM
    out(tr(".env configuration: OK (Telegram token and numeric owner id are set, Bybit keys are set)"))
    client = (client_factory or BybitClient)(MAINNET_URL, env.api_key, env.api_secret)

    async def go() -> int:
        try:
            return await run_check(client, out)
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                await close()
    return asyncio.run(go())
