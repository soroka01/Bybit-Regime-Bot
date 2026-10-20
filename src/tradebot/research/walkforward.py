"""Скользящий walk-forward: окно подбора TRAIN_M месяцев -> тест TEST_M месяцев, шаг TEST_M.
Тестовые куски склеиваются в единую «честную» кривую капитала. Это максимально близко к тому,
как стратегия вела бы себя, если бы вы переподбирали параметры раз в квартал.

Запуск:  python walkforward.py [--tfs 1h 4h] [--lev 1] [--long-only]
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from tradebot.market import klines as data
from tradebot.market import derivs
from tradebot.core import overlays
from tradebot.core.engine import DEFAULT_PERP_FEE, Result, _trades, backtest, metrics, minutes_of
from tradebot.core.strategies import STRATEGIES
from tradebot.paths import RESULTS

TRAIN_M, TEST_M, MIN_TRADES = 12, 3, 12


def windows(index: pd.DatetimeIndex):
    t0, end = index[0], index[-1]
    s = t0
    while True:
        tr_end = s + pd.DateOffset(months=TRAIN_M)
        te_end = tr_end + pd.DateOffset(months=TEST_M)
        if te_end > end + pd.Timedelta(days=5):
            break
        yield s, tr_end, min(te_end, end)
        s = s + pd.DateOffset(months=TEST_M)


def quick_score(bar: pd.Series, pos: pd.Series, bars_per_year: float) -> float:
    if int((pos.diff().fillna(0) != 0).sum()) / 2 < MIN_TRADES:
        return -1e9
    eq = (1 + bar).cumprod()
    mdd = (eq / eq.cummax() - 1).min()
    years = len(bar) / bars_per_year
    cagr = eq.iloc[-1] ** (1 / years) - 1 if eq.iloc[-1] > 0 else -1
    return cagr / abs(mdd) if mdd < 0 else 0.0


def run(symbol, tfs, lev, long_only, fee, slip):
    summary, win_rows, curves = [], [], {}
    for tf in tfs:
        df = derivs.enrich(data.load(symbol, tf, days=1095), symbol)
        wins = list(windows(df.index))
        bpy = 525_600 / minutes_of(df.index)
        for st in STRATEGIES.values():
            for vname, vkw in overlays.VARIANTS.items():
                t0 = time.time()
                runs = []  # (params, Result)
                for p in st.combos():
                    sig = overlays.apply(df, st.fn(df, **p), **vkw)
                    runs.append((p, backtest(df, sig, fee, slip, lev, long_only)))
                chosen = []
                for (a, b, c) in wins:
                    tr = slice(a, b - pd.Timedelta(seconds=1))
                    best = max(runs, key=lambda r: quick_score(r[1].bar_returns[tr], r[1].position[tr], bpy))
                    chosen.append((best, (b, c)))
                bars, poss = [], []
                for (p, r), (b, c) in chosen:
                    te = slice(b, c)
                    bars.append(r.bar_returns[te]); poss.append(r.position[te])
                    win_rows.append({"tf": tf, "strategy": st.name, "variant": vname, "test_start": b, "params": str(p),
                                     "test_return": float((1 + r.bar_returns[te]).prod() - 1)})
                bar = pd.concat(bars); pos = pd.concat(poss)
                bar = bar[~bar.index.duplicated()]; pos = pos[~pos.index.duplicated()]
                eq = (1 + bar).cumprod()
                res = Result(eq, df["close"].reindex(bar.index) / df["close"].reindex(bar.index).iloc[0], pos, bar,
                             _trades(pos, bar, bar.index, (fee + slip) * lev))
                res.metrics = metrics(res, minutes_of(df.index))
                wr = [w["test_return"] for w in win_rows[-len(wins):]]
                summary.append({"tf": tf, "strategy": st.name, "variant": vname,
                                "windows": len(wins), "windows_positive": float(np.mean(np.array(wr) > 0)),
                                **{f"wf_{k}": v for k, v in res.metrics.items()}})
                curves[(tf, st.name, vname)] = eq
                m = res.metrics
                print(f"{tf:>3} {st.name:<20} {vname:<16} {time.time() - t0:5.1f}s  WF {m['total_return']:+.0%} "
                      f"(B&H {m['buy_hold_return']:+.0%}) DD {m['max_drawdown']:.0%} trades {m['trades']}", flush=True)
    return pd.DataFrame(summary), pd.DataFrame(win_rows), curves


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--tfs", nargs="+", default=["4h", "1h"])
    ap.add_argument("--lev", type=float, default=1.0)
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--fee", type=float, default=DEFAULT_PERP_FEE)
    ap.add_argument("--slip", type=float, default=0.0002)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    s, w, c = run(a.symbol, a.tfs, a.lev, a.long_only, a.fee, a.slip)
    s.to_csv(RESULTS / f"wf_{a.symbol}{a.tag}.csv", index=False)
    w.to_csv(RESULTS / f"wf_windows_{a.symbol}{a.tag}.csv", index=False)
    pd.to_pickle(c, RESULTS / f"wf_curves_{a.symbol}{a.tag}.pkl")
    print("saved", len(s))
