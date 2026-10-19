"""Метаданные для UI: константы (таймфреймы, деривативные стратегии), границы слайдеров, очистка параметров, /api/meta."""
from __future__ import annotations

import re

from tradebot.core.portfolio import BASE_TF, DEFAULT_LEGS
from tradebot.core.strategies import STRATEGIES, Strategy, _nice_step, clean_params, param_specs  # noqa: F401  (перенесены в core: их же использует бот)
from tradebot.market import klines
from tradebot.web._facade import F
from tradebot.web.schemas import SYMBOLS

TIMEFRAMES = list(klines.TIMEFRAMES)
DERIV_STRATEGIES = {"Funding contrarian", "Long/short ratio contrarian", "OI trend", "OI flush"}
DERIV_STRATEGIES |= {n for n, s in STRATEGIES.items() if s.derivative}   # новые стратегии помечают себя сами
WF_LEVS = {1: ""}      # историческое имя; полный список плеч определяет wf_levs() по файлам wf_<SYMBOL>[_lev<N>].csv
_WF_FILE = re.compile(r"^wf_BTCUSDT(?:_lev(\d{1,3}))?\.csv$")
MAX_SWEEP_CELLS = 400
PORTFOLIO_TFS = [t for t, (_, m) in klines.TIMEFRAMES.items() if m >= 60 and m % 60 == 0]


def wf_levs() -> list[int]:
    """Плечи, для которых есть готовые результаты walk-forward (по файлам в RESULTS), по возрастанию."""
    levs = set()
    try:
        names = [p.name for p in F.RESULTS.iterdir()]
    except OSError:
        names = []
    for n in names:
        m = _WF_FILE.match(n)
        if m:
            lev = int(m.group(1) or 1)
            if 1 <= lev <= 100:
                levs.add(lev)
    return sorted(levs)


def get_strategy(name: str) -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(f"неизвестная стратегия: {name}")
    return STRATEGIES[name]


def meta() -> dict:
    strategies = [{"name": s.name, "desc": s.desc, "derivative": s.name in DERIV_STRATEGIES, "params": param_specs(s)}
                  for s in STRATEGIES.values()]
    avail = {sym: [tf for tf in TIMEFRAMES if F.available(sym, tf)] for sym in SYMBOLS}
    legs = [{"strategy": l.strategy, "tf": l.tf, "params": l.params, "weight": l.weight, "long_only": l.long_only}
            for l in DEFAULT_LEGS]
    wf = wf_levs()
    return {"strategies": strategies, "timeframes": TIMEFRAMES, "symbols": list(SYMBOLS), "available": avail,
            "klines_available": {sym: [tf for tf in TIMEFRAMES if F.klines_available(sym, tf)] for sym in SYMBOLS},
            "leaderboard_tfs": list(TIMEFRAMES),
            "timeframe_minutes": {t: m for t, (_, m) in klines.TIMEFRAMES.items()},
            "history_days": {t: klines.history_days(t) for t in TIMEFRAMES},
            "fetch_estimate": {t: F.fetch_estimate(t) for t in TIMEFRAMES},
            "portfolio_tfs": list(PORTFOLIO_TFS),
            "base_tf": BASE_TF, "walkforward_levs": wf,
            "defaults": {"fee": 0.00055, "slippage": 0.0002, "days": 365, "leverage": 1.0},
            "legs": legs, "overlay_defaults": {"htf": 50, "stop": 3.0}}
