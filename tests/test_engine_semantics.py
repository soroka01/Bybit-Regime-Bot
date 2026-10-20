"""Семантика бэктест-движка v2: комиссия на развороте, знак фандинга, ликвидация по эффективному плечу leverage*|pos|,
капитал не ниже нуля, сделки по смене знака с комиссией выхода, Sortino по всем свечам, CAGR на коротких выборках."""
import inspect

import numpy as np
import pandas as pd
import pytest

from tradebot.core import engine
from tradebot.core.engine import (DEFAULT_PERP_FEE, MIN_CAGR_DAYS, Result, _trades, backtest, liquidation_threshold,
                                  metrics)


def frame(close, high=None, low=None, funding=None, freq="1h"):
    idx = pd.date_range("2026-01-01", periods=len(close), freq=freq, tz="UTC")
    c = pd.Series(np.asarray(close, dtype="float64"), index=idx)
    df = pd.DataFrame({"open": c, "high": c if high is None else pd.Series(high, index=idx, dtype="float64"),
                       "low": c if low is None else pd.Series(low, index=idx, dtype="float64"), "close": c})
    if funding is not None:
        df["funding"] = pd.Series(funding, index=idx, dtype="float64")
    return df


def sig_of(df, values):
    return pd.Series(np.asarray(values, dtype="float64"), index=df.index)


# ---------------------------------------------------------------- умолчания
def test_default_fee_single_source():
    assert DEFAULT_PERP_FEE == 0.001
    assert inspect.signature(backtest).parameters["fee"].default == DEFAULT_PERP_FEE
    assert inspect.signature(backtest).parameters["slippage"].default == 0.0002
    assert not hasattr(engine, "BYBIT_TAKER_FEE")


# ---------------------------------------------------------------- комиссия
def test_fee_on_flip_is_twice_fee_plus_slippage_times_leverage():
    df = frame([100.0] * 8)
    fee, slip, lev = 0.001, 0.0002, 3.0
    r = backtest(df, sig_of(df, [1, 1, 1, -1, -1, -1, -1, -1]), fee, slip, lev)
    c = (fee + slip) * lev
    bar = r.bar_returns.to_numpy()
    assert bar[1] == pytest.approx(-c)               # вход: |Δpos| = 1
    assert bar[2] == bar[3] == 0.0
    assert bar[4] == pytest.approx(-2 * c)           # разворот +1 -> -1: |Δpos| = 2
    assert bar[5] == 0.0


def test_fee_on_partial_change_scales_with_turnover():
    df = frame([100.0] * 5)
    r = backtest(df, sig_of(df, [0.5, 1.0, 1.0, 1.0, 1.0]), 0.001, 0.0, 2.0)
    bar = r.bar_returns.to_numpy()
    assert bar[1] == pytest.approx(-0.5 * 0.001 * 2)          # вход 0.5
    assert bar[2] == pytest.approx(-0.5 * 0.001 * 2)          # довложили 0.5


# ---------------------------------------------------------------- фандинг
def test_funding_sign_long_pays_short_receives():
    n = 6
    df = frame([100.0] * n, funding=[0.0] + [0.001] * (n - 1))
    long_r = backtest(df, sig_of(df, [1] * n), 0.0, 0.0, 2.0)
    short_r = backtest(df, sig_of(df, [-1] * n), 0.0, 0.0, 2.0)
    assert long_r.bar_returns.iloc[2] == pytest.approx(-2 * 0.001)       # лонг платит положительный фандинг (с плечом)
    assert short_r.bar_returns.iloc[2] == pytest.approx(+2 * 0.001)      # шорт получает
    df_neg = frame([100.0] * n, funding=[0.0] + [-0.001] * (n - 1))
    assert backtest(df_neg, sig_of(df_neg, [1] * n), 0.0, 0.0, 1.0).bar_returns.iloc[2] == pytest.approx(+0.001)
    assert backtest(df_neg, sig_of(df_neg, [-1] * n), 0.0, 0.0, 1.0).bar_returns.iloc[2] == pytest.approx(-0.001)
    half = backtest(df, sig_of(df, [0.5] * n), 0.0, 0.0, 2.0)
    assert half.bar_returns.iloc[2] == pytest.approx(-0.001)             # фандинг пропорционален доле в позиции


