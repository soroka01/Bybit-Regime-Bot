"""Чистая бухгалтерия P/L (без сети и БД): пересчёт ленты исполнений методом средней цены, локальные оценки закрытий,
нормализация записей Bybit closed-pnl и склейка «круговых» сделок (flat -> позиция -> flat) для экранов и итогов.

Источник истины о реализованном P/L — запись биржи (closed-pnl: closedPnl уже за вычетом комиссий и фандинга).
Всё, что считается здесь по таблице trades, — ОЦЕНКА (помечается «≈»): средняя цена входа и комиссия taker 0.1%."""
from __future__ import annotations

from datetime import datetime, timezone

EPS = 1e-12
FUNDING_DUST = 0.005     # |фандинг| меньше полцента — шум округления цен в записи биржи


def parse_ts(s: str | None) -> float | None:
    """ISO-строка -> unix-секунды (без зоны = UTC); None при мусоре."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


# ---------- запись биржи ----------
def _f(x, d: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def normalize_record(raw: dict) -> dict:
    """Сырая запись /v5/position/closed-pnl -> плоский словарь с числами. side — сторона ЗАКРЫВАЮЩЕГО ордера (buy/sell)."""
    return {
        "order_id": str(raw.get("orderId") or ""), "side": "buy" if str(raw.get("side")) == "Buy" else "sell",
        "closed_pnl": _f(raw.get("closedPnl")), "open_fee": abs(_f(raw.get("openFee"))), "close_fee": abs(_f(raw.get("closeFee"))),
        "avg_entry": _f(raw.get("avgEntryPrice")), "avg_exit": _f(raw.get("avgExitPrice")),
        "qty": _f(raw.get("qty")), "closed_size": _f(raw.get("closedSize")) or _f(raw.get("qty")),
        "leverage": _f(raw.get("leverage")), "created_ms": int(_f(raw.get("createdTime"))), "updated_ms": int(_f(raw.get("updatedTime"))),
        "exec_type": str(raw.get("execType") or ""), "order_type": str(raw.get("orderType") or ""),
        "cum_entry": _f(raw.get("cumEntryValue")), "cum_exit": _f(raw.get("cumExitValue")), "fills": int(_f(raw.get("fillCount"))),
    }


def record_parts(rec: dict) -> dict:
    """(net, fees, funding) по записи биржи. net = closedPnl; комиссии = openFee + closeFee; фандинг — остаток:
    net - (грязный результат по ценам - комиссии). Мелочь меньше FUNDING_DUST — округление цен, считаем нулём."""
    size = rec.get("closed_size") or rec.get("qty") or 0.0
    long = rec.get("side") == "sell"                       # закрывающий ордер sell => закрывали лонг
    gross = (rec["avg_exit"] - rec["avg_entry"]) * size * (1 if long else -1)
    fees = rec["open_fee"] + rec["close_fee"]
    funding = rec["closed_pnl"] - (gross - fees)
    if abs(funding) < FUNDING_DUST:
        funding = 0.0
    return {"net": rec["closed_pnl"], "fees": fees, "funding": funding, "gross": gross}


def exit_kind(rec: dict | None, stop: float | None, side: str) -> str:
    """Чем закрыта позиция: liquidation (execType BustTrade) | stop (цена выхода у биржевого стопа) | external (иначе)."""
    if rec is None:
        return "stop"
    if "bust" in rec.get("exec_type", "").lower():
        return "liquidation"
    px = rec.get("avg_exit") or 0.0
    if stop and px and abs(px - stop) / stop <= 0.01:
        return "stop"
    if stop and px:
        return "external"
    return "stop"


# ---------- реплей ленты исполнений ----------
def _sgn(side: str) -> int:
    return 1 if side == "buy" else -1


def replay(fills: list[dict], fee_rate: float | None = None, breaks: list[float] | None = None):
    """fills — хронологически, dict(ts, side, qty, price, reason, order_id). Средняя цена входа (average cost).
    breaks — unix-секунды, когда позицию закрыла биржа мимо ленты (стоп/ликвидация): открытая сделка закрывается без цены (pnl=None).
    Возвращает (steps, closed, open_trip):
      steps[i]  — разбор i-го исполнения: before/after (позиция со знаком), avg_before, closed_qty, gross (по ценам),
                  fee_open/fee_close/net (оценка P/L закрытой части, как closedPnl: по ценам минус обе комиссии);
      closed    — завершённые сделки (dict: side, ts_open, ts_close, qty, entry, exit, gross, fees, pnl, close_order_id, ...);
      open_trip — незавершённая сделка или None (поля entry/qty — по текущему набору)."""
    if fee_rate is None:
        from tradebot.trading.exchange import TAKER_FEE     # ленивый импорт: exchange импортирует этот модуль
        fee_rate = TAKER_FEE
    pos = avg = 0.0
    trip: dict | None = None
    steps: list[dict] = []
    closed: list[dict] = []
    pending = sorted(breaks or [])

    def new_trip(f, side):
        return {"side": side, "ts_open": f["ts"], "ts_close": None, "entry_value": 0.0, "open_qty": 0.0, "exit_value": 0.0,
                "closed_qty": 0.0, "gross": 0.0, "fees": 0.0, "reason_open": f.get("reason") or "", "reason_close": "",
                "close_order_id": None, "first_order_id": f.get("order_id"), "stop": False, "n_fills": 0, "red_oids": []}

    def finish(t, ts, order_id, reason, stop=False):
        t = dict(t)
        t.update(ts_close=ts, close_order_id=order_id, reason_close=reason, stop=stop)
        t["qty"] = t["open_qty"]
        t["entry"] = t["entry_value"] / t["open_qty"] if t["open_qty"] else 0.0
        t["exit"] = t["exit_value"] / t["closed_qty"] if t["closed_qty"] else None
        t["pnl"] = None if stop else t["gross"] - t["fees"]
        t["open"] = False
        return t

    for f in fills:
        ts = parse_ts(f["ts"]) if isinstance(f["ts"], str) else f["ts"]
        while pending and trip is not None and pending[0] <= (ts or 0):    # биржа закрыла позицию до этого исполнения
            b = pending.pop(0)
            closed.append(finish(trip, datetime.fromtimestamp(b, timezone.utc).isoformat(timespec="seconds"), None, "стоп", True))
            pos = avg = 0.0
            trip = None
        sgn, q, px = _sgn(f["side"]), float(f["qty"]), float(f["price"])
        st = {"fill": f, "before": pos, "avg_before": avg, "closed_qty": 0.0, "gross": 0.0, "fee_open": 0.0, "fee_close": 0.0,
              "net": 0.0}
        rest = q
        if pos != 0 and sgn != (1 if pos > 0 else -1):
            c = min(rest, abs(pos))
            gross = c * (px - avg) * (1 if pos > 0 else -1)
            st.update(closed_qty=c, gross=gross, fee_open=c * avg * fee_rate, fee_close=c * px * fee_rate)
            st["net"] = gross - st["fee_open"] - st["fee_close"]
            trip["exit_value"] += c * px
            trip["closed_qty"] += c
            trip["gross"] += gross
            trip["fees"] += c * px * fee_rate
            trip["red_oids"].append(f.get("order_id"))         # id ордеров, уменьшавших позицию: по записи биржи на каждый
            pos += sgn * c
            rest -= c
            if abs(pos) < EPS:
                pos = 0.0
                closed.append(finish(trip, f["ts"], f.get("order_id"), f.get("reason") or ""))
                trip = None
        if rest > EPS:
            if pos == 0:
                trip = new_trip(f, "long" if sgn > 0 else "short")
                avg = px
            else:
                avg = (abs(pos) * avg + rest * px) / (abs(pos) + rest)
            pos += sgn * rest
            trip["entry_value"] += rest * px
            trip["open_qty"] += rest
            trip["fees"] += rest * px * fee_rate
        if trip is not None:
            trip["n_fills"] += 1
        st["after"] = pos
        steps.append(st)
    while pending and trip is not None:                      # стопы после последнего исполнения
        b = pending.pop(0)
        closed.append(finish(trip, datetime.fromtimestamp(b, timezone.utc).isoformat(timespec="seconds"), None, "стоп", True))
        trip = None
    open_trip = None
    if trip is not None:
        open_trip = dict(trip)
        open_trip.update(qty=trip["open_qty"], entry=trip["entry_value"] / trip["open_qty"] if trip["open_qty"] else 0.0,
                         exit=None, pnl=None, open=True)
    return steps, closed, open_trip


def step_for(steps: list[dict], order_id: str) -> dict | None:
    """Разбор исполнения по id ордера (последнее совпадение)."""
    for st in reversed(steps):
        if st["fill"].get("order_id") == order_id:
            return st
    return None


DEDUP_S = 120.0     # сделка ленты и строка таблицы — одна и та же, если близки по времени выхода (и совпадает сторона)


def merge_trips(table_rows: list[dict], derived: list[dict]) -> list[dict]:
    """Склейка закрытых сделок: записи таблицы roundtrips (точные или оценка при закрытии) + реконструкция по ленте исполнений для
    сделок, которых в таблице нет. Одну и ту же сделку не дублируем: реконструированная пропускается, если её закрывающий
    ордер есть среди order_id таблицы либо рядом по времени выхода (±DEDUP_S) есть строка той же стороны (закрытие биржей идёт
    без ордера бота). Новые сверху."""
    oids = {str(r["order_id"]) for r in table_rows if r.get("order_id")}
    closes = [(parse_ts(r["ts_close"]) or 0.0, r.get("side")) for r in table_rows]
    out = [dict(r) for r in table_rows]
    for t in derived:
        tc = parse_ts(t["ts_close"]) or 0.0
        if (t.get("close_order_id") and str(t["close_order_id"]) in oids) or any(
                s == t["side"] and abs(c - tc) <= DEDUP_S for c, s in closes):
            continue
        out.append({"ts_open": t["ts_open"], "ts_close": t["ts_close"], "side": t["side"], "qty": t["qty"], "entry": t["entry"],
                    "exit": t["exit"], "pnl": t["pnl"], "fees": t["fees"], "funding": None, "equity_open": None, "eq_est": 1,
                    "approx": 1, "kind": "stop" if t["stop"] else "history", "reason": t["reason_close"], "order_id": t["close_order_id"]})
    out.sort(key=lambda r: parse_ts(r["ts_close"]) or 0.0, reverse=True)
    return out


# ---------- добор точных итогов с биржи ----------
def plan_backfill(records: list[dict], trades: list[dict], rt_rows: list[dict], breaks: list[float] | None = None) -> list[dict]:
    """План добора точных итогов: records — записи closed-pnl (normalize_record) за недавнее окно, trades — вся лента исполнений
    (по возрастанию), rt_rows — уже записанные roundtrips. Для каждой ЗАВЕРШЁННОЙ сделки ленты (не закрытой биржей) берём записи
    по id каждого её ордера, уменьшавшего позицию; нужны ВСЕ (частичные закрытия — отдельные записи): иначе сделка остаётся оценкой,
    половинчатых сумм не делаем. Сделка, у которой в roundtrips уже есть ТОЧНАЯ строка (approx=0, pnl задан), пропускается:
    точное не перезаписывается ни оценкой, ни повтором. Возвращает список {order_id, ts_open, ts_close, side, qty, entry, exit,
    pnl, fees, funding, reason, update} (update=True — заменить оценочную строку, иначе вставить новую)."""
    if not records or not trades:
        return []
    by_oid = {r["order_id"]: r for r in records if r.get("order_id")}
    existing = {str(r["order_id"]): r for r in rt_rows if r.get("order_id")}
    _, closed, _ = replay(trades, breaks=breaks)
    out: list[dict] = []
    for t in closed:
        oids = t.get("red_oids") or []
        if t.get("stop") or not oids or not t.get("close_order_id") or any(o is None or o not in by_oid for o in oids):
            continue
        cur = existing.get(str(t["close_order_id"]))
        if cur is not None and cur.get("approx") == 0 and cur.get("pnl") is not None:
            continue
        recs = [by_oid[o] for o in dict.fromkeys(oids)]
        parts = [record_parts(r) for r in recs]
        size = sum(r["closed_size"] for r in recs)
        out.append({"order_id": str(t["close_order_id"]), "ts_open": t["ts_open"], "ts_close": t["ts_close"], "side": t["side"],
                    "qty": t["qty"], "entry": t["entry"],
                    "exit": (sum(r["avg_exit"] * r["closed_size"] for r in recs) / size) if size else t["exit"],
                    "pnl": sum(p["net"] for p in parts), "fees": sum(p["fees"] for p in parts),
                    "funding": sum(p["funding"] for p in parts), "reason": t["reason_close"], "update": cur is not None})
    return out
