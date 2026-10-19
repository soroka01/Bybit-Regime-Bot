"""Метрики сделок окна (KPI), сравнение с предыдущим окном, сверка с кошельком и строка таблицы сделок."""
from __future__ import annotations

import numpy as np

from tradebot.web.stats_base import COMPARE_MIN_S, EPS_QTY, _num
from tradebot.web.stats_equity import equity_metrics
from tradebot.web.stats_market import Market, funding_between
from tradebot.web.stats_replay import state_at


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