# ---------------------------------------------------------------- ликвидация по эффективному плечу
def _dip(side, depth, n=10, bar=5):
    """Цена стоит на 100, на баре `bar` экстремум уходит против позиции на `depth` (доля) от прошлого close."""
    low, high = [100.0] * n, [100.0] * n
    if side > 0:
        low[bar] = 100.0 * (1 - depth)
    else:
        high[bar] = 100.0 * (1 + depth)
    return frame([100.0] * n, high=high, low=low)


def test_threshold_accepts_arrays_and_scalars():
    assert isinstance(liquidation_threshold(3), float)
    arr = liquidation_threshold(np.array([0.0, 1.0, 3.0, 100.0]))
    assert arr == pytest.approx([-0.995, -0.995, -0.985, -0.5])


@pytest.mark.parametrize("side", [1, -1])
def test_half_position_is_liquidated_by_effective_leverage_not_nominal(side):
    """Плечо 3x, позиция 0.5 -> эффективное 1.5x: порог -(1 - 0.005*1.5) = -0.9925 => ликвидация при движении ≈ 66 %.
    Раньше использовалось номинальное 3x (порог -0.985 => 32.8 %) и позиция 0.5 ликвидировалась слишком рано."""
    df = _dip(side, 0.40)
    half = backtest(df, sig_of(df, [0.5 * side] * len(df)), 0.0, 0.0, 3.0)
    full = backtest(df, sig_of(df, [1.0 * side] * len(df)), 0.0, 0.0, 3.0)
    assert half.metrics["liquidations"] == 0 and half.equity.min() > 0
    assert full.metrics["liquidations"] == 1 and full.equity.iloc[-1] == 0.0      # 3x*0.4 = 1.2 > 0.985: ликвидирована
    deep = _dip(side, 0.70)
    assert backtest(deep, sig_of(deep, [0.5 * side] * len(deep)), 0.0, 0.0, 3.0).metrics["liquidations"] == 1


def test_effective_leverage_one_never_liquidates_on_big_wick():
    df = _dip(1, 0.90)
    r = backtest(df, sig_of(df, [0.5] * len(df)), 0.0, 0.0, 2.0)       # 2x * 0.5 = 1x: порог -0.995, фитиль -90 % *1x -> жив
    assert r.metrics["liquidations"] == 0


def test_liquidation_unchanged_for_full_position():
    """|pos| = 1: эффективное плечо = номинальное, поведение прежнее (ликвидация при 1/lev - MMR)."""
    for lev in (3.0, 10.0, 50.0):
        edge = 1 / lev - 0.005
        below, above = _dip(1, edge * 0.98), _dip(1, edge * 1.02)
        assert backtest(below, sig_of(below, [1.0] * len(below)), 0.0, 0.0, lev).metrics["liquidations"] == 0
        assert backtest(above, sig_of(above, [1.0] * len(above)), 0.0, 0.0, lev).metrics["liquidations"] == 1


# ---------------------------------------------------------------- капитал не ниже нуля
def test_bar_return_clipped_at_minus_one_and_ruin_is_absorbing():
    """Шорт 1x, свеча +99 % и отрицательный фандинг (шорт платит): сырая доходность < -100 % -> -100 % (ликвидации нет:
    порог -99.5 % не достигнут). Следующая свеча в пользу шорта капитал не оживляет."""
    n = 10
    close = [100.0, 100.0, 100.0, 199.0] + [100.0] * 6
    funding = [0, 0, 0, -0.05] + [0] * 6
    df = frame(close, funding=funding, freq="1D")        # сутки: выборка >= 7 дн., CAGR определён (= -100 %)
    r = backtest(df, sig_of(df, [-1.0] * n), 0.0, 0.0, 1.0)
    assert r.metrics["liquidations"] == 0
    assert r.bar_returns.iloc[3] == -1.0
    assert (r.equity >= 0).all() and r.equity.iloc[3:].eq(0.0).all()
    assert r.bar_returns.iloc[4:].eq(0.0).all() and r.position.iloc[4:].eq(0.0).all()   # после разорения позиции нет
    assert r.metrics["total_return"] == pytest.approx(-1.0) and r.metrics["max_drawdown"] == pytest.approx(-1.0)
    assert r.metrics["cagr"] == -1.0


