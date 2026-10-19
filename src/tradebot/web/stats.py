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

import bisect
import json
import math
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

# Реальная ставка тейкера аккаунта (Bybit fee-rate API) = 0.1 %, проверено 2026-10-09 — это НЕ 0.055 %, которые используют
# бэктесты. Наблюдаемые fills: execFee == notional*0.001 в точности.
TAKER_FEE_EST = 0.001

RANGE_SECONDS: dict[str, int | None] = {
    "1h": 3600, "1d": 86400, "3d": 3 * 86400, "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400,
    "180d": 180 * 86400, "365d": 365 * 86400, "730d": 730 * 86400, "1095d": 1095 * 86400, "all": None,
}
DEFAULT_RANGE = "30d"
COMPARE_MIN_S = 7 * 86400          # сравнение с предыдущим окном только от 7d (и не для all)
SETTLE_S = 8 * 3600                # расчёты фандинга Bybit: 00/08/16 UTC
BAR_S = 3600                       # свечи 1h
PRICE_TOL_S = 2 * BAR_S            # цена «на момент t» не старше 2 часов, иначе считаем свечи недоступными
EPS_QTY = 1e-9
EQ_POINTS_MAX = 600
GAP_S = 15 * 60                    # разрыв между точками equity > 15 мин = бот не работал
MIN_DAYS_SHARPE = 14
MIN_DAYS_CALMAR = 30
FEW_TRADES = 10
MAX_TRADE_ROWS = 500
EQ_ENTRY_MAX_AGE_S = 86400         # кошелёк «на входе» берётся из точки equity не старше суток до первого fill сделки (иначе null)
EQ_LOOKBACK_S = 7 * 86400          # equity читается на неделю глубже окна: сделка могла войти до начала окна
ANN = math.sqrt(365)
DEFAULT_STOP_PCT = 0.08
STOP_NOTICE = "позиция исчезла"    # подстрока (в нижнем регистре) уведомления executor.detect_stopout
EXACT_MATCH_S = 120                # запасное сопоставление сделки со строкой roundtrips: время выхода ± 120 с
EXACT_QTY_TOL = 0.01               # ... и объём в пределах 1 %
STOP_REASON = "стоп (реконструкция)"
SYMBOL, TF = "BTCUSDT", "1h"
CACHE_SIZE = 32
CACHE_TTL_S = 60                   # ключ кэша включает минуту: «сейчас» в окне не стареет дольше минуты
_ENV_RE = re.compile(r"Среда ордеров:\s*(\w+)", re.I)

DEFINITIONS: dict[str, str] = {
    "window": "Окно [сейчас - диапазон, сейчас], границы дней/недель/месяцев — UTC. Сделка относится к окну по времени ВЫХОДА. "
              "Для «Всё» окно начинается с первой сделки/точки equity.",
    "round_trip": "Сделка = позиция «плоско -> плоско». Считается по таблице trades со средней ценой входа; fill, меняющий сторону, "
                  "делится на закрытие и открытие; частичные уменьшения реализуют P/L на уменьшенную часть.",
    "reconstructed": "Закрытие по стопу/ликвидации на бирже в trades не пишется. Событие «Позиция исчезла» считается закрытием по "
                     "реконструированной цене: стоп-уровень (вход ± текущий stop_pct), если свеча его коснулась, иначе close свечи. "
                     "Это оценка, точность ниже, чем у обычных сделок.",
    "fees_est": f"Комиссии сделки: у точных сделок (exact) — фактические по данным Bybit, иначе ОЦЕНКА: объём fill в USDT × {TAKER_FEE_EST:.3%} "
                f"(реальная ставка тейкера аккаунта; ордера рыночные).",
    "funding_est": "Фандинг ОЦЕНОЧНЫЙ по локальным данным: для каждого расчёта (00/08/16 UTC) внутри сделки = знак × объём × цена "
                   "(open часовой свечи расчёта) × ставка; лонг платит при положительной ставке. После funding_coverage_until "
                   "ставки неизвестны — считаются нулём.",
    "net": "Net = валовый P/L - комиссии - фандинг. Если у сделки есть точные данные биржи (exact) — это closedPnl Bybit (то же число, "
           "что в Telegram), иначе оценка бота.",
    "exact": "exact = итог сделки взят из записи Bybit closed-pnl (комиссии и фандинг фактические). Без этого признака сделка — оценка бота "
             "(комиссия × ставка тейкера, фандинг по локальным свечам), точнее после обновления данных фандинга.",
    "ret_pct": "Доходность сделки, % = Net / входной объём в USDT.",
    "ret_margin_pct": "Доходность на маржу, % (оценка) = Net / (входной объём / ТЕКУЩЕЕ плечо).",
    "equity_at_entry": "Кошелёк счёта в момент входа: последняя точка equity не позже первого fill сделки (и не старше суток); null, если такой точки нет.",
    "ret_account_pct": "Результат сделки в % счёта = Net / кошелёк на входе × 100 (ROI счёта по сделке). Проценты разных сделок НЕ складываются: "
                       "кошелёк на входе у каждой свой; суммарный результат окна — в долларах (Σ Net) и в ROI счёта.",
    "profit_sum_usdt": "Σ Net прибыльных сделок окна, USDT (после комиссий и фандинга; по данным Bybit, где они есть, иначе оценка).",
    "loss_sum_usdt": "Σ Net убыточных сделок окна, USDT (число ≤ 0; после комиссий и фандинга; по данным Bybit, где они есть, иначе оценка).",
    "wallet_return_pct": "ROI счёта, % = Δ кошелька за окно / кошелёк на начале окна = eq_end / eq_start - 1. Кошелёк = весь счёт (UTA), "
                         "включая нереализованный P/L; вводы/выводы средств невидимы и искажают цифру.",
    "max_drawdown_pct": "Максимальная просадка кошелька внутри окна, % от предыдущего пика (число <= 0).",
    "volatility_pct": "Годовая волатильность дневных доходностей кошелька, % (σ × √365).",
    "sharpe": "Sharpe по дневным доходностям кошелька (последняя точка каждого дня UTC; дни без данных пропускаются), годовой √365. "
              "Только при >= 14 дневных доходностей.",
    "sortino": "Sortino: как Sharpe, но знаменатель — только отрицательные дни. null без отрицательных дней или при < 14 днях.",
    "calmar": "Calmar = годовая доходность кошелька / |макс. просадка|. Только при >= 30 дневных доходностей и просадке > 0.",
    "win_rate_pct": "Доля сделок с Net > 0, %.",
    "payoff": "Payoff = средний выигрыш / |средний проигрыш|.",
    "profit_factor": "Σ прибыльных Net / |Σ убыточных Net|; null, если убытков нет.",
    "expectancy": "Матожидание сделки: средний Net (USDT) и средняя доходность (%).",
    "time_in_market_pct": "Σ интервалов удержания позиции (обрезанных окном) / длина окна, %.",
    "turnover": "Оборот = Σ объёмов входов и выходов сделок окна, USDT.",
    "fees_pct_of_gross_profit": "Комиссии окна / Σ валовой прибыли сделок с gross > 0, %.",
    "buy_hold_pct": "Buy&hold BTC за то же окно по 1h-свечам (close последней закрытой свечи на начале и конце окна), %.",
    "open_position": "Открытая позиция из kv 'position' (нереализованный P/L от биржи) + оценка комиссии входа и фандинга до сих пор. "
                     "В закрытые сделки не входит.",
    "reconciliation": "Непояснённое = Δкошелька - (Σ Net сделок окна + оценка открытой позиции - вклад позиции, открытой на старте окна). "
                      "В основном внебот-движение кошелька (вводы/выводы, другие монеты) и ошибка оценок; не является ошибкой бота.",
    "comparison": "Сравнение с предыдущим окном такой же длины: только для диапазонов от 7 дней и не для «Всё»; нужны данные за всё "
                  "предыдущее окно.",
}


