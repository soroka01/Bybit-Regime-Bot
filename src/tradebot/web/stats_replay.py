"""Реплей сделок: fills из trades -> round trips (средняя цена входа), синтетические закрытия по стопу, точные итоги биржи."""
from __future__ import annotations

import bisect
import math

import numpy as np

from tradebot.web.stats_base import (BAR_S, DEFAULT_STOP_PCT, EPS_QTY, EXACT_MATCH_S, EXACT_QTY_TOL, PRICE_TOL_S, STOP_REASON,
                                     TAKER_FEE_EST, _num, _ts, parse_ts, reason_group)
from tradebot.web.stats_market import Market, funding_between


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