def test_short_squeeze_next_negative_bar_does_not_flip_equity_positive():
    """Раньше при bar < -100 % капитал становился отрицательным, а следующая убыточная свеча делала его положительным."""
    close = [100.0, 100.0, 100.0, 250.0, 500.0, 500.0]
    df = frame(close)
    r = backtest(df, sig_of(df, [-1.0] * len(df)), 0.0, 0.0, 1.0)
    assert (r.equity >= 0).all()
    assert r.equity.iloc[-1] == 0.0


def test_liquidations_counted_only_until_ruin():
    n = 10
    close = np.array([100.0, 100, 95, 90, 85, 80, 75, 70, 65, 60])                 # каждую свечу падение на ~5 %
    df = frame(close, high=np.r_[100.0, close[:-1]], low=close)
    r = backtest(df, sig_of(df, [1.0] * n), 0.0, 0.0, 100.0)
    assert r.metrics["liquidations"] == 1                  # 100x: первая же свеча -5 % ликвидирует; дальше капитал 0
    assert r.equity.iloc[2:].eq(0.0).all()
    assert r.position.iloc[3:].eq(0.0).all()


# ---------------------------------------------------------------- сделки
def test_exit_fee_is_attributed_to_the_closing_trade():
    df = frame([100.0] * 7)
    fee, slip, lev = 0.001, 0.0002, 2.0
    r = backtest(df, sig_of(df, [1, 1, 1, 0, 0, 0, 0]), fee, slip, lev)
    c = (fee + slip) * lev
    assert len(r.trades) == 1
    assert r.trades["return"].iloc[0] == pytest.approx((1 - c) ** 2 - 1)         # вход + выход (раньше был только вход)
    assert r.trades["return"].iloc[0] == pytest.approx(r.equity.iloc[-1] - 1)    # единственная сделка = весь результат
    assert r.trades["entry"].iloc[0] == df.index[1] and r.trades["exit"].iloc[0] == df.index[3]   # последняя активная свеча


def test_open_trade_at_the_end_has_entry_fee_only():
    close = [100.0, 100.0, 101.0, 102.0, 103.0]
    df = frame(close)
    r = backtest(df, sig_of(df, [1, 1, 1, 1, 1]), 0.001, 0.0, 1.0)
    assert len(r.trades) == 1 and r.trades["exit"].iloc[0] == df.index[-1]
    assert r.trades["return"].iloc[0] == pytest.approx(r.equity.iloc[-1] - 1)
    assert r.trades["return"].iloc[0] == pytest.approx((1 - 0.001) * 103.0 / 100.0 - 1)


def test_flip_closes_one_trade_and_opens_another_exit_fee_split():
    df = frame([100.0] * 8)
    fee, slip, lev = 0.001, 0.0002, 3.0
    r = backtest(df, sig_of(df, [1, 1, -1, -1, -1, 0, 0, 0]), fee, slip, lev)
    c = (fee + slip) * lev
    t = r.trades
    assert list(t["side"]) == ["long", "short"]
    assert list(t["entry"]) == [df.index[1], df.index[3]] and list(t["exit"]) == [df.index[2], df.index[5]]
    # каждая сделка платит вход и выход по c; у разворота комиссия 2c делится пополам между закрываемой и новой сделкой
    assert t["return"].iloc[0] == pytest.approx((1 - c) ** 2 - 1)
    assert t["return"].iloc[1] == pytest.approx((1 - c) ** 2 - 1)
    assert np.isclose(r.equity.iloc[-1], (1 - c) * (1 - 2 * c) * (1 - c), rtol=1e-12)    # движок: вход c, разворот 2c, выход c


