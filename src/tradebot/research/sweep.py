"""Массовый перебор: стратегия × таймфрейм × параметры.

Параметры выбираются на in-sample (первые IS_FRAC истории), затем лучший набор проверяется
на out-of-sample (оставшаяся часть, которую подбор не видел). Именно OOS-колонки показывают,
что можно ожидать в реальности.

Запуск:  python sweep.py [--symbol BTCUSDT] [--tfs 15m 1h 4h] [--lev 1] [--long-only]
"""
from __future__ import annotations

import argparse
import logging
import time

import pandas as pd

from tradebot.market import derivs
from tradebot.market import klines as data
from tradebot.core.engine import backtest
from tradebot.core.strategies import STRATEGIES
from tradebot.paths import RESULTS

log = logging.getLogger("tradebot.research")

IS_FRAC = 0.67
MIN_TRADES = 30  # меньше — статистически бессмысленно


def split(df: pd.DataFrame, frac: float = IS_FRAC):
    k = int(len(df) * frac)
    return df.iloc[:k], df.iloc[k:]


def score(m: dict) -> float:
    """Критерий выбора параметров на IS: Calmar, с отсевом редких сделок."""
    if not m or m["trades"] < MIN_TRADES:
        return -1e9
    return m["calmar"]


def runnable(st, df: pd.DataFrame) -> str | None:
    """None, если стратегию можно считать на этих данных; иначе причина пропуска. Деривативной стратегии нужны колонки
    funding/OI/LSR (derivs.enrich) — без них она падала с KeyError и обрывала весь перебор."""
    try:
        st.fn(df, **next(iter(st.combos())))
    except KeyError as e:
        return f"в данных нет колонки {e}"
    except Exception as e:  # noqa: BLE001  стратегия не отработала даже на первой комбинации
        return f"{type(e).__name__}: {e}"
    return None


def run(symbol, tfs, lev, long_only, fee, slip, days, refresh=True):
    rows, grid_rows = [], []
    for tf in tfs:
        df = derivs.enrich(data.load(symbol, tf, days=days, refresh=refresh), symbol, refresh=refresh, days=days)
        is_df, oos_df = split(df)
        for st in STRATEGIES.values():
            why = runnable(st, df)
            if why:
                log.warning("sweep %s %s: стратегия «%s» пропущена — %s", symbol, tf, st.name, why)
                print(f"{tf:>4} {st.name:<22} пропущена: {why}", flush=True)
                continue
            t0 = time.time()
            best, best_s = None, -1e18
            for p in st.combos():
                sig = st.fn(df, **p)  # сигнал считаем на всей истории (прогрев индикаторов), режем после
                m_is = backtest(is_df, sig, fee, slip, lev, long_only).metrics
                s = score(m_is)
                grid_rows.append({"symbol": symbol, "tf": tf, "strategy": st.name, **p,
                                  "is_return": m_is.get("total_return"), "is_dd": m_is.get("max_drawdown"),
                                  "is_trades": m_is.get("trades"), "score": s})
                if s > best_s:
                    best, best_s = p, s
            if best is None:
                continue
            sig = st.fn(df, **best)
            m_is = backtest(is_df, sig, fee, slip, lev, long_only).metrics
            m_oos = backtest(oos_df, sig, fee, slip, lev, long_only).metrics
            rows.append({
                "symbol": symbol, "tf": tf, "strategy": st.name, "params": str(best),
                "is_months": round((is_df.index[-1] - is_df.index[0]).days / 30.4, 1),
                "oos_months": round((oos_df.index[-1] - oos_df.index[0]).days / 30.4, 1),
                **{f"is_{k}": v for k, v in m_is.items()},
                **{f"oos_{k}": v for k, v in m_oos.items()},
            })
            print(f"{tf:>4} {st.name:<22} {time.time() - t0:5.1f}s  best={best}  "
                  f"IS {m_is.get('total_return', 0):+.0%}  OOS {m_oos.get('total_return', 0):+.0%} "
                  f"(B&H OOS {m_oos.get('buy_hold_return', 0):+.0%})", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(grid_rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--tfs", nargs="+", default=["15m", "1h", "4h"])
    ap.add_argument("--lev", type=float, default=1.0)
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--fee", type=float, default=0.00055)
    ap.add_argument("--slip", type=float, default=0.0002)
    ap.add_argument("--days", type=int, default=1095)
    a = ap.parse_args()
    res, grid = run(a.symbol, a.tfs, a.lev, a.long_only, a.fee, a.slip, a.days)
    res.to_csv(RESULTS / f"results_{a.symbol}.csv", index=False)
    grid.to_parquet(RESULTS / f"grid_{a.symbol}.parquet")
    print("saved", len(res), "rows")
