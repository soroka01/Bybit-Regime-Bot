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

    # ---- trading/executor.py (только log.*: notify/events/return-значения остаются русскими) ----
    "size of the current position %s BTC accepted as held (bot update)":
        "размер текущей позиции %s BTC принят как удерживаемый (обновление бота)",
    "order %s is ambiguous, but the position changed": "ордер %s неоднозначен, но позиция изменилась",
    "fill accounting (does not affect trading)": "учёт исполнения (на торговлю не влияет)",
    "notification about an exchange-side close": "оповещение о закрытии биржей",
    "fill notification": "оповещение об исполнении",
    "trade results backfill: the exchange did not respond within %.0f s — skipping":
        "добор итогов сделок: биржа не ответила за %.0f с — пропускаю",
    "trade results backfill skipped: %s": "добор итогов сделок пропущен: %s",
    "trade results backfill": "добор итогов сделок",
    "backfilled trade result %s: %+.4f $ (fees %.4f, funding %+.4f) order %s":
        "добор итога сделки %s: %+.4f $ (комиссии %.4f, фандинг %+.4f) ордер %s",
    "trade results backfill: exchange records %d, added %d, updated %d, unchanged %d":
        "добор итогов сделок: записей биржи %d, добавлено %d, обновлено %d, без изменений %d",
    "target exposure changed — latch released": "целевая экспозиция изменилась — защёлка снята",
    "target size is below minOrderQty — target 0": "целевой размер ниже minOrderQty — цель 0",
    "kill-switch: cancelling orders after the repeated close: %s": "kill-switch: отмена ордеров после повторного закрытия: %s",
    "kill-switch: repeated close failed: %s": "kill-switch: повторное закрытие не удалось: %s",

    # ---- trading/runner.py, market_feed.py, state.py (только log.*) ----
    "acting %d min late (after a restart/pause)": "действую с опозданием на %d мин (после перезапуска/паузы)",
    "bar %s target %+.3f -> %s": "бар %s цель %+.3f -> %s",
    "reconcile: %s": "сверка: %s",
    "trade results backfill: %s": "добор итогов сделок: %s",
    "the last bar is behind the expected one (%s) — one catch-up recalculation":
        "последний бар отстаёт от ожидаемого (%s) — один догоняющий пересчёт",
    "DB maintenance: %s": "обслуживание БД: %s",
    "DB maintenance: backup %s, purged %s": "обслуживание БД: копия %s, удалено %s",
    "could not get the server time: %s": "не удалось получить серверное время: %s",
    "data load (attempt %d/%d): %s": "загрузка данных (попытка %d/%d): %s",
    "DB migration v1 postponed: %s": "миграция БД v1 отложена: %s",
    "backup before the DB migration: %s": "копия перед миграцией БД: %s",
    "DB migration v1 failed: %s": "миграция БД v1 не выполнена: %s",
    "DB migration v1 completed": "миграция БД v1 выполнена",

    # ---- market/klines.py, derivs.py, guard.py (только log.*) ----
    "%s: gaps in the data: %d, %d bars missing; first after %s, last before %s":
        "%s: пропуски в данных: %d шт., не хватает %d баров; первый после %s, последний до %s",
    "klines %s %s: the file is busy with another update — using the existing data without a top-up":
        "klines %s %s: файл занят другим обновлением — использую имеющиеся данные без докачки",
    "derivs %s: the file is busy with another update — using the existing data without a top-up":
        "derivs %s: файл занят другим обновлением — использую имеющиеся данные без докачки",
    "%s: could not check the freshness of the series": "%s: не удалось проверить свежесть ряда",
    "~year {value}": "~год {value}",
    "%s: data forward-filled (ffill) from old values: last record %s, lag behind the close of the last "
    "candle %s, candles with a value older than %s: %d":
        "%s: данные протянуты вперёд (ffill) со старых значений: последняя запись %s, отставание от закрытия "
        "последней свечи %s, свечей со значением старше %s: %d",
    "file %s has been busy with another update for longer than %.0f s": "файл %s занят другим обновлением дольше %.0f с",

    # ---- web/lanes.py, cache.py, common.py, meta.py, wf.py, research/sweep.py ----
    "request superseded by a newer one": "запрос вытеснен более новым",
    "the server is busy with another calculation": "сервер занят другим расчётом",
    "a data download is already in progress": "уже идёт загрузка данных",
    "the server is shutting down": "сервер останавливается",
    "the client cancelled the request": "клиент отменил запрос",
    "calculation took longer than {seconds} s": "расчёт дольше {seconds} с",
    "%s/%s: the job did not finish within %.0f s, but %d threads are already abandoned — the slot stays occupied":
        "%s/%s: задание не завершилось за %.0f с, но брошенных потоков уже %d — слот остаётся занятым",
    "%s/%s: the job did not finish within %.0f s (timeout %.0f s) — the semaphore was replaced, the thread keeps running":
        "%s/%s: задание не завершилось за %.0f с (таймаут %.0f с) — семафор заменён, поток продолжает работать",
    "cache DB error (%s) — skipped without a reset": "cache DB error (%s) — пропуск без сброса",
    "calculation time exceeded": "превышено время расчёта",
    "unknown strategy: {name}": "неизвестная стратегия: {name}",
    "no walk-forward results for leverage {lev}x (available: {available})":
        "нет результатов walk-forward для плеча {lev}x (есть: {available})",
    "none": "нет",
    "unknown symbol": "неизвестный символ",
    "no such curve": "такой кривой нет",
    "no column {column} in the data": "в данных нет колонки {column}",
    "sweep %s %s: strategy «%s» skipped — %s": "sweep %s %s: стратегия «%s» пропущена — %s",
    "skipped: {why}": "пропущена: {why}",

    # ---- web/api.py, security.py, schemas.py, data.py, backtests.py, stats.py: тексты ошибок API ----
    "the database is temporarily unavailable": "база данных временно недоступна",
    "the results file is corrupted ({kind})": "файл результатов повреждён ({kind})",
    "no key/column {key} in the data or the request": "в данных или запросе нет ключа/колонки {key}",
    "no walk-forward results file: {detail}": "нет файла результатов walk-forward: {detail}",
    "unknown range «{range}»: allowed {allowed}": "неизвестный диапазон «{range}»: допустимо {allowed}",
    "unknown range {range}: allowed {allowed}": "неизвестный диапазон {range}: допустимо {allowed}",
    "failed to update the data: ": "не удалось обновить данные: ",
    "The UI is not built": "Интерфейс не собран",
    "Run <code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code>.":
        "Выполните <code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code>.",
    "cross-site is forbidden": "cross-site запрещён",
    "cross-origin is forbidden": "cross-origin запрещён",
    "the request body must be application/json": "тело запроса должно быть application/json",
    "unknown timeframe (allowed: {allowed})": "неизвестный таймфрейм (допустимо: {allowed})",
    "No local data for {symbol} {tf}: {missing}. Download it explicitly (POST /api/data/fetch).":
        "Нет локальных данных {symbol} {tf}: {missing}. Скачайте их явно (POST /api/data/fetch).",
    "the data file is empty (or has fewer than 2 rows): {name}. Download the data again.":
        "файл данных пуст (или в нём меньше 2 строк): {name}. Скачайте данные заново.",
    "unknown symbol or timeframe": "неизвестный символ или таймфрейм",
    "candles updated, but the derivatives failed to load: {exc}: {err}":
        "свечи обновлены, но деривативы не загрузились: {exc}: {err}",
    "no column {column} in the data for strategy {strategy}": "в данных нет колонки {column} для стратегии {strategy}",
    "data": "данные",
    "too little data for the selected period": "слишком мало данных за выбранный период",
    "pick strategy parameters (two different ones for 2D)": "нужно выбрать параметры стратегии (для 2D — два разных)",
    "too many cells (maximum {max})": "слишком много ячеек (максимум {max})",
    "leg timeframe {tf} is not supported: allowed {allowed} (not shorter than the base grid {base} and multiples of it)":
        "таймфрейм ноги {tf} не поддерживается: допустимы {allowed} (не короче базовой сетки {base} и кратные ей)",
    "legs must differ by the strategy@timeframe pair": "ноги должны различаться по паре стратегия@таймфрейм",
    "unknown criterion: {criterion}": "неизвестный критерий: {criterion}",
    "too little data to split into IS/OOS": "слишком мало данных для разделения IS/OOS",
}
