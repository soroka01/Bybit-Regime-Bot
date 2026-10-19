"""Паритет: сигнал, посчитанный на усечённых данных (как в live), совпадает с сигналом полного бэктеста.
Три ноги: SMA cross@4h, OI flush@1h, Regime switch (CHOP)@6h. Усечение — РОВНО как в живом пути (closed_only + window_for)."""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from tradebot.core.portfolio import DEFAULT_LEGS, Leg, align, exposure, leg_signal
from tradebot.market import derivs, klines
from tradebot.trading.market_feed import TF_DELTA, closed_only, compute_target, expected_last_bar, window_for

TFS = ("1h", "4h", "6h")
H1 = pd.Timedelta(hours=1)
N_RANDOM = 150
N_BOUNDARY = 40          # окрестности закрытий 6h свечей: час до, закрытие, час после (x3)


def _load():
    return {tf: derivs.enrich(klines.load("BTCUSDT", tf, 1095, refresh=False), "BTCUSDT", refresh=False) for tf in TFS}


@pytest.fixture(scope="module")
def dfs():
    return _load()


def _live_cut(dfs, t_close, legs):
    """Данные, которые видит живой бот в момент t_close: только закрытые свечи, окно window_for на таймфрейм."""
    return {tf: closed_only(d, tf, t_close).iloc[-window_for(tf, legs):] for tf, d in dfs.items()}


def _points(dfs):
    """Случайные 1h-точки решения + окрестности закрытий 6h свечей (индексы базовой сетки)."""
    base = dfs["1h"]
    rng = np.random.default_rng(1)
    rnd = [int(i) for i in rng.integers(2000, len(base) - 5, N_RANDOM)]
    closes_6h = [i for i in range(2000, len(base) - 6) if ((base.index[i] + H1).hour % 6 == 0)]
    chosen = rng.choice(closes_6h, N_BOUNDARY, replace=False)
    edge = [int(i) + d for i in chosen for d in (-1, 0, 1)]
    return rnd, edge


def _equal_weight_legs():
    return [dataclasses.replace(l, weight=1 / 3) for l in DEFAULT_LEGS]


def test_default_legs_include_regime_6h():
    keys = [l.key for l in DEFAULT_LEGS]
    assert keys == ["SMA cross@4h", "OI flush@1h", "Regime switch (CHOP)@6h"]
    r = DEFAULT_LEGS[2]
    assert r.tf == "6h" and r.weight == 0.5 and r.long_only is False
    assert [l.weight for l in DEFAULT_LEGS] == [0.5, 0.0, 0.5]         # OI flush: edge выдохся -> вес 0 (нога переключаемая)
    assert sum(l.weight for l in DEFAULT_LEGS) == 1.0                  # = живой дефолт, |экспозиция| <= 1
    assert r.params == {"n": 28, "chop_lo": 45.0, "chop_hi": 55.0, "fast": 12, "slow": 100}


def test_live_matches_backtest(dfs):
    """exposure() на усечённых данных == exposure() на полной истории (3 ноги с ненулевыми весами 1/3: проверяем и
    ногу OI flush, чей дефолтный вес 0), >= 200 точек."""
    legs = _equal_weight_legs()
    full, _ = exposure(legs, dfs)
    base = dfs["1h"]
    rnd, edge = _points(dfs)
    pts = rnd + edge
    assert len(pts) >= 200
    worst = 0.0
    for i in pts:
        t_close = base.index[i] + H1  # момент принятия решения
        live, _ = exposure(legs, _live_cut(dfs, t_close, legs))
        worst = max(worst, abs(live.iloc[-1] - full.iloc[i]))
        assert abs(live.iloc[-1] - full.iloc[i]) < 1e-12, (base.index[i], live.iloc[-1], full.iloc[i])
    assert worst < 1e-12


def test_live_compute_target_matches_backtest_capped(dfs):
    """Тот же паритет через живую функцию compute_target (с ограничением суммы весов <= 1): веса 1/3 -> без масштабирования."""
    legs = _equal_weight_legs()
    full, _ = exposure(legs, dfs)
    base = dfs["1h"]
    rnd, edge = _points(dfs)
    seen_nonzero_6h = 0
    for i in rnd + edge:
        t_close = base.index[i] + H1
        tgt, sigs = compute_target(legs, _live_cut(dfs, t_close, legs), False, {})
        assert tgt is not None and abs(tgt - full.iloc[i]) < 1e-12, (base.index[i], tgt, full.iloc[i])
        assert abs(tgt) <= 1.0 + 1e-12
        seen_nonzero_6h += sigs["Regime switch (CHOP)@6h"] != 0
    assert seen_nonzero_6h > 20                                        # ветка 6h реально участвовала


