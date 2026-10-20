"""`python -m tradebot.app --check`: проверка конфигурации, ключа и аккаунта БЕЗ запуска бота. Только чтение: подписанные и
публичные GET-запросы к Bybit; не открывает bot.db, не берёт bot.lock, не пишет в bot.log, не шлёт сообщения в Telegram, не
отправляет ордера и не меняет настройки аккаунта (setup_account НЕ вызывается). Секреты (токен, ключ, секрет) не печатаются.

Коды выхода: 0 - всё в порядке; 4 - что-то не так в конфигурации/ключе/настройках аккаунта (бота в таком виде запускать нельзя);
1 - биржа недоступна (сеть, 5xx, лимиты): проверить не удалось. Правила блокировки ключа те же, что при старте бота
(exchange.key_block_reason): ключ только на чтение или с правом вывода (Withdraw) торговлю блокирует."""
from __future__ import annotations

import asyncio
from typing import Callable

from tradebot.trading.config import MAINNET_URL, MAX_LEVERAGE, ConfigError, load_env
from tradebot.trading.exchange import BybitClient, ExchangeError, NetworkError, key_block_reason

EXIT_OK, EXIT_NETWORK, EXIT_PROBLEM = 0, 1, 4
OK, WARN, BAD = "[ОК]       ", "[ВНИМАНИЕ] ", "[ОШИБКА]   "


async def run_check(client, out: Callable[[str], None] = print) -> int:
    """Проверки по порядку; клиент - любой объект с методами BybitClient (в тестах - на MockTransport). Возвращает код выхода.
    Сеть недоступна -> печатается то, что уже проверено, и код 1; остальные ошибки биржи/настроек - проблема (код 4)."""
    problems = 0

    def bad(text: str) -> None:
        nonlocal problems
        problems += 1
        out(BAD + text)

    out("Проверка (только чтение: ни ордеров, ни изменений настроек на бирже; bot.db не открывается)")
    try:
        await client.sync_time()
        out(OK + f"время биржи: поправка часов {client.offset_ms:+d} мс")

        try:
            ki = await client.key_info()
        except NetworkError:
            raise
        except ExchangeError as e:
            bad(f"ключ API отклонён биржей или права не прочитаны: {e}")
        else:
            reason = key_block_reason(ki)
            if reason == "read_only":
                bad("ключ API только на чтение: торговля заблокирована. Создайте ключ с правами Contract Orders + Positions (Read-Write)")
            elif reason == "withdraw":
                bad("у ключа API есть право вывода (Withdraw): торговля заблокирована. Выпустите ключ без Withdraw")
            else:
                out(OK + "ключ API: торговать можно (запись разрешена), права вывода (Withdraw) нет")

        try:
            acc = await client.read_account()
        except NetworkError:
            raise
        except ExchangeError as e:
            bad(f"настройки аккаунта не прочитаны: {e}")
        else:
            lev = acc["leverage"]
            line = f"плечо {lev:g}x, {'изолированная' if acc['isolated'] else 'НЕ изолированная'} маржа, " \
                   f"{'односторонний' if acc['one_way'] else 'НЕ односторонний'} режим"
            if not acc["isolated"] or not acc["one_way"]:
                bad(line + ". Бот при старте попробует переключить это сам, но с открытой позицией биржа может не разрешить; "
                           "переключите вручную в приложении Bybit (Isolated Margin, One-Way Mode)")
            elif lev > MAX_LEVERAGE:
                bad(line + f". Это выше потолка бота ({MAX_LEVERAGE}x): при старте бот выставит своё плечо")
            else:
                out(OK + "аккаунт: " + line)

        try:
            eq, bal = await client.equity(), await client.sizing_balance()
        except NetworkError:
            raise
        except ExchangeError as e:
            bad(f"баланс не прочитан (нужен единый аккаунт UTA с USDT): {e}")
        else:
            out(OK + f"баланс: equity {eq:.2f} $, база для размера позиции {bal:.2f} $")
            if not bal > 0:
                bad("баланс для расчёта размера равен нулю: бот не сможет открыть позицию")

        try:
            pos = await client.position()
        except NetworkError:
            raise
        except ExchangeError as e:
            bad(f"позиция не прочитана: {e}")
        else:
            if pos is None:
                out(OK + "позиция: нет")
            else:
                stop = f"биржевой стоп {pos.stop:g}" if pos.stop else "БЕЗ биржевого стопа (бот выставит его при старте)"
                out((OK if pos.stop else WARN) + f"позиция: {'лонг' if pos.side == 'long' else 'шорт'} {pos.qty:g} BTC, вход {pos.entry:g}, {stop}")
    except NetworkError as e:
        out(BAD + f"биржа недоступна: {e}. Проверка не завершена")
        return EXIT_NETWORK
    except (ExchangeError, KeyError, IndexError, AttributeError, TypeError, ValueError) as e:      # ответ биржи не того вида (например, не JSON Bybit)
        out(BAD + f"ответ биржи не разобран ({type(e).__name__}): проверка не завершена")
        return EXIT_NETWORK
    if problems:
        out(f"Итог: проблем: {problems} - в таком виде запускать бота не стоит (код {EXIT_PROBLEM})")
        return EXIT_PROBLEM
    out(f"Итог: всё в порядке (код {EXIT_OK})")
    return EXIT_OK


def check_main(environ: dict | None = None, client_factory: Callable[[str, str, str], object] | None = None,
               out: Callable[[str], None] = print) -> int:
    """Точка входа `--check`. environ - словарь переменных (в тестах), иначе .env проекта. client_factory(url, key, secret) - подмена
    клиента в тестах. Конфигурация проверяется теми же правилами, что при старте (устаревшие PAPER/BYBIT_ENV=testnet - ошибка)."""
    try:
        env = load_env(environ)
    except ConfigError as e:
        out(f"Ошибка конфигурации: {e}")
        return EXIT_PROBLEM
    out("Конфигурация .env: в порядке (токен Telegram и числовой id владельца заданы, ключи Bybit заданы)")
    client = (client_factory or BybitClient)(MAINNET_URL, env.api_key, env.api_secret)

    async def go() -> int:
        try:
            return await run_check(client, out)
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                await close()
    return asyncio.run(go())
