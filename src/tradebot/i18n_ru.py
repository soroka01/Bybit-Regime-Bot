"""Русские переводы для tradebot.i18n.tr(): ключ - английская строка из кода (msgid), значение - русский текст. Подстановки
(%s, %d, %+.3f, %(x)s, {x}) в значении те же и в том же порядке, что в ключе (tests/test_i18n.py это проверяет, как и то, что
у каждого msgid есть запись, а у каждой записи - место использования). Telegram-тексты сюда не входят."""
from __future__ import annotations

RU: dict[str, str] = {
    # ---- trading/config.py: ConfigError ----
    "APP_LANGUAGE must be 'en' or 'ru' (empty = en): fix or remove it in .env":
        "APP_LANGUAGE должен быть 'en' или 'ru' (пусто = en): исправьте или удалите его в .env",
    "Set TELEGRAM_BOT_TOKEN and a numeric TELEGRAM_OWNER_ID in .env":
        "Укажите TELEGRAM_BOT_TOKEN и числовой TELEGRAM_OWNER_ID в .env",
    "TELEGRAM_BOT_TOKEN has an invalid format (expected <number>:<string>, as issued by @BotFather)":
        "TELEGRAM_BOT_TOKEN имеет неверный формат (ожидается <число>:<строка>, как выдаёт @BotFather)",
    "PAPER mode is no longer supported: remove PAPER from .env":
        "режим PAPER больше не поддерживается: удалите PAPER из .env",
    "testnet (BYBIT_ENV=testnet) is no longer supported: the bot runs on mainnet only, remove BYBIT_ENV from .env":
        "тестовая сеть (BYBIT_ENV=testnet) больше не поддерживается: бот работает только на mainnet, удалите BYBIT_ENV из .env",
    "BYBIT_ENV is no longer used (the bot runs on mainnet only): remove BYBIT_ENV from .env":
        "BYBIT_ENV больше не используется (бот работает только на mainnet): удалите BYBIT_ENV из .env",
    "Set BYBIT_API_KEY and BYBIT_API_SECRET in .env (permissions: Contract Orders + Positions, no Withdraw)":
        "Укажите BYBIT_API_KEY и BYBIT_API_SECRET в .env (права: Contract Orders + Positions, без Withdraw)",

    # ---- check.py: отчёт --check ----
    "[OK]       ": "[ОК]       ",
    "[WARN]     ": "[ВНИМАНИЕ] ",
    "[ERROR]    ": "[ОШИБКА]   ",
    "Check (read-only: no orders, no account setting changes on the exchange; bot.db is not opened)":
        "Проверка (только чтение: ни ордеров, ни изменений настроек на бирже; bot.db не открывается)",
    "exchange time: clock offset {offset:+d} ms": "время биржи: поправка часов {offset:+d} мс",
    "API key rejected by the exchange or its permissions could not be read: {error}":
        "ключ API отклонён биржей или права не прочитаны: {error}",
    "API key is read-only: trading is blocked. Create a key with Contract Orders + Positions (Read-Write) permissions":
        "ключ API только на чтение: торговля заблокирована. Создайте ключ с правами Contract Orders + Positions (Read-Write)",
    "API key has the Withdraw permission: trading is blocked. Issue a key without Withdraw":
        "у ключа API есть право вывода (Withdraw): торговля заблокирована. Выпустите ключ без Withdraw",
    "API key: trading allowed (write access enabled), no Withdraw permission":
        "ключ API: торговать можно (запись разрешена), права вывода (Withdraw) нет",
    "account settings could not be read: {error}": "настройки аккаунта не прочитаны: {error}",
    "leverage {lev:g}x, {margin} margin, {mode} mode": "плечо {lev:g}x, {margin} маржа, {mode} режим",
    "isolated": "изолированная",
    "NOT isolated": "НЕ изолированная",
    "one-way": "односторонний",
    "NOT one-way": "НЕ односторонний",
    ". The bot will try to switch this itself on startup, but the exchange may refuse while a position is open; "
    "switch it manually in the Bybit app (Isolated Margin, One-Way Mode)":
        ". Бот при старте попробует переключить это сам, но с открытой позицией биржа может не разрешить; "
        "переключите вручную в приложении Bybit (Isolated Margin, One-Way Mode)",
    ". This is above the bot's ceiling ({ceiling}x): on startup the bot will set its own leverage":
        ". Это выше потолка бота ({ceiling}x): при старте бот выставит своё плечо",
    "account: ": "аккаунт: ",
    "balance could not be read (a unified UTA account with USDT is required): {error}":
        "баланс не прочитан (нужен единый аккаунт UTA с USDT): {error}",
    "balance: equity {equity:.2f} $, position sizing base {base:.2f} $":
        "баланс: equity {equity:.2f} $, база для размера позиции {base:.2f} $",
    "the balance used for position sizing is zero: the bot cannot open a position":
        "баланс для расчёта размера равен нулю: бот не сможет открыть позицию",
    "position could not be read: {error}": "позиция не прочитана: {error}",
    "position: none": "позиция: нет",
    "exchange stop {stop:g}": "биржевой стоп {stop:g}",
    "NO exchange stop (the bot will set it on startup)": "БЕЗ биржевого стопа (бот выставит его при старте)",
    "position: {side} {qty:g} BTC, entry {entry:g}, {stop}": "позиция: {side} {qty:g} BTC, вход {entry:g}, {stop}",
    "long": "лонг",
    "short": "шорт",
    "exchange unavailable: {error}. The check did not finish": "биржа недоступна: {error}. Проверка не завершена",
    "exchange response could not be parsed ({exc}): the check did not finish":
        "ответ биржи не разобран ({exc}): проверка не завершена",
    "Result: {problems} problem(s) - the bot should not be started as is (code {code})":
        "Итог: проблем: {problems} - в таком виде запускать бота не стоит (код {code})",
    "Result: all good (code {code})": "Итог: всё в порядке (код {code})",
    "Configuration error: {error}": "Ошибка конфигурации: {error}",
    ".env configuration: OK (Telegram token and numeric owner id are set, Bybit keys are set)":
        "Конфигурация .env: в порядке (токен Telegram и числовой id владельца заданы, ключи Bybit заданы)",

    # ---- app.py ----
    "web dashboard unavailable (%s: %s) — continuing without it": "веб-дашборд недоступен (%s: %s) — продолжаю без него",
    "dashboard: http://127.0.0.1:8000": "дашборд: http://127.0.0.1:8000",
    "task {name} was cancelled": "задача {name} была отменена",
    "task {name} finished without an error although nobody asked it to stop":
        "задача {name} завершилась без ошибки, хотя остановки никто не просил",
    "task %s failed: %r": "задача %s упала: %r",
    "time sync: %s": "синхронизация времени: %s",
    "stopping...": "останавливаюсь...",
    "trading operation did not finish within 30 s": "торговая операция не завершилась за 30 с",
    "BTCUSDT trading bot for Bybit (mainnet only, real money)": "Бот торговли BTCUSDT на Bybit (только mainnet, реальные деньги)",
    "check .env, the API key permissions and the account settings (read-only) and exit; the bot is not started: "
    "no bot.db, no bot.lock, no Telegram, no orders. Exit codes: 0 - all good, 4 - a problem found, 1 - exchange unavailable":
        "проверить .env, права API-ключа и настройки аккаунта (только чтение) и выйти; бот не запускается: "
        "без bot.db, без блокировки bot.lock, без Telegram и ордеров. Коды: 0 - в порядке, 4 - есть проблема, 1 - биржа недоступна",
    "The bot is already running (storage/state/bot.lock is held). Close the other instance.":
        "Бот уже запущен (занят storage/state/bot.lock). Закройте другой экземпляр.",
    "starting": "старт",
}