def test_default_live_exposure_equals_web_default(dfs):
    """Живой дефолт (compute_target на DEFAULT_LEGS, сумма весов 1.0) == exposure() дефолтных ног веба: без масштабирования,
    |цель| <= 1; а без нулевых ног (как делает бот: effective_legs) результат тот же."""
    assert sum(l.weight for l in DEFAULT_LEGS) == 1.0
    full, _ = exposure(DEFAULT_LEGS, dfs)
    base = dfs["1h"]
    active = [l for l in DEFAULT_LEGS if l.weight > 0]
    assert [l.key for l in active] == ["SMA cross@4h", "Regime switch (CHOP)@6h"]
    n_nonzero = 0
    for i in range(5000, len(base) - 5, 797):
        tc = base.index[i] + H1
        tgt, sigs = compute_target(DEFAULT_LEGS, _live_cut(dfs, tc, DEFAULT_LEGS), False, {})
        tgt2, _ = compute_target(active, _live_cut(dfs, tc, active), False, {})
        assert abs(tgt - full.iloc[i]) < 1e-12 and abs(tgt2 - full.iloc[i]) < 1e-12 and abs(tgt) <= 1.0
        n_nonzero += tgt != 0
    assert n_nonzero > 0


def test_live_windows_per_tf():
    assert window_for("1h", DEFAULT_LEGS) == 300 and window_for("4h", DEFAULT_LEGS) == 300
    assert window_for("6h", DEFAULT_LEGS) == 480                       # >= прогрев EMA(100) + CHOP(28)


def test_window_480_is_enough_for_regime_everywhere(dfs):
    """Окно живого бота даёт ТОТ ЖЕ сигнал 6h-ноги, что и вся история, на каждой 6h свече (а не только в случайных точках)."""
    leg = DEFAULT_LEGS[2]
    d = dfs["6h"]
    full = leg_signal(leg, d)
    n = window_for("6h", DEFAULT_LEGS)
    bad = [d.index[i] for i in range(n, len(d)) if leg_signal(leg, d.iloc[i - n + 1:i + 1]).iloc[-1] != full.iloc[i]]
    assert not bad, bad[:5]


def test_6h_signal_never_uses_bar_closing_after_decision_point(dfs):
    """На точке решения t сигнал 6h-ноги не зависит от свечей, закрывающихся ПОЗЖЕ t (портим их — сигнал тот же),
    и равен сигналу последней 6h свечи, закрывшейся <= t."""
    leg = DEFAULT_LEGS[2]
    d6, base = dfs["6h"], dfs["1h"]
    step6 = TF_DELTA["6h"]
    sig_true = leg_signal(leg, d6)
    aligned_true = align(sig_true, step6, base.index, H1)
    rnd, edge = _points(dfs)
    rng = np.random.default_rng(7)
    checked = 0
    for i in (rnd[:60] + edge[:60]):
        t_close = base.index[i] + H1
        future = d6.index + step6 > t_close                             # свечи, закрывающиеся строго после точки решения
        assert future.any() and (~future).any()
        poisoned = d6.copy()
        k = len(poisoned[future])
        for c in ("open", "high", "low", "close"):
            poisoned.loc[future, c] = poisoned.loc[future, c].to_numpy() * rng.uniform(0.2, 5.0, k)
        poisoned.loc[future, "high"] = poisoned.loc[future, ["open", "high", "low", "close"]].max(axis=1)
        poisoned.loc[future, "low"] = poisoned.loc[future, ["open", "high", "low", "close"]].min(axis=1)
        al_p = align(leg_signal(leg, poisoned), step6, base.index, H1)
        assert al_p.iloc[i] == aligned_true.iloc[i], base.index[i]
        last_closed = expected_last_bar(t_close, "6h")                  # последняя закрытая 6h свеча
        assert last_closed + step6 <= t_close < last_closed + 2 * step6
        assert aligned_true.iloc[i] == sig_true.loc[last_closed], base.index[i]
        checked += 1
    assert checked >= 100


def test_6h_signal_changes_only_on_6h_close(dfs):
    """Раннер просыпается каждый час, но сигнал 6h-ноги меняется только в часы 00/06/12/18 UTC (закрытие свечи)."""
    leg = DEFAULT_LEGS[2]
    al = align(leg_signal(leg, dfs["6h"]), TF_DELTA["6h"], dfs["1h"].index, H1)
    changed = al.index[al.diff().fillna(0) != 0]
    assert len(changed) > 100
    # al[i] — сигнал на закрытии 1h свечи i (момент start+1h); изменение возможно только если start+1h на сетке 6h
    assert all((t + H1).hour % 6 == 0 for t in changed)