def test_fractional_positions_are_segmented_by_sign_not_by_size():
    df = frame(100.0 + np.arange(12) * 0.1)
    s = [0.5, 0.6, 0.4, 0.5, 0.7, 0.0, 0.0, -0.3, -0.5, -0.5, 0.0, 0.0]
    r = backtest(df, sig_of(df, s), 0.001, 0.0002, 2.0)
    t = r.trades
    assert list(t["side"]) == ["long", "short"], "изменение размера позиции не должно рвать сделку"
    assert list(t["entry"]) == [df.index[1], df.index[8]]
    assert list(t["exit"]) == [df.index[5], df.index[10]]
    assert r.metrics["trades"] == 2


def test_always_fractional_positive_position_is_one_trade():
    rng = np.random.default_rng(3)
    df = frame(100 * np.exp(np.cumsum(rng.normal(0, 0.003, 300))))
    r = backtest(df, sig_of(df, rng.uniform(0.2, 0.9, 300)), 0.001, 0.0002, 1.0)
    assert r.metrics["trades"] == 1 and r.trades["side"].iloc[0] == "long"


def test_trades_empty_when_flat():
    df = frame([100.0] * 5)
    r = backtest(df, sig_of(df, [0] * 5))
    assert r.trades.empty and list(r.trades.columns) == ["entry", "exit", "side", "return"] and r.metrics["trades"] == 0


def test_trades_function_without_unit_cost_ignores_flip_split():
    idx = pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC")
    pos = pd.Series([1.0, 1.0, -1.0, -1.0], index=idx)
    bar = pd.Series([0.01, 0.0, -0.02, 0.0], index=idx)
    t = _trades(pos, bar, idx)
    assert list(t["side"]) == ["long", "short"]
    assert t["return"].iloc[0] == pytest.approx(0.01) and t["return"].iloc[1] == pytest.approx(-0.02)


# ---------------------------------------------------------------- метрики
def _res(bar):
    idx = pd.date_range("2026-01-01", periods=len(bar), freq="1h", tz="UTC")
    b = pd.Series(bar, index=idx, dtype="float64")
    eq = (1 + b).cumprod()
    return Result(eq, eq * 0 + 1.0, pd.Series(1.0, index=idx), b, pd.DataFrame({"entry": [], "exit": [], "side": [], "return": []}))


def test_sortino_uses_downside_deviation_over_all_bars():
    bar = np.array([0.01, 0.02, -0.01, 0.0, 0.03, -0.03, 0.01, 0.0, 0.02, 0.01])
    m = metrics(_res(bar), 60.0)
    dsd = np.sqrt(np.mean(np.minimum(bar, 0) ** 2))
    assert m["sortino"] == pytest.approx(bar.mean() / dsd * np.sqrt(525_600 / 60))
    one_loss = np.array([0.01, 0.02, -0.01, 0.0, 0.03, 0.02, 0.01, 0.0, 0.02, 0.01])      # одна убыточная свеча: std() давал NaN -> 0
    assert metrics(_res(one_loss), 60.0)["sortino"] > 0
    assert metrics(_res(np.abs(bar)), 60.0)["sortino"] == 0.0                              # без убытков знаменатель 0 -> 0


def test_cagr_and_calmar_none_below_minimum_span():
    short = _res(np.r_[0.01, np.full(47, 0.0005), -0.005])           # 2 дня
    m = metrics(short, 60.0)
    assert m["cagr"] is None and m["calmar"] is None and m["total_return"] > 0
    ok = metrics(_res(np.full(int(MIN_CAGR_DAYS * 24) + 6, 0.0001)), 60.0)
    assert ok["cagr"] is not None and ok["cagr"] > 0 and ok["calmar"] == 0.0     # просадки нет -> Calmar 0, как раньше
    assert MIN_CAGR_DAYS == 7.0
