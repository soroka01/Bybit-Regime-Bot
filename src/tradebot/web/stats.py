"""Статистика РЕАЛЬНОЙ торговой истории живого бота (вкладка «Статистика»). Только чтение: bot.db открывается read-only,
сеть и торговая логика не трогаются. Это НЕ бэктест стратегий — данные берутся из таблиц trades / equity / events / kv.

Модуль = чистые функции (на входе уже прочитанные строки БД и свечи, на выходе JSON-совместимый dict) + тонкая обвязка
ввода-вывода (read_db / load_market / get_stats) с кэшем в процессе. Ответ GET /api/stats?range=… — см. web/src/types.ts
(StatsResponse) и словарь `definitions` в самом ответе (подсказки для UI). Единицы: *_pct — ПРОЦЕНТЫ (1.5 = 1.5 %),
время — unix-секунды UTC (int), деньги — USDT, NaN/inf никогда не попадают в JSON (null).

ОПРЕДЕЛЕНИЯ
1) Сделка (round trip): fills из trades в порядке id, позиция со знаком, СРЕДНЯЯ цена входа (average cost). Сделка = «плоско ->
   плоско». Fill, меняющий знак позиции, делится на закрытие + открытие по той же цене и времени; частичные уменьшения
   реализуют P/L на уменьшенную часть, добавления усредняют цену. Fills с price<=0, qty<=0 или неизвестной стороной
   отбрасываются (warning bad_fills).
   Таблица trades ДЫРЯВАЯ: закрытия по стопу на бирже/ликвидации и неподтверждённые fills не пишутся (executor.detect_stopout
   только шлёт уведомление «Позиция исчезла (стоп/ликвидация)» -> events). Такое событие = СИНТЕТИЧЕСКОЕ закрытие: позиция
   принудительно обнуляется на ts события по реконструированной цене: стоп-уровень = средняя цена входа*(1 -/+ stop_pct),
   где stop_pct — ТЕКУЩАЯ kv s_stop_pct (не историческая); если часовые свечи вокруг события (предыдущая и текущая) его
   коснулись — цена выхода = стоп-уровень, иначе close свечи (если свечи недоступны — цена входа). Сделка помечается
   reconstructed=true, причина «стоп (реконструкция)». Если итоговая реплеем позиция не совпала с kv 'position' (сторона/объём) —
   отдаётся position_mismatch {replayed, actual}, ни одной из сторон мы не доверяем молча.
   Причина закрытия обычной сделки = trades.reason закрывающего fill (сырая строка; для группировок reason_group = текст до
   первой запятой).
2) Комиссии ОЦЕНОЧНЫЕ: notional fill * TAKER_FEE_EST (все fills — рыночные ордера, тейкер). Фандинг ОЦЕНОЧНЫЙ по локальной
   серии 1h-свечей: для каждого расчёта t в (вход, выход] (00/08/16 UTC): funding_paid = sign*qty*price_t*rate_t
   (лонг ПЛАТИТ при rate>0), price_t = OPEN часовой свечи, начинающейся в t; qty — объём позиции в момент t. Net = gross - fees -
   funding_paid. ret_pct = net / вход-notional (сумма notional всех увеличивающих fills); ret_margin_pct = net/(notional/плечо),
   плечо — ТЕКУЩЕЕ kv s_leverage (оценка). Фандинг после funding_coverage_until (parquet обновляется только явным POST) неизвестен:
   считается 0, сделка помечена funding_complete=false, warning funding_stale.
3) Окно [now - range, now]; сделка принадлежит окну по ВРЕМЕНИ ВЫХОДА. Открытая позиция — отдельно (open_position: нереализованный
   P/L из kv 'position' + оценка фандинга), в ROI по equity она входит. range=all: старт окна = первая сделка/точка equity.
4) Equity-метрики (авторитетны для ROI). Таблица equity — это КОШЕЛЁК аккаунта (UTA totalEquity: весь счёт, все монеты в USD,
   с нереализованным), а не «капитал бота»; ввод/вывод невидимы. Бот торгует на ВЕСЬ баланс счёта, поэтому заголовочный ROI
   («ROI счёта») = wallet_return_pct = eq_end/eq_start - 1 (Δ кошелька за окно / кошелёк на начале окна). eq_start = последняя точка <= начала окна,
   иначе первая точка (starts_before_data=true, eq_start_ts). Макс. просадка — по кошельку внутри окна. Дневные доходности —
   по ПОСЛЕДНЕЙ точке каждого дня UTC; доходность дня считается только если есть точка предыдущего дня (пропущенные дни
   пропускаются, нулями не заполняются). Sharpe/Sortino годовые sqrt(365) только при >= 14 дневных доходностей (Sortino null без
   отрицательных дней), Calmar — при >= 30 и просадке > 0. Сверка (reconciliation): unexplained = Δкошелька - (Σ net сделок
   окна + оценка открытой позиции - вклад позиции, уже открытой на старте окна, оценённый по закрытию 1h-свечи на старте); если
   цену на старте взять нельзя — null с причиной. Это «непояснённое» в основном внебот-движение кошелька + ошибка оценки.
5) Метрики сделок (окно, по выходу): число, win rate, средние win/loss, payoff, profit factor (null без убытков),
   матожидание (USDT и %), лучшая/худшая, hold среднее/медиана, серии, время в рынке (интервалы удержания, обрезанные окном /
   длина окна), оборот (Σ notional входов и выходов), комиссии, фандинг, комиссии как % валовой прибыли (Σ gross прибыльных по gross
   сделок), buy&hold BTC за то же окно по 1h-свечам (close последней закрытой свечи на момент t; null, если свечи недоступны/устарели).
6) Сравнение с предыдущим окном такой же длины — только для range >= 7d и никогда для all.
7) Ряды для графиков — компактные массивы (см. StatsSeries в types.ts); сделки в таблице — последние MAX_TRADE_ROWS по выходу.
   Деньги — главное: у строки сделки equity_at_entry (кошелёк на входе: последняя точка equity <= первого fill и не старше суток,
   иначе null) и ret_account_pct = net / equity_at_entry * 100 (null без кошелька). Проценты РАЗНЫХ сделок не складываются
   (кошелёк на входе у каждой свой), поэтому в KPI только суммы в USDT: profit_sum_usdt (Σ net > 0) и loss_sum_usdt (Σ net < 0).
8) warnings — машинные коды + русский текст.
10) ТОЧНЫЕ ЦИФРЫ БИРЖИ. Бот пишет в таблицу roundtrips итог каждой закрытой сделки по записи Bybit closed-pnl (closedPnl уже за вычетом
   комиссий и фандинга; approx=0) — это то же число, что в Telegram. Если для сделки реплея есть такая строка (совпадение по order_id
   закрывающего fill; запасной вариант — время выхода в пределах 120 с + сторона + объём), её net/комиссии/фандинг ЗАМЕНЯЮТ оценку:
   сделка получает exact=true, funding_complete=true, а все агрегаты (KPI, корзины, календарь, гистограмма, сверка, сравнение)
   считаются уже по точным числам. Нет строки (или она оценочная, approx=1) — оценка как раньше, exact=false. Построчные fills
   (комиссия каждого fill) остаются оценкой и могут не сходиться с точным итогом сделки.
9) Границы дней/недель/месяцев — UTC (как дневной лимит риска бота).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

from tradebot.i18n import tr
from tradebot.web.stats_base import (
    CACHE_SIZE, CACHE_TTL_S, COMPARE_MIN_S, DEFAULT_STOP_PCT, DEFINITIONS, EPS_QTY, EQ_LOOKBACK_S, FEW_TRADES, MAX_TRADE_ROWS,
    RANGE_SECONDS, STOP_NOTICE, SYMBOL, TAKER_FEE_EST, _day, _num, _ts)
from tradebot.web.stats_equity import equity_at, equity_metrics
from tradebot.web.stats_io import _db_uri, _market_sig, fingerprint, load_market, read_db
from tradebot.web.stats_market import Market, funding_between
from tradebot.web.stats_replay import apply_exact, clean_fills, replay
from tradebot.web.stats_series import _bucket_kind, downsample, histogram, pnl_buckets
from tradebot.web.stats_trades import _compare, _finish_compare, _group_row, _reconcile, _row, trade_metrics

# ---------------------------------------------------------------- сборка ответа
def unavailable(range_key: str, reason: str, message: str, now: float) -> dict:
    return {"available": False, "reason": reason, "message": message, "range": range_key, "computed_at": int(now),
            "definitions": DEFINITIONS, "warnings": []}


def _warn(code: str, text: str, level: str = "warn", **extra) -> dict:
    return {"code": code, "level": level, "text": text, **extra}


def _pos_dict(pos: float) -> dict | None:
    return None if abs(pos) < EPS_QTY else {"side": "long" if pos > 0 else "short", "qty": _num(abs(pos), 9)}


def compute_stats(range_key: str, now: float, *, fill_rows: list[dict], event_rows: list[dict], eq_t, eq_v,
                  eq_first_ts: float | None, kv: dict, market: Market | None, market_error: str | None = None,
                  roundtrip_rows: list[dict] | None = None) -> dict:
    """Ядро: всё считается из переданных строк БД (никакого ввода-вывода). eq_t/eq_v — точки equity (секунды, значения) по
    возрастанию; должны покрывать окно (и предыдущее для сравнения) плюс одну точку <= начала запроса."""
    if range_key not in RANGE_SECONDS:
        raise ValueError(tr("unknown range {range}: allowed {allowed}").format(range=range_key, allowed=', '.join(RANGE_SECONDS)))
    fills, bad = clean_fills(fill_rows)
    if not fills:
        return unavailable(range_key, "no_trades", "В журнале ещё нет сделок бота.", now)
    eq_t = np.asarray(eq_t, dtype="int64")
    eq_v = np.asarray(eq_v, dtype="float64")
    ok = np.isfinite(eq_v)
    eq_t, eq_v = eq_t[ok], eq_v[ok]

    lev = kv.get("s_leverage")
    lev = float(lev) if isinstance(lev, (int, float)) and lev > 0 else 1.0
    stop_pct = kv.get("s_stop_pct")
    stop_pct = float(stop_pct) if isinstance(stop_pct, (int, float)) and stop_pct > 0 else DEFAULT_STOP_PCT

    stop_ts = [t for t in (_ts(e["ts"]) for e in event_rows if STOP_NOTICE in str(e.get("text", "")).lower()) if t is not None]
    rp = replay(fills, stop_ts, stop_pct=stop_pct, leverage=lev, market=market)
    trades, acc = rp["trades"], rp["open"]
    apply_exact(trades, roundtrip_rows)                    # точные итоги биржи (roundtrips) вместо оценки — ДО любых агрегатов

    # --- окно
    first_fill_ts = min(f["ts"] for f in fills)         # fills идут в порядке id, но ts не обязан возрастать (правка/импорт)
    last_fill_ts = max(f["ts"] for f in fills)
    first_data = min([first_fill_ts] + ([eq_first_ts] if eq_first_ts is not None else []))
    secs = RANGE_SECONDS[range_key]
    start = first_data if secs is None else now - secs
    end = now
    wt = [x for x in trades if start <= x["exit_ts"] <= end]

    # --- открытая позиция и расхождение с kv
    actual = kv.get("position") if isinstance(kv.get("position"), dict) else None
    act_pos = 0.0
    if actual:
        try:
            act_pos = float(actual["qty"]) * (1 if actual["side"] == "long" else -1)
        except (KeyError, TypeError, ValueError):
            actual, act_pos = None, 0.0
    mismatch = None
    if abs(rp["pos"] - act_pos) > EPS_QTY:
        mismatch = {"replayed": _pos_dict(rp["pos"]), "actual": _pos_dict(act_pos)}
    open_pos, open_net = None, None
    if actual and act_pos:
        entry, upnl = actual.get("entry"), actual.get("upnl")
        fees_o = fund_o = None
        opened = None
        comp = True
        if acc is not None and mismatch is None:
            opened = int(acc.open_ts)
            fees_o = acc.fees
            fund_o, comp = funding_between(market, acc.timeline, acc.open_ts, now)
        notional = abs(act_pos) * float(actual.get("mark") or entry or 0.0)
        net_o = (float(upnl) - fees_o - fund_o) if (upnl is not None and fees_o is not None) else None
        open_net = net_o
        open_pos = {"side": actual["side"], "qty": _num(abs(act_pos), 9), "entry": _num(entry, 8), "mark": _num(actual.get("mark"), 8),
                    "upnl": _num(upnl, 4), "notional": _num(notional, 2), "opened_ts": opened, "fees_paid_est": _num(fees_o, 4),
                    "funding_est": _num(fund_o, 4), "funding_complete": comp, "net_est": _num(net_o, 4),
                    "heartbeat": kv.get("heartbeat")}

    # --- equity
    em = equity_metrics(eq_t, eq_v, start, end)
    if em is None:
        return unavailable(range_key, "no_equity", "В окне меньше двух точек equity — график и ROI посчитать нельзя.", now)

    intervals = [(x["entry_ts"], x["exit_ts"]) for x in trades]
    if acc is not None and mismatch is None:
        intervals.append((acc.open_ts, now))
    # если данных equity нет с начала окна, «эффективное» начало = первая точка equity: время в рынке, buy&hold и сверка
    # считаются за период, где бот реально наблюдался (иначе знаменатель включал бы дни до запуска)
    eff = float(em["eq_start_ts"]) if em["starts_before_data"] else start
    km = trade_metrics(wt, intervals, eff, end, market)

    # --- сверка с кошельком
    rec = _reconcile(em, [x for x in wt if x["exit_ts"] >= eff], trades, acc, open_net, rp, eff, market, mismatch)

    # --- сравнение с предыдущим окном
    comparison = _finish_compare(_compare(secs, start, eq_t, eq_v, eq_first_ts, first_data, trades, intervals, market), km, em)

    # --- ряды
    ts_, vs_, dd_ = em.pop("_series")
    rdays, r = em.pop("_daily")
    series = downsample(ts_, vs_, dd_)
    kind = _bucket_kind(range_key)
    cal: dict[str, dict] = {}
    for x in wt:
        c = cal.setdefault(_day(x["exit_ts"]), {"date": _day(x["exit_ts"]), "net": 0.0, "trades": 0})
        c["net"] += x["net"]
        c["trades"] += 1
    series["pnl_buckets"] = {"kind": kind, "rows": pnl_buckets(kind, wt, min([eff] + [x["exit_ts"] for x in wt]), end)}
    series["calendar"] = [{**c, "net": _num(c["net"], 4)} for c in cal.values()]
    series["daily_returns"] = [{"date": _day(d * 86400), "ret_pct": _num(x * 100, 4)} for d, x in zip(rdays, r)]
    series["histogram"] = histogram([x["ret_pct"] for x in wt])

    rows = [_row(x, equity_at(eq_t, eq_v, x["entry_ts"])) for x in wt[-MAX_TRADE_ROWS:]]

    # --- предупреждения
    warnings = _warnings(em, wt, mismatch, open_pos, bad, market, market_error, start, end)

    return {
        "available": True, "range": range_key, "computed_at": int(now),
        "window": {"start": int(start), "effective_start": int(eff), "end": int(end), "start_date": _day(start), "end_date": _day(end),
                   "length_s": int(end - start)},
        "config": {"symbol": SYMBOL, "taker_fee_est": TAKER_FEE_EST, "leverage": lev, "stop_pct": stop_pct},
        "data": {"first_trade_ts": int(first_fill_ts), "last_trade_ts": int(last_fill_ts), "first_equity_ts": int(eq_first_ts) if eq_first_ts is not None else None,
                 "funding_coverage_until": int(market.funding_until) if market and market.funding_until else None,
                 "candles_until": int(market.candles_until) if market and market.candles_until else None,
                 "fills": len(fills), "round_trips": len(trades)},
        "equity": {k: v for k, v in em.items()},
        "kpis": km,
        "open_position": open_pos,
        "position_mismatch": mismatch,
        "reconciliation": rec,
        "comparison": comparison,
        "series": series,
        "breakdown": {
            "by_reason": [_group_row(k, [x for x in wt if x["reason_group"] == k]) for k in dict.fromkeys(x["reason_group"] for x in wt)],
            "by_side": [_group_row(s, [x for x in wt if x["side"] == s]) for s in ("long", "short")],
        },
        "trades": {"total": len(wt), "truncated": len(wt) > MAX_TRADE_ROWS, "rows": rows},
        "warnings": warnings,
        "definitions": DEFINITIONS,
    }


def _warnings(em, wt, mismatch, open_pos, bad, market, market_error, start, end) -> list[dict]:
    w: list[dict] = []
    if em["starts_before_data"]:
        w.append(_warn("starts_before_data", "Данные equity начинаются позже начала окна: ROI считается от первой точки.",
                       eq_start_ts=em["eq_start_ts"]))
    if len(wt) < FEW_TRADES:
        w.append(_warn("few_trades", f"Мало сделок в окне ({len(wt)} < {FEW_TRADES}): статистика ненадёжна.", count=len(wt)))
    inc = sum(1 for x in wt if not x["funding_complete"]) + (1 if open_pos and not open_pos["funding_complete"] else 0)
    if inc:
        until = market.funding_until if market else None
        w.append(_warn("funding_stale", "Локальные данные фандинга устарели или отсутствуют: для " + str(inc) +
                       " сделок фандинг неизвестен и принят за 0 (обновите данные на вкладке «Данные»).",
                       trades=inc, funding_coverage_until=int(until) if until else None))
    if market is None:
        w.append(_warn("candles_missing", "Нет локальных свечей BTCUSDT 1h: buy&hold и реконструкция стопов недоступны."
                       + (f" ({market_error})" if market_error else ""), error=market_error))
    elif market.price_at(end) is None or market.price_at(start) is None:
        w.append(_warn("candles_missing", "Локальные свечи не покрывают границы окна: buy&hold недоступен.",
                       candles_until=int(market.candles_until) if market.candles_until else None))
    if mismatch is not None:
        w.append(_warn("position_mismatch", "Позиция по журналу сделок не совпадает с позицией бота (kv): журнал неполон. "
                       "Открытая позиция показана по данным бота.", **mismatch))
    rc = sum(1 for x in wt if x["reconstructed"])
    if rc:
        w.append(_warn("reconstructed_trades", f"{rc} сделок реконструированы по событию «Позиция исчезла» (стоп/ликвидация): "
                       "цена выхода — оценка.", count=rc))
    g = em["gaps"]
    if g["count"]:
        w.append(_warn("equity_gaps", f"В окне есть разрывы equity > 15 мин (бот не работал): {g['count']} шт., всего "
                       f"{g['total_s'] / 3600:.1f} ч.", count=g["count"], total_s=g["total_s"], level="info"))
    if bad:
        w.append(_warn("bad_fills", f"Отброшено некорректных записей сделок: {bad}.", count=bad))
    return w


_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
MAX_PARALLEL = 2                       # одновременных РАСЧЁТОВ статистики (разные диапазоны/ключи); остальные ждут слот
COMPUTE_WAIT_S = 120.0                 # сколько ждёт результата чужого расчёта того же ключа (потом считает сам)
_compute_sem = threading.Semaphore(MAX_PARALLEL)


class _Flight:
    """Идущий расчёт одного ключа кэша: остальные вызовы того же ключа ждут его результата, а не считают то же самое."""

    def __init__(self):
        self.event = threading.Event()
        self.result: dict | None = None


_inflight: dict[tuple, _Flight] = {}


def _now() -> float:
    return time.time()


def get_stats(range_key: str, db_path: Path | str, now: float | None = None) -> dict:
    """Точка входа API: читает bot.db read-only, считает, кэширует по (range, max id сделок/equity/events, mtime БД, свечи).
    Один и тот же ключ считается ОДИН раз (single-flight): параллельные вызовы ждут результата первого; разные ключи считаются
    не более MAX_PARALLEL одновременно. ValueError — неверный диапазон; всё остальное -> available:false."""
    if range_key not in RANGE_SECONDS:
        raise ValueError(tr("unknown range {range}: allowed {allowed}").format(range=range_key, allowed=', '.join(RANGE_SECONDS)))
    now = _now() if now is None else float(now)
    path = Path(db_path)
    if not path.exists():
        return unavailable(range_key, "no_db", "База бота не найдена: бот ещё не запускался.", now)
    try:
        db = sqlite3.connect(_db_uri(path), uri=True, timeout=5)
    except sqlite3.Error as e:
        return unavailable(range_key, "db_error", f"Не удалось открыть базу бота: {type(e).__name__}", now)
    flight, leader, out = None, False, None
    try:
        key = (range_key, int(now // CACHE_TTL_S), str(path), fingerprint(db, path), _market_sig())
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None:
                _cache.move_to_end(key)
                return hit
            flight = _inflight.get(key)
            if flight is None:
                flight = _inflight[key] = _Flight()
                leader = True
        if not leader:
            if flight.event.wait(COMPUTE_WAIT_S) and flight.result is not None:
                return flight.result
            flight = None                                 # лидер не справился/не успел — считаем самостоятельно
        with _compute_sem:
            secs = RANGE_SECONDS[range_key]
            q_start = None if secs is None else now - (2 * secs if secs >= COMPARE_MIN_S else secs) - EQ_LOOKBACK_S
            raw = read_db(db, q_start)
            market, err = load_market()
            out = compute_stats(range_key, now, fill_rows=raw["fills"], event_rows=raw["events"], eq_t=raw["eq_t"],
                                eq_v=raw["eq_v"], eq_first_ts=raw["eq_first_ts"], kv=raw["kv"], market=market, market_error=err,
                                roundtrip_rows=raw["roundtrips"])
    except sqlite3.Error as e:
        out = unavailable(range_key, "db_error", f"Ошибка чтения базы бота: {type(e).__name__}", now)
        return out
    finally:
        db.close()
        if leader:
            with _cache_lock:
                _inflight.pop(key, None)
            flight.result = out
            flight.event.set()                            # будим ждущих (при исключении result=None -> они посчитают сами)
    with _cache_lock:
        _cache[key] = out
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return out