# ---------------------------------------------------------------- мелочи
def _num(x, nd: int = 6):
    """float -> round(float) или None для None/NaN/inf."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def parse_ts(s) -> float | None:
    """ISO-строка БД (UTC) -> unix-секунды."""
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _ts(x) -> float | None:
    return parse_ts(x) if isinstance(x, str) else (float(x) if x is not None else None)


def iso(ts: float) -> str:
    """unix-секунды -> строка в формате БД (сравнимо строками)."""
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat(timespec="seconds")


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def reason_group(reason: str | None) -> str:
    r = (reason or "").split(",")[0].strip()
    return (r[:40] or "—")


def _pct(x):
    return None if x is None else _num(x * 100.0)


# ---------------------------------------------------------------- рынок (свечи + фандинг)
class Market:
    """Часовые свечи BTCUSDT (open/high/low/close, секунды старта) и фандинг на сетке 00/08/16 UTC."""

    def __init__(self, bar_t, o, h, l, c, funding, funding_until: float | None):
        self.t = np.asarray(bar_t, dtype="int64")
        self.o, self.h, self.l, self.c = (np.asarray(x, dtype="float64") for x in (o, h, l, c))
        self.close_t = self.t + BAR_S
        self.funding_until = funding_until
        f = np.nan_to_num(np.asarray(funding, dtype="float64"), nan=0.0)
        m = (self.t % SETTLE_S) == 0
        # расчёт t -> (ставка, open свечи, начинающейся в t)
        self.settle = {int(t): (float(r), float(px)) for t, r, px in zip(self.t[m], f[m], self.o[m])}
        self.candles_until = float(self.close_t[-1]) if len(self.t) else None

    def price_at(self, t: float) -> float | None:
        """Close последней ЗАКРЫТОЙ свечи на момент t; None, если такой свечи нет или она старше PRICE_TOL_S."""
        if not len(self.t):
            return None
        i = int(np.searchsorted(self.close_t, t, side="right")) - 1
        if i < 0 or t - self.close_t[i] > PRICE_TOL_S:
            return None
        return float(self.c[i])

    def bars_between(self, lo: float, hi: float) -> np.ndarray:
        """Индексы свечей, чей СТАРТ в [lo, hi]."""
        return np.flatnonzero((self.t >= lo) & (self.t <= hi))


def market_from_df(df, funding_until: float | None) -> Market:
    """DataFrame из web.data.get_df (индекс — старт свечи UTC; колонка funding — сумма расчётов в свече)."""
    from tradebot.web.serialize import secs
    return Market(secs(df.index), df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy(),
                  df["funding"].to_numpy() if "funding" in df else np.zeros(len(df)), funding_until)


def funding_between(market: Market | None, timeline: list[tuple[float, float]], t0: float, t1: float) -> tuple[float, bool]:
    """(оплаченный фандинг, полнота) за расчёты t в (t0, t1]. timeline = [(ts, позиция со знаком ПОСЛЕ fill)].
    paid = signed_qty*price_t*rate_t (лонг платит при rate>0). Расчёт без данных (нет свечи/ставки, t > funding_until) — неполнота."""
    if t1 <= t0:
        return 0.0, True
    paid, complete = 0.0, True
    times = [x[0] for x in timeline]
    for k in range(int(math.floor(t0 / SETTLE_S)) + 1, int(math.floor(t1 / SETTLE_S)) + 1):
        t = k * SETTLE_S
        rec = market.settle.get(t) if market is not None else None
        if rec is None or market.funding_until is None or t > market.funding_until:
            complete = False
            continue
        i = bisect.bisect_left(times, t) - 1          # позиция ДО fills в момент t (выход ровно в t ещё платит)
        q = timeline[i][1] if i >= 0 else 0.0
        paid += q * rec[1] * rec[0]
    return paid, complete


# ---------------------------------------------------------------- реплей сделок
class _Acc:
    """Накопитель одной (возможно ещё открытой) сделки."""

    def __init__(self, sign: int, ts: float):
        self.sign, self.open_ts = sign, ts
        self.fills: list[dict] = []
        self.timeline: list[tuple[float, float]] = []     # (ts, позиция со знаком после fill)
        self.entry_qty = self.entry_notional = self.exit_qty = self.exit_notional = 0.0
        self.gross = self.fees = 0.0
        self.max_qty = 0.0
        self.close_oid: str | None = None                  # id ордера последнего закрывающего fill (для сопоставления с roundtrips)


def _fill_rec(f: dict, qty: float, realised: float, fee: float, split: bool, synthetic: bool = False, basis=None) -> dict:
    r = {"id": f.get("id"), "ts": int(f["ts"]), "side": f["side"], "qty": _num(qty, 9), "price": _num(f["price"], 8),
         "reason": f.get("reason"), "fee": _num(fee, 8), "realised": _num(realised, 8), "split": split, "synthetic": synthetic}
    if basis:
        r["basis"] = basis
    return r


def _reconstruct_exit(sign: int, avg: float, ev_ts: float, open_ts: float, stop_pct: float, market: Market | None):
    """(цена выхода, основание) для синтетического закрытия по стопу."""
    level = avg * (1 - sign * stop_pct)
    if market is None or not len(market.t):
        return avg, "entry"
    lo = max(math.floor(ev_ts / BAR_S) * BAR_S - BAR_S, math.floor(open_ts / BAR_S) * BAR_S)
    idx = market.bars_between(lo, ev_ts)
    if len(idx):
        if (sign > 0 and market.l[idx].min() <= level) or (sign < 0 and market.h[idx].max() >= level):
            return level, "stop_level"
    i = int(np.searchsorted(market.t, ev_ts, side="right")) - 1       # последняя свеча со стартом <= события
    if i < 0 or ev_ts - market.close_t[i] > PRICE_TOL_S:
        return avg, "entry"
    if market.close_t[i] <= ev_ts:
        return float(market.c[i]), "candle_close"
    return float(market.o[i]), "candle_open"                            # свеча ещё идёт: close неизвестен -> open


def _finish(a: _Acc, exit_ts: float, reason: str, market: Market | None, lev: float, n: int, reconstructed: bool) -> dict:
    fund, complete = funding_between(market, a.timeline, a.open_ts, exit_ts)
    entry_vwap = a.entry_notional / a.entry_qty
    exit_vwap = a.exit_notional / a.exit_qty if a.exit_qty > 0 else entry_vwap
    net = a.gross - a.fees - fund
    return {
        "n": n, "entry_ts": int(a.open_ts), "exit_ts": int(exit_ts), "side": "long" if a.sign > 0 else "short", "sign": a.sign,
        "qty": _num(a.max_qty, 9), "entry_price": _num(entry_vwap, 8), "exit_price": _num(exit_vwap, 8),
        "entry_notional": a.entry_notional, "exit_notional": a.exit_notional,
        "gross": a.gross, "fees": a.fees, "funding": fund, "funding_complete": complete, "net": net,
        "ret_pct": net / a.entry_notional * 100.0, "ret_margin_pct": net / a.entry_notional * 100.0 * lev,
        "hold_s": int(exit_ts - a.open_ts), "reason": reason, "reason_group": reason_group(reason),
        "reconstructed": reconstructed, "fills": a.fills, "_timeline": a.timeline,
        "exact": False, "close_oid": a.close_oid, "_lev": lev,
    }


def _rt_exact(r: dict) -> bool:
    """Строка roundtrips с ТОЧНЫМ итогом биржи: approx == 0 и конечный pnl. approx NULL/1 или pnl NULL — не точная."""
    try:
        return r.get("approx") == 0 and r.get("pnl") is not None and math.isfinite(float(r["pnl"]))
    except (TypeError, ValueError):
        return False


def _set_exact(x: dict, r: dict) -> None:
    """Заменяет оценку сделки точными числами строки roundtrips. funding в roundtrips: + получен, − уплачен; здесь funding = уплачено."""
    net = float(r["pnl"])
    fees = float(r["fees"]) if r.get("fees") is not None else x["fees"]
    if r.get("funding") is not None:
        fund = -float(r["funding"])
        gross = net + fees + fund
    else:                                                  # фандинг неизвестен: валовый P/L оставляем оценкой, остаток — в фандинг
        gross = x["gross"]
        fund = gross - fees - net
    x.update(net=net, fees=fees, funding=fund, gross=gross, funding_complete=True, exact=True,
             ret_pct=net / x["entry_notional"] * 100.0 if x["entry_notional"] else 0.0)
    x["ret_margin_pct"] = x["ret_pct"] * x["_lev"]


def apply_exact(trades: list[dict], rows: list[dict] | None) -> int:
    """Накладывает точные итоги из roundtrips на закрытые сделки реплея; возвращает число точных. Приоритет: order_id закрывающего
    fill; иначе ближайшая по времени выхода (≤ EXACT_MATCH_S) строка той же стороны и объёма (±1 %). Строка используется один раз;
    оценочные строки (approx != 0) и сделки без совпадения остаются оценкой."""
    cand = [r for r in (rows or []) if _rt_exact(r)]
    if not cand or not trades:
        return 0
    by_oid: dict[str, int] = {}
    for i, r in enumerate(cand):
        if r.get("order_id"):
            by_oid.setdefault(str(r["order_id"]), i)
    used: set[int] = set()
    n = 0
    for x in trades:
        i = by_oid.get(x["close_oid"]) if x.get("close_oid") else None
        if i is not None and i in used:
            i = None
        if i is None:
            best = None
            for k, r in enumerate(cand):
                if k in used or r.get("side") != x["side"]:
                    continue
                tc = _ts(r.get("ts_close"))
                if tc is None or abs(tc - x["exit_ts"]) > EXACT_MATCH_S:
                    continue
                q = r.get("qty")
                if q is not None and x["qty"] and abs(float(q) - x["qty"]) > EXACT_QTY_TOL * max(float(q), x["qty"]):
                    continue
                d = abs(tc - x["exit_ts"])
                if best is None or d < best[0]:
                    best = (d, k)
            i = best[1] if best else None
        if i is None:
            continue
        used.add(i)
        _set_exact(x, cand[i])
        n += 1
    return n


def clean_fills(rows: list[dict]) -> tuple[list[dict], int]:
    """Строки trades -> fills для реплея; отбрасывает price<=0, qty<=0, неизвестную сторону и кривые ts. Возвращает (fills, число отброшенных)."""
    out, bad = [], 0
    for r in rows:
        ts = r.get("ts")
        ts = parse_ts(ts) if isinstance(ts, str) else ts
        try:
            q, p = float(r["qty"]), float(r["price"])
        except (TypeError, ValueError, KeyError):
            bad += 1
            continue
        if ts is None or r.get("side") not in ("buy", "sell") or not (q > 0 and p > 0 and math.isfinite(q) and math.isfinite(p)):
            bad += 1
            continue
        out.append({"id": r.get("id"), "ts": float(ts), "side": r["side"], "qty": q, "price": p, "reason": r.get("reason") or "",
                    "order_id": r.get("order_id")})
    return out, bad


def replay(fills: list[dict], stop_events: list[float], *, stop_pct: float = DEFAULT_STOP_PCT, leverage: float = 1.0,
           market: Market | None = None, fee: float = TAKER_FEE_EST) -> dict:
    """Реплей fills (+ синтетические закрытия на ts событий стоп-аута). Возвращает:
    trades — закрытые сделки (dict, см. _finish); open — накопитель незакрытой сделки или None; pos, avg — итоговая позиция со
    знаком и средняя цена; states — [(ts, pos, avg)] после каждого шага (для позиции на момент t)."""
    items = [(f["ts"], 0, f) for f in fills] + [(t, 1, None) for t in stop_events]
    items.sort(key=lambda x: (x[0], x[1]))                  # fills раньше события с тем же ts; sort устойчив -> порядок id сохранён
    trades: list[dict] = []
    states: list[tuple[float, float, float]] = []
    acc: _Acc | None = None
    pos = avg = 0.0

    def open_new(f, qty, sign):
        nonlocal acc, pos, avg
        acc = _Acc(sign, f["ts"])
        pos, avg = sign * qty, f["price"]
        add_open(f, qty, split=False)

    def add_open(f, qty, split):
        acc.entry_qty += qty
        acc.entry_notional += qty * f["price"]
        fe = qty * f["price"] * fee
        acc.fees += fe
        acc.fills.append(_fill_rec(f, qty, 0.0, fe, split))
        acc.timeline.append((f["ts"], pos))
        acc.max_qty = max(acc.max_qty, abs(pos))

    def close_part(f, qty, split, synthetic=False, basis=None):
        """Уменьшает позицию на qty по цене f (реализует P/L от средней цены); True, если стало плоско."""
        nonlocal pos, acc
        sign = 1 if pos > 0 else -1
        realised = sign * (f["price"] - avg) * qty
        fe = qty * f["price"] * fee
        acc.gross += realised
        acc.fees += fe
        acc.exit_qty += qty
        acc.exit_notional += qty * f["price"]
        acc.close_oid = None if synthetic else (str(f["order_id"]) if f.get("order_id") else None)
        pos = round(pos - sign * qty, 12)
        if abs(pos) < EPS_QTY:
            pos = 0.0
        acc.fills.append(_fill_rec(f, qty, realised, fe, split, synthetic, basis))
        acc.timeline.append((f["ts"], pos))
        return pos == 0.0

    for ts, kind, f in items:
        if kind == 1:                                       # синтетическое закрытие по стопу
            if acc is None or pos == 0.0:
                continue
            px, basis = _reconstruct_exit(acc.sign, avg, ts, acc.open_ts, stop_pct, market)
            fs = {"id": None, "ts": ts, "side": "sell" if pos > 0 else "buy", "price": px, "reason": STOP_REASON}
            close_part(fs, abs(pos), False, True, basis)
            trades.append(_finish(acc, ts, STOP_REASON, market, leverage, len(trades) + 1, True))
            acc, avg = None, 0.0
            states.append((ts, 0.0, 0.0))
            continue
        sgn = 1 if f["side"] == "buy" else -1
        q = f["qty"]
        if acc is None or pos == 0.0:
            open_new(f, q, sgn)
        elif (pos > 0) == (sgn > 0):                        # добавление: усредняем
            avg = (abs(pos) * avg + q * f["price"]) / (abs(pos) + q)
            pos = round(pos + sgn * q, 12)
            add_open(f, q, split=False)
        else:
            cq = min(q, abs(pos))
            rest = round(q - cq, 12)
            flat = close_part(f, cq, split=rest > EPS_QTY)
            if flat:
                trades.append(_finish(acc, f["ts"], f.get("reason") or "", market, leverage, len(trades) + 1, False))
                acc, avg = None, 0.0
                if rest > EPS_QTY:                           # переворот: остаток открывает новую сделку
                    open_new(f, rest, sgn)
                    acc.fills[-1]["split"] = True
        states.append((ts, pos, avg))
    return {"trades": trades, "open": acc, "pos": pos, "avg": avg if pos else 0.0, "states": states}


def state_at(states, t: float) -> tuple[float, float]:
    """(позиция со знаком, средняя цена) непосредственно ПЕРЕД моментом t."""
    keys = [s[0] for s in states]
    i = bisect.bisect_left(keys, t) - 1
    return (states[i][1], states[i][2]) if i >= 0 else (0.0, 0.0)


# ---------------------------------------------------------------- equity
def select_series(t: np.ndarray, v: np.ndarray, start: float, end: float):
    """(t, v, starts_before_data) для окна: от eq_start (последняя точка <= start, иначе первая) до последней точки <= end."""
    if not len(t):
        return t, v, False
    i0 = int(np.searchsorted(t, start, side="right")) - 1
    before = i0 < 0
    i0 = max(i0, 0)
    i1 = int(np.searchsorted(t, end, side="right"))
    return t[i0:i1], v[i0:i1], before


def _bucket_edges(t: np.ndarray, n: int):
    """Границы бакетов по ВРЕМЕНИ (равные интервалы): индексы начала каждой непустой группы."""
    t0, span = float(t[0]), float(t[-1] - t[0])
    if span <= 0:
        return np.array([0])
    idx = np.minimum(((t - t0) / span * n).astype("int64"), n - 1)
    return np.concatenate(([0], np.flatnonzero(np.diff(idx)) + 1))


def downsample(t: np.ndarray, v: np.ndarray, dd: np.ndarray, n: int = EQ_POINTS_MAX) -> dict:
    """Кривая equity и просадка не более n точек: на бакет — последняя (t, v) + min/max v и min просадки."""
    if len(t) <= n:
        return {"equity": {"t": [int(x) for x in t], "v": [_num(x, 4) for x in v], "lo": [_num(x, 4) for x in v],
                           "hi": [_num(x, 4) for x in v]},
                "drawdown": {"t": [int(x) for x in t], "dd_pct": [_num(x * 100, 4) for x in dd]}}
    starts = _bucket_edges(t, n)
    ends = np.append(starts[1:], len(t)) - 1
    lo, hi, dmin = np.minimum.reduceat(v, starts), np.maximum.reduceat(v, starts), np.minimum.reduceat(dd, starts)
    tt = t[ends]
    return {"equity": {"t": [int(x) for x in tt], "v": [_num(x, 4) for x in v[ends]], "lo": [_num(x, 4) for x in lo],
                       "hi": [_num(x, 4) for x in hi]},
            "drawdown": {"t": [int(x) for x in tt], "dd_pct": [_num(x * 100, 4) for x in dmin]}}


def equity_metrics(t: np.ndarray, v: np.ndarray, start: float, end: float) -> dict | None:
    """Метрики по кошельку для окна [start, end]; None, если в окне меньше 2 точек. Внутри — numpy, наружу — числа/None."""
    ts, vs, before = select_series(t, v, start, end)
    if len(ts) < 2:
        return None
    eq0, eq1 = float(vs[0]), float(vs[-1])
    rm = np.maximum.accumulate(vs)
    dd = vs / rm - 1.0
    j = int(dd.argmin())
    i = int(vs[: j + 1].argmax())
    mdd = float(dd[j])
    # дневные доходности: последняя точка каждого дня UTC, доходность только если есть точка предыдущего дня
    day = ts // 86400
    last = np.flatnonzero(np.diff(day, append=day[-1] + 1))
    dd_day, dd_close = day[last], vs[last]
    rets, rdays = [], []
    for k in range(1, len(dd_day)):
        if dd_day[k] - dd_day[k - 1] == 1 and dd_close[k - 1] > 0:
            rets.append(dd_close[k] / dd_close[k - 1] - 1.0)
            rdays.append(int(dd_day[k]))
    r = np.array(rets, dtype="float64")
    n = len(r)
    sd = float(r.std(ddof=1)) if n >= 2 else None
    sharpe = sortino = calmar = vol = None
    if sd is not None:
        vol = sd * ANN * 100.0
    if n >= MIN_DAYS_SHARPE and sd and sd > 0:
        sharpe = float(r.mean()) / sd * ANN
        neg = r[r < 0]
        if len(neg):
            dsd = math.sqrt(float((np.minimum(r, 0.0) ** 2).mean()))
            sortino = float(r.mean()) / dsd * ANN if dsd > 0 else None
    span = max(float(ts[-1] - max(start, ts[0])), 1.0)
    if n >= MIN_DAYS_CALMAR and mdd < 0 and eq0 > 0:
        try:
            ann_ret = (eq1 / eq0) ** (365 * 86400 / span) - 1.0
            calmar = ann_ret / abs(mdd)
        except (OverflowError, ValueError, ZeroDivisionError):
            calmar = None
    gaps = np.diff(ts)
    big = gaps[gaps > GAP_S]
    tail = end - ts[-1]
    tail_gap = float(tail) if tail > GAP_S else 0.0
    best = int(r.argmax()) if n else None
    worst = int(r.argmin()) if n else None
    delta = eq1 - eq0
    return {
        "eq_start": _num(eq0, 4), "eq_end": _num(eq1, 4), "eq_start_ts": int(ts[0]), "eq_end_ts": int(ts[-1]),
        "starts_before_data": bool(before), "points": int(len(ts)), "delta": _num(delta, 4),
        "wallet_return_pct": _num((eq1 / eq0 - 1.0) * 100.0, 4) if eq0 > 0 else None,
        "max_drawdown_pct": _num(mdd * 100.0, 4), "max_drawdown_peak_ts": int(ts[i]), "max_drawdown_trough_ts": int(ts[j]),
        "daily_points": n, "volatility_pct": _num(vol, 4), "sharpe": _num(sharpe, 4), "sortino": _num(sortino, 4),
        "calmar": _num(calmar, 4),
        "best_day": {"date": _day(rdays[best] * 86400), "ret_pct": _num(r[best] * 100, 4)} if n else None,
        "worst_day": {"date": _day(rdays[worst] * 86400), "ret_pct": _num(r[worst] * 100, 4)} if n else None,
        "gaps": {"count": int(len(big)) + (1 if tail_gap else 0), "total_s": int(big.sum() + tail_gap) if (len(big) or tail_gap) else 0},
        "_series": (ts, vs, dd), "_daily": (rdays, r),
    }


# ---------------------------------------------------------------- метрики сделок
def _bucket_kind(range_key: str) -> str:
    return {"1h": "5min", "1d": "hour"}.get(range_key) or (
        "day" if range_key in ("3d", "7d", "30d") else "week" if range_key in ("90d", "180d", "365d") else "month")


def bucket_start(kind: str, ts: float) -> int:
    ts = int(ts)
    if kind == "5min":
        return ts // 300 * 300
    if kind == "hour":
        return ts // 3600 * 3600
    if kind == "day":
        return ts // 86400 * 86400
    if kind == "week":                                       # неделя с понедельника UTC (1970-01-01 — четверг)
        d = ts // 86400
        return ((d + 3) // 7 * 7 - 3) * 86400
    d = datetime.fromtimestamp(ts, timezone.utc)
    return int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp())


def _next_bucket(kind: str, b: int) -> int:
    if kind == "month":
        d = datetime.fromtimestamp(b, timezone.utc)
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp())
    return b + {"5min": 300, "hour": 3600, "day": 86400, "week": 7 * 86400}[kind]


def pnl_buckets(kind: str, trades: list[dict], start: float, end: float) -> list[dict]:
    """Корзины P/L от начала окна до конца (пустые — с нулями); сделки по времени выхода."""
    rows: dict[int, dict] = {}
    b, last = bucket_start(kind, start), bucket_start(kind, end)
    while b <= last:
        rows[b] = {"t": b, "n": 0, "wins": 0, "net": 0.0}
        b = _next_bucket(kind, b)
    for tr in trades:
        r = rows.get(bucket_start(kind, tr["exit_ts"]))
        if r is not None:
            r["n"] += 1
            r["wins"] += tr["net"] > 0
            r["net"] += tr["net"]
    return [{**r, "net": _num(r["net"], 4)} for r in rows.values()]


def histogram(vals: list[float]) -> dict:
    if not vals:
        return {"edges": [], "counts": []}
    a = np.asarray(vals, dtype="float64")
    nb = int(min(20, max(5, math.ceil(math.sqrt(len(a))))))
    if float(a.max()) == float(a.min()):
        lo = float(a.min())
        return {"edges": [_num(lo - 0.5, 4), _num(lo + 0.5, 4)], "counts": [int(len(a))]}
    counts, edges = np.histogram(a, bins=nb)
    return {"edges": [_num(x, 4) for x in edges], "counts": [int(x) for x in counts]}


def _group_row(key: str, trs: list[dict]) -> dict:
    n = len(trs)
    wins = sum(1 for x in trs if x["net"] > 0)
    net = sum(x["net"] for x in trs)
    return {"key": key, "count": n, "win_rate_pct": _num(wins / n * 100, 2) if n else None, "net": _num(net, 4),
            "avg_net": _num(net / n, 4) if n else None}


def _streaks(trs: list[dict]) -> tuple[int, int]:
    bw = bl = cw = cl = 0
    for x in trs:
        cw = cw + 1 if x["net"] > 0 else 0
        cl = cl + 1 if x["net"] < 0 else 0
        bw, bl = max(bw, cw), max(bl, cl)
    return bw, bl


def trade_metrics(trs: list[dict], intervals: list[tuple[float, float]], start: float, end: float,
                  market: Market | None) -> dict:
    """KPI по сделкам окна (trs — уже отобранные по выходу, в порядке выхода)."""
    n = len(trs)
    nets = [x["net"] for x in trs]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    gp, gl = sum(wins), -sum(losses)
    gross_pos = sum(x["gross"] for x in trs if x["gross"] > 0)
    fees, fund = sum(x["fees"] for x in trs), sum(x["funding"] for x in trs)
    holds = np.array([x["hold_s"] for x in trs], dtype="float64")
    length = max(end - start, 1.0)
    inm = sum(max(0.0, min(b, end) - max(a, start)) for a, b in intervals)
    bh = None
    if market is not None:
        p0, p1 = market.price_at(start), market.price_at(end)
        if p0 and p1:
            bh = (p1 / p0 - 1.0) * 100.0
    avg_w = gp / len(wins) if wins else None
    avg_l = -gl / len(losses) if losses else None
    sw, sl = _streaks(trs)
    best = max(trs, key=lambda x: x["net"], default=None)
    worst = min(trs, key=lambda x: x["net"], default=None)

    def ref(x):
        return None if x is None else {"n": x["n"], "net": _num(x["net"], 4), "ret_pct": _num(x["ret_pct"], 4), "exit_ts": x["exit_ts"]}
    net = sum(nets)
    return {
        "trades": n, "wins": len(wins), "losses": len(losses), "breakeven": n - len(wins) - len(losses),
        "win_rate_pct": _num(len(wins) / n * 100, 2) if n else None,
        "avg_win": _num(avg_w, 4), "avg_loss": _num(avg_l, 4),
        "payoff_ratio": _num(avg_w / abs(avg_l), 4) if avg_w is not None and avg_l else None,
        "profit_factor": _num(gp / gl, 4) if gl > 0 else None,
        "expectancy_usdt": _num(net / n, 4) if n else None,
        "expectancy_pct": _num(sum(x["ret_pct"] for x in trs) / n, 4) if n else None,
        "best_trade": ref(best), "worst_trade": ref(worst),
        "hold_mean_s": _num(holds.mean(), 1) if n else None, "hold_median_s": _num(float(np.median(holds)), 1) if n else None,
        "max_win_streak": sw, "max_loss_streak": sl,
        "net_pnl": _num(net, 4), "profit_sum_usdt": _num(gp, 4), "loss_sum_usdt": _num(-gl, 4),
        "gross_pnl": _num(sum(x["gross"] for x in trs), 4), "gross_profit": _num(gross_pos, 4),
        "fees_total": _num(fees, 4), "funding_total": _num(fund, 4),
        "fees_pct_of_gross_profit": _num(fees / gross_pos * 100.0, 2) if gross_pos > 0 else None,
        "turnover": _num(sum(x["entry_notional"] + x["exit_notional"] for x in trs), 2),
        "time_in_market_pct": _num(inm / length * 100.0, 2), "buy_hold_pct": _num(bh, 2),
    }


COMPARE_KEYS = ("net_pnl", "trades", "win_rate_pct", "profit_factor", "expectancy_usdt", "fees_total")
COMPARE_EQ_KEYS = ("wallet_return_pct", "max_drawdown_pct", "sharpe")


def _cmp_row(cur: dict, prev: dict, keys) -> dict:
    out = {}
    for k in keys:
        a, b = cur.get(k), prev.get(k)
        out[k] = {"cur": a, "prev": b, "delta": _num(a - b, 4) if a is not None and b is not None else None}
    return out


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
        raise ValueError(f"неизвестный диапазон {range_key}: допустимо {', '.join(RANGE_SECONDS)}")
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
    first_data = min([fills[0]["ts"]] + ([eq_first_ts] if eq_first_ts is not None else []))
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
    env_set = sorted({m.group(1).upper() for e in event_rows if (_ts(e["ts"]) or 0) >= first_data and "Бот запущен" in str(e.get("text", ""))
                      for m in [_ENV_RE.search(str(e.get("text", "")))] if m})
    warnings = _warnings(range_key, em, wt, trades, mismatch, open_pos, bad, env_set, market, market_error, now, start, end)

    return {
        "available": True, "range": range_key, "computed_at": int(now),
        "window": {"start": int(start), "effective_start": int(eff), "end": int(end), "start_date": _day(start), "end_date": _day(end),
                   "length_s": int(end - start)},
        "config": {"symbol": SYMBOL, "env": kv.get("env"), "taker_fee_est": TAKER_FEE_EST, "leverage": lev, "stop_pct": stop_pct},
        "data": {"first_trade_ts": int(fills[0]["ts"]), "last_trade_ts": int(fills[-1]["ts"]), "first_equity_ts": int(eq_first_ts) if eq_first_ts is not None else None,
                 "funding_coverage_until": int(market.funding_until) if market and market.funding_until else None,
                 "candles_until": int(market.candles_until) if market and market.candles_until else None,
                 "fills": len(fills), "round_trips": len(trades), "environments": env_set},
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


def equity_at(eq_t: np.ndarray, eq_v: np.ndarray, t: float) -> float | None:
    """Кошелёк на момент t: последняя точка equity не позже t и не старше EQ_ENTRY_MAX_AGE_S; нет такой (или значение <= 0) -> None."""
    if not len(eq_t):
        return None
    i = int(np.searchsorted(eq_t, t, side="right")) - 1
    if i < 0 or t - float(eq_t[i]) > EQ_ENTRY_MAX_AGE_S:
        return None
    v = float(eq_v[i])
    return v if math.isfinite(v) and v > 0 else None


def _row(x: dict, eq_entry: float | None = None) -> dict:
    """Строка таблицы сделок (внутренние поля и сырые накопители не отдаются). eq_entry — кошелёк на входе (equity_at) или None."""
    return {"n": x["n"], "entry_ts": x["entry_ts"], "exit_ts": x["exit_ts"], "side": x["side"], "qty": x["qty"],
            "entry_price": x["entry_price"], "exit_price": x["exit_price"], "entry_notional": _num(x["entry_notional"], 2),
            "gross": _num(x["gross"], 4), "fees": _num(x["fees"], 4), "funding": _num(x["funding"], 4),
            "funding_complete": x["funding_complete"], "exact": bool(x.get("exact")), "net": _num(x["net"], 4),
            "ret_pct": _num(x["ret_pct"], 4),
            "ret_margin_pct": _num(x["ret_margin_pct"], 4),
            "equity_at_entry": _num(eq_entry, 4), "ret_account_pct": _num(x["net"] / eq_entry * 100.0, 4) if eq_entry else None,
            "hold_s": x["hold_s"], "reason": x["reason"],
            "reason_group": x["reason_group"], "reconstructed": x["reconstructed"],
            "fills": [{k: v for k, v in f.items() if k != "realised"} for f in x["fills"]]}


def _reconcile(em, wt, trades, acc, open_net, rp, start, market, mismatch) -> dict:
    """Δкошелька против оценки P/L бота. См. docstring п.4. Позиция, открытая на старте окна, оценивается по цене закрытия
    1h-свечи на старте; нет цены -> unexplained=null."""
    delta = em["delta"]
    base = {"wallet_delta": delta, "bot_pnl_est": None, "unexplained": None, "reason": None, "flat_at_start": None}
    if mismatch is not None:
        return {**base, "reason": "position_mismatch"}
    pos0, avg0 = state_at(rp["states"], start)
    base["flat_at_start"] = abs(pos0) < EPS_QTY
    bot = sum(x["net"] for x in wt) + (open_net or 0.0)
    if abs(pos0) >= EPS_QTY:
        mark = market.price_at(start) if market is not None else None
        if mark is None:
            return {**base, "reason": "no_start_price"}
        sign = 1 if pos0 > 0 else -1
        s_trade = next((x for x in trades if x["entry_ts"] < start <= x["exit_ts"]), None)
        accs = s_trade if s_trade is not None else (acc if acc is not None and acc.open_ts < start else None)
        if accs is None:
            return {**base, "reason": "no_start_trade"}
        fills = s_trade["fills"] if s_trade is not None else acc.fills
        tl = s_trade["_timeline"] if s_trade is not None else acc.timeline
        t0 = s_trade["entry_ts"] if s_trade is not None else acc.open_ts
        pre_real = sum(f["realised"] for f in fills if f["ts"] < start)
        pre_fee = sum(f["fee"] for f in fills if f["ts"] < start)
        pre_fund, _ = funding_between(market, tl, t0, start)
        bot -= pre_real + sign * abs(pos0) * (mark - avg0) - pre_fee - pre_fund
    base["bot_pnl_est"] = _num(bot, 4)
    base["unexplained"] = _num(delta - bot, 4) if delta is not None else None
    return base


def _compare(secs, start, eq_t, eq_v, eq_first_ts, first_data, trades, intervals, market) -> dict:
    if secs is None or secs < COMPARE_MIN_S:
        return {"available": False, "reason": "range_too_short" if secs else "all_range"}
    ps = start - secs
    if eq_first_ts is None or ps < eq_first_ts or ps < first_data:
        return {"available": False, "reason": "no_previous_data"}
    em = equity_metrics(eq_t, eq_v, ps, start)
    if em is None:
        return {"available": False, "reason": "no_previous_data"}
    tr = [x for x in trades if ps <= x["exit_ts"] < start]
    km = trade_metrics(tr, intervals, ps, start, market)
    return {"available": True, "prev_start": int(ps), "prev_end": int(start), "_km": km, "_em": em}


def _finish_compare(cmp: dict, km: dict, em: dict) -> dict:
    if not cmp.get("available"):
        return cmp
    pkm, pem = cmp.pop("_km"), cmp.pop("_em")
    return {**cmp, "kpis": _cmp_row(km, pkm, COMPARE_KEYS), "equity": _cmp_row(em, pem, COMPARE_EQ_KEYS)}


def _warnings(range_key, em, wt, trades, mismatch, open_pos, bad, env_set, market, market_error, now, start, end) -> list[dict]:
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
    if len(env_set) > 1:
        w.append(_warn("env_mixed", "В журнале есть запуски в разных средах (" + ", ".join(env_set) + "): данные могут быть смешаны.",
                       environments=env_set))
    g = em["gaps"]
    if g["count"]:
        w.append(_warn("equity_gaps", f"В окне есть разрывы equity > 15 мин (бот не работал): {g['count']} шт., всего "
                       f"{g['total_s'] / 3600:.1f} ч.", count=g["count"], total_s=g["total_s"], level="info"))
    if bad:
        w.append(_warn("bad_fills", f"Отброшено некорректных записей сделок: {bad}.", count=bad))
    return w


# ---------------------------------------------------------------- ввод-вывод
def _db_uri(path: Path) -> str:
    return f"file:{Path(path).as_posix()}?mode=ro"


def fingerprint(db: sqlite3.Connection, path: Path) -> tuple:
    """Дёшево: последние id таблиц + mtime файла БД и WAL (WAL меняется чаще основного файла)."""
    def one(sql):
        r = db.execute(sql).fetchone()
        return r[0] if r else None
    mt = []
    for suffix in ("", "-wal"):
        try:
            mt.append(Path(str(path) + suffix).stat().st_mtime_ns)
        except OSError:
            mt.append(None)
    try:
        rt = db.execute("SELECT COUNT(*), TOTAL(COALESCE(pnl,0)), TOTAL(COALESCE(approx,0)) FROM roundtrips").fetchone()
    except sqlite3.Error:                                  # старая БД без таблицы
        rt = None
    return (one("SELECT MAX(id) FROM trades"), one("SELECT MAX(rowid) FROM equity"), one("SELECT MAX(id) FROM events"), rt, *mt)


def read_db(db: sqlite3.Connection, q_start: float | None) -> dict:
    """Читает всё нужное для compute_stats. equity — WHERE ts >= q_start (ISO-строки сортируются как время) + одна точка до него."""
    fills = [dict(zip(("id", "ts", "side", "qty", "price", "reason", "order_id"), r))
             for r in db.execute("SELECT id,ts,side,qty,price,reason,order_id FROM trades ORDER BY id")]
    try:                                                   # итоги закрытых сделок по данным биржи (пишет бот); таблицы может не быть
        rtrips = [dict(zip(("ts_open", "ts_close", "side", "qty", "pnl", "fees", "funding", "approx", "order_id"), r)) for r in db.execute(
            "SELECT ts_open,ts_close,side,qty,pnl,fees,funding,approx,order_id FROM roundtrips ORDER BY id")]
    except sqlite3.Error:
        rtrips = []
    events = [dict(zip(("ts", "level", "text"), r)) for r in db.execute(
        "SELECT ts,level,text FROM events WHERE text LIKE '%озиция исчезла%' OR text LIKE '%Бот запущен%' ORDER BY id")]
    if q_start is None:
        rows = db.execute("SELECT ts,v FROM equity ORDER BY rowid").fetchall()
    else:
        q = iso(q_start)
        rows = db.execute("SELECT ts,v FROM equity WHERE ts >= ? ORDER BY rowid", (q,)).fetchall()
        b = db.execute("SELECT ts,v FROM equity WHERE ts < ? ORDER BY rowid DESC LIMIT 1", (q,)).fetchone()
        if b:
            rows.insert(0, b)
    first = db.execute("SELECT ts FROM equity ORDER BY rowid LIMIT 1").fetchone()
    t, v = _eq_arrays(rows)
    kv = {}
    for k in ("position", "env", "heartbeat", "s_leverage", "s_stop_pct"):
        r = db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        try:
            kv[k] = json.loads(r[0]) if r else None
        except ValueError:
            kv[k] = None
    return {"fills": fills, "events": events, "eq_t": t, "eq_v": v, "eq_first_ts": parse_ts(first[0]) if first else None, "kv": kv,
            "roundtrips": rtrips}


def _eq_arrays(rows) -> tuple[np.ndarray, np.ndarray]:
    if not rows:
        return np.zeros(0, "int64"), np.zeros(0)
    try:   # быстрый путь: формат БД 'YYYY-MM-DDTHH:MM:SS+00:00' (UTC)
        t = np.array([str(r[0])[:19] for r in rows], dtype="datetime64[s]").astype("int64")
    except ValueError:
        t = np.array([parse_ts(r[0]) or 0 for r in rows], dtype="int64")
    v = np.array([np.nan if r[1] is None else r[1] for r in rows], dtype="float64")
    order = np.argsort(t, kind="stable")
    return t[order], v[order]


def load_market() -> tuple[Market | None, str | None]:
    """Часовые свечи + фандинг из локального кэша (без сети). Любая проблема -> (None, текст)."""
    from tradebot.web._facade import F
    try:
        df = F.get_df(SYMBOL, TF)
        last = F._last_ts(F.MARKET / f"{SYMBOL}_funding.parquet")
        fu = datetime.fromisoformat(last).timestamp() if last else None
        return market_from_df(df, fu), None
    except Exception as e:  # noqa: BLE001  DataMissing/DataEmpty/любой сбой чтения — статистика не падает
        return None, type(e).__name__


def _market_sig() -> tuple:
    from tradebot.web._facade import F
    try:
        return tuple(p.stat().st_mtime_ns if p.exists() else None for p in F._files(SYMBOL, TF))
    except Exception:  # noqa: BLE001
        return ()


_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()


def _now() -> float:
    return time.time()


def get_stats(range_key: str, db_path: Path | str, now: float | None = None) -> dict:
    """Точка входа API: читает bot.db read-only, считает, кэширует по (range, max id сделок/equity/events, mtime БД, свечи).
    ValueError — неверный диапазон; всё остальное -> available:false."""
    if range_key not in RANGE_SECONDS:
        raise ValueError(f"неизвестный диапазон {range_key}: допустимо {', '.join(RANGE_SECONDS)}")
    now = _now() if now is None else float(now)
    path = Path(db_path)
    if not path.exists():
        return unavailable(range_key, "no_db", "База бота не найдена: бот ещё не запускался.", now)
    try:
        db = sqlite3.connect(_db_uri(path), uri=True, timeout=5)
    except sqlite3.Error as e:
        return unavailable(range_key, "db_error", f"Не удалось открыть базу бота: {type(e).__name__}", now)
    try:
        key = (range_key, int(now // CACHE_TTL_S), str(path), fingerprint(db, path), _market_sig())
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None:
                _cache.move_to_end(key)
                return hit
        secs = RANGE_SECONDS[range_key]
        q_start = None if secs is None else now - (2 * secs if secs >= COMPARE_MIN_S else secs) - EQ_LOOKBACK_S
        raw = read_db(db, q_start)
        market, err = load_market()
        out = compute_stats(range_key, now, fill_rows=raw["fills"], event_rows=raw["events"], eq_t=raw["eq_t"], eq_v=raw["eq_v"],
                            eq_first_ts=raw["eq_first_ts"], kv=raw["kv"], market=market, market_error=err,
                            roundtrip_rows=raw["roundtrips"])
    except sqlite3.Error as e:
        return unavailable(range_key, "db_error", f"Ошибка чтения базы бота: {type(e).__name__}", now)
    finally:
        db.close()
    with _cache_lock:
        _cache[key] = out
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return out
