"""Бэктест, развёртка параметров и портфель + упаковка результата для графиков (даунсэмплинг, маркеры, сделки)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tradebot.core import overlays as ov
from tradebot.core.engine import backtest, monthly_returns
from tradebot.core.portfolio import BASE_TF, Leg, exposure
from tradebot.core.strategies import Strategy
from tradebot.market import klines
from tradebot.web import serialize as S
from tradebot.web._facade import F
from tradebot.web.common import Deadline
from tradebot.web.data import DataEmpty
from tradebot.web.meta import MAX_SWEEP_CELLS, PORTFOLIO_TFS
from tradebot.web.schemas import BacktestReq, PortfolioReq, SweepReq


@dataclass(frozen=True)
class MarketEff:
    """Эффективные настройки расчёта с учётом рынка (единственное место, где «спот» превращается в параметры движка)."""
    market: str
    leverage: float
    long_only: bool
    fee: float
    drop_funding: bool       # спот: фандинг не начисляется (колонка убирается перед вызовом движка; сигналы считаются по полным данным)

    def echo(self) -> dict:
        """Поля для ответа API. Для перпа (по умолчанию) пусто: ответы перпа остаются побайтно прежними."""
        if self.market == "perp":
            return {}
        return {"market": self.market, "effective": {"leverage": self.leverage, "long_only": self.long_only, "fee": self.fee}}

    def bt_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """Данные для движка: без колонки funding для спота."""
        return df.drop(columns="funding") if self.drop_funding and "funding" in df.columns else df


def apply_market(req) -> MarketEff:
    """market='spot': плечо 1x, только лонг, без фандинга, комиссия по умолчанию 0.10 % (см. schemas._Common._apply_market,
    который нормализует запрос на входе; здесь значения берутся из уже нормализованного запроса и страхуются повторно)."""
    spot = getattr(req, "market", "perp") == "spot"
    return MarketEff("spot" if spot else "perp", 1.0 if spot else float(req.leverage),
                     True if spot else bool(getattr(req, "long_only", False)), float(req.fee), spot)


def _signal(df: pd.DataFrame, st: Strategy, params: dict, o, dl: Deadline | None = None) -> pd.Series:
    try:
        sig = st.fn(df, **params)
    except KeyError as e:
        raise ValueError(f"в данных нет колонки {e} для стратегии {st.name}") from e
    sig = sig.reindex(df.index).fillna(0.0)
    if dl:
        dl.check()        # core.overlays.apply (цикл ATR-стопа) внутри не прерывается — проверяем до и после
    out = ov.apply(df, sig, htf=o.htf, stop=o.stop)
    if dl:
        dl.check()
    return out


def eff_days(tf: str, days: int) -> int:
    return klines.effective_days(tf, days)


def _slice(df: pd.DataFrame, days: int):
    if len(df) == 0:
        raise DataEmpty("данные")
    return df.index >= df.index[-1] - pd.Timedelta(days=days)


def _trade_geometry(sub: pd.DataFrame, t: pd.DataFrame):
    idx = sub.index
    ie = idx.get_indexer(pd.DatetimeIndex(t["entry"]))
    ix = idx.get_indexer(pd.DatetimeIndex(t["exit"]))
    return ie, ix, np.maximum(ie - 1, 0)   # вход происходит по close предыдущей свечи


def _marker_geometry(sub: pd.DataFrame, res, cap: int):
    """Сделки, попадающие на график (последние cap): (индексы баров входа, баров выхода, номера сделок для выходов, total, first)."""
    t = res.trades
    ie, ix, pe = _trade_geometry(sub, t)
    n = len(t)
    first = max(n - cap, 0)               # усекаем ПО СДЕЛКАМ: входы и выходы остаются парами (раньше выходы сдвигались на одну сделку)
    open_last = bool(res.position.iloc[-1] != 0)
    ks = np.array([k for k in range(first, n) if not (open_last and k == n - 1)], dtype=int)
    return pe, ix, ks, n, first


def _marker_bars(sub: pd.DataFrame, res, cap: int = 1200) -> list[int]:
    """Индексы баров маркеров в порядке приоритета «новые сначала» (для добавления в выборку точек)."""
    if res.trades.empty:
        return []
    pe, ix, ks, n, first = _marker_geometry(sub, res, cap)
    out: list[int] = []
    has_exit = set(ks.tolist())
    for k in range(n - 1, first - 1, -1):
        out.append(int(pe[k]))
        if k in has_exit:
            out.append(int(ix[k]))
    return out


def _snap(idx: np.ndarray, pick: np.ndarray) -> np.ndarray:
    """Ближайший к idx элемент отсортированного pick (при равенстве — левый)."""
    j = np.searchsorted(pick, idx)
    lo, hi = pick[np.maximum(j - 1, 0)], pick[np.minimum(j, len(pick) - 1)]
    return np.where(idx - lo <= hi - idx, lo, hi)


def _markers(sub: pd.DataFrame, res, cap: int = 1200, pick: np.ndarray | None = None) -> dict:
    """Маркеры входов/выходов. Если задан pick (индексы баров прореженного ряда), маркер, чьего бара в выборке нет,
    притягивается к ближайшей выбранной точке (время и цена берутся из неё), чтобы лежать точно на линии цены."""
    t = res.trades
    if t.empty:
        return {"entries": [], "exits": [], "truncated": False}
    close = sub["close"].to_numpy()
    pe, ix, ks, n, first = _marker_geometry(sub, res, cap)
    if pick is not None and len(pick):
        pe, ix = _snap(pe, pick), _snap(ix, pick)
    ets, xts = S.secs(sub.index[pe]), S.secs(sub.index[ix])
    sides = t["side"].tolist()
    rets = t["return"].tolist()
    entries = [{"t": int(ets[k]), "p": S.num(close[pe[k]], 4), "side": sides[k]} for k in range(first, n)]
    exits = [{"t": int(xts[k]), "p": S.num(close[ix[k]], 4), "side": sides[k], "ret": S.num(rets[k], 5)} for k in ks]
    return {"entries": entries, "exits": exits, "truncated": n > cap}


def _trade_rows(sub: pd.DataFrame, res, limit: int = 2000) -> tuple[list, int]:
    t = res.trades
    if t.empty:
        return [], 0
    close = sub["close"].to_numpy()
    ie, ix, pe = _trade_geometry(sub, t)
    en, exi, sd, rt = t["entry"].tolist(), t["exit"].tolist(), t["side"].tolist(), t["return"].tolist()
    rows = [{"entry": en[k].isoformat(), "exit": exi[k].isoformat(), "side": sd[k], "ret": S.num(rt[k], 5),
             "bars": int(ix[k] - ie[k] + 1), "entry_price": S.num(close[pe[k]], 4),
             "exit_price": S.num(close[ix[k]], 4)} for k in range(len(t))]
    return rows[-limit:], len(rows)


SERIES_CAP = 2400    # максимум точек в рядах графика (раньше 1500); короче — отдаём все бары без прореживания
SERIES_N = 1000      # размер выборки min/max-бакетов отдельно для капитала и цены


def _pick(eq: np.ndarray, price: np.ndarray, dd: np.ndarray, marker_bars=(), cap: int = SERIES_CAP, n: int = SERIES_N) -> np.ndarray:
    """Общие индексы для всех рядов: объединение выборок капитала и цены (пики цены не теряются), минимум просадки,
    первая/последняя точки; оставшийся до cap запас занимают бары маркеров (новые первыми)."""
    m = len(eq)
    if m <= cap:
        return np.arange(m)
    pick = np.unique(np.concatenate([S.pick_indices(eq, n), S.pick_indices(price, n), [int(np.argmin(dd)), 0, m - 1]]))
    room = cap - len(pick)
    if room > 0:
        have = set(pick.tolist())
        extra = [b for b in dict.fromkeys(marker_bars) if b not in have][:room]
        if extra:
            pick = np.union1d(pick, np.array(extra, dtype=int))
    return pick


def _package(sub: pd.DataFrame, res, extra: dict | None = None) -> dict:
    eq = res.equity
    dd = eq / eq.cummax() - 1
    price = sub["close"].to_numpy()
    pick = _pick(eq.to_numpy(), price, dd.to_numpy(), _marker_bars(sub, res))
    t = S.secs(sub.index)
    mr = monthly_returns(eq)
    ye = eq.resample("YE").last()
    yearly = ye / ye.shift().fillna(eq.iloc[0]) - 1
    m = {k: S.num(v) for k, v in res.metrics.items()}
    bh = res.bh_equity
    m["bh_max_drawdown"] = S.num((bh / bh.cummax() - 1).min())
    rows, total = _trade_rows(sub, res)
    out = {
        "metrics": m,
        "data_range": {"start": sub.index[0].isoformat(), "end": sub.index[-1].isoformat(), "bars": int(len(sub)),
                       "step_min": float(sub.index.to_series().diff().median().total_seconds() / 60)},
        "t": t[pick].tolist(),
        "equity": S.fl(eq.to_numpy()[pick]),
        "bh": S.fl(bh.to_numpy()[pick]),
        "drawdown": S.fl(dd.to_numpy()[pick]),
        "price": S.fl(price[pick], 4),
        "markers": _markers(sub, res, pick=pick),
        "monthly": [{"y": int(i.year), "m": int(i.month), "v": S.num(v, 5)} for i, v in mr.items()],
        "yearly": [{"y": int(i.year), "v": S.num(v, 5)} for i, v in yearly.items()],
        "trades": rows, "trades_total": total,
    }
    if extra:
        out.update(extra)
    return out


def run_backtest(req: BacktestReq, dl: Deadline | None = None) -> dict:
    st = F.get_strategy(req.strategy)
    df = F.get_df(req.symbol, req.tf)
    params = F.clean_params(st, req.params)
    sig = _signal(df, st, params, req.overlays, dl)   # на ПОЛНОЙ истории (прогрев индикаторов), затем срез периода
    mask = _slice(df, eff_days(req.tf, req.days))
    sub = df[mask]
    eff = apply_market(req)
    res = backtest(eff.bt_df(sub), sig[mask], eff.fee, req.slippage, eff.leverage, eff.long_only)
    if dl:
        dl.check()
    if not res.metrics:
        raise ValueError("слишком мало данных за выбранный период")
    out = _package(sub, res, eff.echo())
    out["params"] = params
    out["days_effective"] = eff_days(req.tf, req.days)
    return out


def run_sweep(req: SweepReq, dl: Deadline) -> dict:
    """2D-развёртка (x, y) либо 1D-профиль (y не задан): остальные параметры берутся из req.params."""
    st = F.get_strategy(req.strategy)
    specs = F.param_specs(st)
    if req.x.name not in specs or (req.y is not None and (req.y.name not in specs or req.y.name == req.x.name)):
        raise ValueError("нужно выбрать параметры стратегии (для 2D — два разных)")
    if req.y is not None and req.x.n * req.y.n > MAX_SWEEP_CELLS:
        raise ValueError(f"слишком много ячеек (максимум {MAX_SWEEP_CELLS})")
    df = F.get_df(req.symbol, req.tf)
    base = F.clean_params(st, req.params)
    mask = _slice(df, eff_days(req.tf, req.days))
    sub = df[mask]
    eff = apply_market(req)
    bt_sub = eff.bt_df(sub)

    def axis(a):
        sp = specs[a.name]
        lo, hi = max(a.min, sp["min"]), min(a.max, sp["max"])   # диапазон слайдера (сетка x1.5)
        if hi < lo:
            lo, hi = hi, lo
        vals = np.linspace(lo, hi, a.n)
        if sp["kind"] == "int":
            return [int(v) for v in np.unique(np.round(vals).astype(int))]
        return [float(v) for v in np.unique(np.round(vals, 6))]

    def run(p):
        sig = _signal(df, st, p, req.overlays)[mask]
        return backtest(bt_sub, sig, eff.fee, req.slippage, eff.leverage, eff.long_only)

    xs = axis(req.x)
    if req.y is None:
        points = []
        for xv in xs:
            dl.check()
            m = run({**base, req.x.name: xv}).metrics
            points.append({k: S.num(v) for k, v in m.items()} if m else {})
        return {"x": {"name": req.x.name, "values": xs}, "y": None, "points": points, "grids": {}, "base": base, **eff.echo()}

    ys = axis(req.y)
    keys = ["total_return", "sharpe", "calmar", "max_drawdown", "avg_month", "trades"]
    grids = {k: [[None] * len(xs) for _ in ys] for k in keys}
    for j, yv in enumerate(ys):
        for i, xv in enumerate(xs):
            dl.check()
            m = run({**base, req.x.name: xv, req.y.name: yv}).metrics
            for k in keys:
                grids[k][j][i] = S.num(m.get(k)) if m else None
    return {"x": {"name": req.x.name, "values": xs}, "y": {"name": req.y.name, "values": ys},
            "points": None, "grids": grids, "base": base, **eff.echo()}


def run_portfolio(req: PortfolioReq, dl: Deadline | None = None) -> dict:
    eff = apply_market(req)      # спот: long_only у КАЖДОЙ ноги (клип до суммирования), плечо 1x, без фандинга
    legs = []
    for l in req.legs:
        st = F.get_strategy(l.strategy)
        if l.tf not in PORTFOLIO_TFS:
            raise ValueError(f"таймфрейм ноги {l.tf!r} не поддерживается: допустимы {', '.join(PORTFOLIO_TFS)} "
                             f"(не короче базовой сетки {BASE_TF} и кратные ей)")
        legs.append(Leg(l.strategy, l.tf, F.clean_params(st, l.params), l.weight, l.long_only or eff.long_only))
    keys = [l.key for l in legs]
    if len(set(keys)) != len(keys):
        raise ValueError("ноги должны различаться по паре стратегия@таймфрейм")
    dfs = {tf: F.get_df(req.symbol, tf) for tf in {BASE_TF, *(l.tf for l in legs)}}
    if dl:
        dl.check()
    expo, sigs = exposure(legs, dfs)          # core: одним вызовом, внутри не прерывается
    if dl:
        dl.check()
    base = dfs[BASE_TF]
    mask = _slice(base, req.days)
    sub = base[mask]
    res = backtest(eff.bt_df(sub), expo[mask], eff.fee, req.slippage, eff.leverage, eff.long_only)
    if dl:
        dl.check()
    if not res.metrics:
        raise ValueError("слишком мало данных за выбранный период")
    t = S.secs(sub.index)
    ssub = sigs[mask]
    bt, ex = S.bucket_mean(expo[mask].to_numpy(), t, 500)
    strips = {"t": bt.tolist(), "exposure": S.fl(ex, 3),
              "legs": {c: S.fl(S.bucket_mean(ssub[c].to_numpy(), t, 500)[1], 3) for c in ssub.columns}}
    out = _package(sub, res, {"strips": strips, **eff.echo()})
    out["legs"] = [{"key": l.key, "strategy": l.strategy, "tf": l.tf, "params": l.params, "weight": l.weight,
                    "long_only": l.long_only} for l in legs]
    return out
