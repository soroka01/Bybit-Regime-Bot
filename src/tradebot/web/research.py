"""Подбор параметров (optimize) и рейтинг стратегий (leaderboard): расчёт + дисковый кэш результатов."""
from __future__ import annotations

import pandas as pd

from tradebot.core import optimize as opt
from tradebot.core import overlays as ov
from tradebot.core.strategies import STRATEGIES, Strategy, param_specs
from tradebot.market import klines
from tradebot.web import backtests, cache, data
from tradebot.web import serialize as S
from tradebot.web.common import Deadline
from tradebot.web.backtests import apply_market
from tradebot.web.data import DERIV_STRATEGIES
from tradebot.web.meta import get_strategy
from tradebot.web.schemas import CachedReq, LbItemReq, OptimizeReq, Overlays, SYMBOLS


def _optimize(df, st: Strategy, o: Overlays, req, criterion: str, top: int, dl: Deadline, curve: bool = False) -> dict:
    eff = apply_market(req)
    # сигналы — по полным данным (деривативные стратегии читают funding/OI/LSR перпа), движок — без фандинга в споте
    return opt.optimize(
        eff.bt_df(df), st.grid, lambda p: backtests._signal(df, st, p, o), days=backtests.eff_days(req.tf, req.days), fee=eff.fee,
        slippage=req.slippage, leverage=eff.leverage, long_only=eff.long_only, criterion=criterion, top=top, curve=curve,
        check=dl.check)


def run_optimize(req: OptimizeReq, dl: Deadline) -> dict:
    st = get_strategy(req.strategy)
    key = cache.make_key("opt", symbol=req.symbol, tf=req.tf, days=backtests.eff_days(req.tf, req.days), strategy=st.name,
                         long_only=req.long_only, overlays=[req.overlays.htf, req.overlays.stop], leverage=req.leverage,
                         fee=req.fee, slip=req.slippage, criterion=req.criterion, top=req.top, market=req.market,
                         fp=data.data_fingerprint(req.symbol, req.tf, st.name))
    hit = cache.get(key)
    if hit is not None:
        return {**hit, "from_cache": True}
    df = data.get_df(req.symbol, req.tf)       # кадр читается только при промахе кэша (ключ — по отпечатку файлов, без загрузки данных)
    out = _optimize(df, st, req.overlays, req, req.criterion, req.top, dl)
    out["param_specs"] = param_specs(st)
    out["days_effective"] = backtests.eff_days(req.tf, req.days)
    out.update(apply_market(req).echo())
    out = S.clean(out)
    cache.put(key, out, kind="opt", symbol=req.symbol, tf=req.tf, strategy=st.name, data_sig=data.data_sig(req.symbol, req.tf))
    return {**out, "from_cache": False}


def lb_plan(symbol: str, tf: str) -> dict:
    if symbol not in SYMBOLS or tf not in klines.TIMEFRAMES:
        raise ValueError("неизвестный символ или таймфрейм")
    has_k, has_d = data.klines_available(symbol, tf), data.available(symbol, tf)
    items = []
    for s in STRATEGIES.values():
        ready = has_k and (has_d or s.name not in DERIV_STRATEGIES)
        reason = "" if ready else ("нет свечей — скачайте данные" if not has_k else "нет данных фандинга/OI/LSR")
        items.append({"name": s.name, "desc": s.desc, "derivative": s.name in DERIV_STRATEGIES, "ready": ready,
                      "reason": reason})
    return {"symbol": symbol, "tf": tf, "klines": has_k, "derivs": has_d, "strategies": items}


def lb_key(symbol: str, tf: str, days: int, long_only: bool, leverage: float, fee: float, slippage: float,
           variants: bool, strategy: str, market: str = "perp") -> str:
    return cache.make_key("lb", symbol=symbol, tf=tf, days=backtests.eff_days(tf, days), long_only=long_only, leverage=leverage,
                          fee=fee, slip=slippage, variants=variants, strategy=strategy, market=market,
                          fp=data.data_fingerprint(symbol, tf, strategy))


def run_lb_item(req: LbItemReq, dl: Deadline) -> dict:
    st = get_strategy(req.strategy)
    key = lb_key(req.symbol, req.tf, req.days, req.long_only, req.leverage, req.fee, req.slippage, req.variants, st.name,
                 req.market)
    if not req.force:
        hit = cache.get(key)
        if hit is not None:
            return {**hit, "from_cache": True}
    out = S.clean(_compute_lb_item(req, st, dl))
    info = data._file_info(klines._path(req.symbol, req.tf))
    out["data_last"] = info["last"] if info else None
    out["computed_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    cache.put(key, out, kind="lb", symbol=req.symbol, tf=req.tf, strategy=st.name, data_sig=data.data_sig(req.symbol, req.tf))
    return {**out, "from_cache": False}


def lb_cached(req: CachedReq) -> dict:
    """Чистый поиск в дисковом кэше (без вычислений): готовые карточки + что ещё нужно досчитать."""
    plan = lb_plan(req.symbol, req.tf)
    keys = {s["name"]: lb_key(req.symbol, req.tf, req.days, req.long_only, req.leverage, req.fee, req.slippage,
                              req.variants, s["name"], req.market) for s in plan["strategies"] if s["ready"]}
    hits = cache.get_many(list(keys.values()))
    items = [{**hits[k], "from_cache": True} for k in keys.values() if k in hits]
    missing = [n for n, k in keys.items() if k not in hits]
    info = data._file_info(klines._path(req.symbol, req.tf))
    return {"items": items, "missing": missing, "plan": plan, "days_effective": backtests.eff_days(req.tf, req.days),
            "data_last": info["last"] if info else None,
            "fingerprint": data.data_fingerprint(req.symbol, req.tf, "")}


def _compute_lb_item(req: LbItemReq, st: Strategy, dl: Deadline) -> dict:
    df = data.get_df(req.symbol, req.tf) if st.name in DERIV_STRATEGIES else data.get_df_lite(req.symbol, req.tf)
    variants = ov.VARIANTS if req.variants else {"base": {}}
    best, best_name, best_o = None, None, None
    for name, kw in variants.items():
        o = Overlays(htf=kw.get("htf"), stop=kw.get("stop"))
        r = _optimize(df, st, o, req, "robust", 1, dl, curve=True)
        if r["results"] and (best is None or r["results"][0]["robust_score"] > best["results"][0]["robust_score"]):
            best, best_name, best_o = r, name, o
    base = {"strategy": st.name, "desc": st.desc, "derivative": st.name in DERIV_STRATEGIES,
            "funding_included": "funding" in df.columns and apply_market(req).market == "perp",
            **apply_market(req).echo()}
    if best is None:
        return {**base, "result": None, "reason": "недостаточно сделок на in-sample для надёжного подбора"}
    return {**base, "result": best["results"][0], "variant": best_name,
            "overlays": {"htf": best_o.htf, "stop": best_o.stop},
            "n_combos": best["n_combos"] * len(variants), "elapsed": best["elapsed"], "min_trades": best["min_trades"],
            "is_range": best["is_range"], "oos_range": best["oos_range"], "buy_hold_oos": best["buy_hold_oos"],
            "oos_days": best["oos_days"], "short_oos": best["short_oos"],
            "days_effective": backtests.eff_days(req.tf, req.days)}
