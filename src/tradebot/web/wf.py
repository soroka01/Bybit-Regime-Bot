"""Walk-forward: чтение готовых результатов исследования (csv/pkl) с кэшем по mtime."""
from __future__ import annotations

import pandas as pd

from tradebot.web import serialize as S
from tradebot.web._facade import F
from tradebot.web.meta import wf_levs
from tradebot.web.schemas import SYMBOLS



_wf_cache: dict = {}


def _wf_tag(lev: int) -> str:
    if lev == 1:
        return ""
    if lev not in wf_levs():           # список плеч определяют реально существующие файлы результатов
        raise ValueError(f"нет результатов walk-forward для плеча {lev}x (есть: {', '.join(map(str, wf_levs())) or 'нет'})")
    return f"_lev{lev}"


def _cached_file(path, loader):
    if not path.exists():
        raise FileNotFoundError(path.name)
    key = path.stat().st_mtime_ns
    hit = _wf_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    obj = loader(path)
    _wf_cache[path] = (key, obj)
    return obj


def _wf_symbol(symbol: str) -> str:
    if symbol not in SYMBOLS:
        raise ValueError("неизвестный символ")
    return symbol


def wf_table(lev: int, symbol: str = "BTCUSDT") -> list[dict]:
    p = F.RESULTS / f"wf_{_wf_symbol(symbol)}{_wf_tag(lev)}.csv"
    df = _cached_file(p, pd.read_csv)
    return S.clean(df.rename(columns=lambda c: c[3:] if c.startswith("wf_") else c))


def wf_windows(lev: int, symbol: str = "BTCUSDT") -> list[dict]:
    p = F.RESULTS / f"wf_windows_{_wf_symbol(symbol)}{_wf_tag(lev)}.csv"
    return S.clean(_cached_file(p, pd.read_csv))


def wf_curve(lev: int, tf: str, strategy: str, variant: str, symbol: str = "BTCUSDT") -> dict:
    p = F.RESULTS / f"wf_curves_{_wf_symbol(symbol)}{_wf_tag(lev)}.pkl"
    curves = _cached_file(p, pd.read_pickle)          # локальный файл, создан research.walkforward
    eq = curves.get((tf, strategy, variant))
    if eq is None:
        raise ValueError("такой кривой нет")
    pick = S.pick_indices(eq.to_numpy(), 1500)
    return {"t": S.secs(eq.index)[pick].tolist(), "equity": S.fl(eq.to_numpy()[pick])}
