"""Тесты расширенного набора стратегий: причинность (без look-ahead), область значений, реальные данные,
скорость, сетки параметров и отображение в веб-API. Сеть не используется."""
import time

import numpy as np
import pandas as pd
import pytest

from tradebot.core.engine import backtest
from tradebot.core.indicators import bars_per_year, timed_hold
from tradebot.core.strategies import STRATEGIES
from tradebot.paths import MARKET

OLD_COUNT = 17
NEW = list(STRATEGIES)[OLD_COUNT:]
DERIV_NEW = {"Funding z-score", "Trend + funding filter"}
CAP_OK = {"TSMOM vol-target", "Donchian ensemble (trail)"}   # документированный кап 1.5 (таргет волатильности)
EXPECTED_NEW = ["TSMOM vol-target", "Donchian ensemble (trail)", "MA ribbon vote", "Extreme highs", "Extreme lows",
                "Squeeze breakout", "KAMA trend", "Z-score reversion", "Connors RSI(2)", "Funding z-score",
                "Trend + funding filter", "Ichimoku cloud", "Parabolic SAR", "Regime switch (CHOP)", "Volume breakout"]


def synth(n: int, seed: int = 0, freq: str = "1h") -> pd.DataFrame:
    """Синтетический OHLCV (случайное блуждание) + funding/funding_last/oi/lsr."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq=freq, tz="UTC")
    c = 20000 * np.exp(np.cumsum(rng.normal(0, 0.004, n) + 0.0001 * np.sin(np.arange(n) / 300)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.001, n))
    hi = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.002, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.002, n)))
    vol = np.exp(rng.normal(5, 0.5, n))
    f_step = np.where(np.arange(n) % 8 == 0, rng.normal(1e-4, 1.5e-4, n), np.nan)
    f_step[0] = 1e-4
    funding_last = pd.Series(f_step, index=idx).ffill()
    funding_last.iloc[:10] = np.nan                       # как на реальных данных до первой ставки
    df = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": vol}, index=idx)
    df["funding"] = pd.Series(f_step, index=idx).fillna(0.0)
    df["funding_last"] = funding_last
    df["oi"] = 1e9 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    df["lsr"] = 1.5 + 0.2 * np.sin(np.arange(n) / 50) + rng.normal(0, 0.05, n)
    return df


@pytest.fixture(scope="module")
def frame4k():
    return synth(4000, seed=1)


def test_registry_has_all_new_in_order():
    assert NEW == EXPECTED_NEW
    assert len(STRATEGIES) == OLD_COUNT + len(EXPECTED_NEW)


# ------------------------------------------------------------------------------------------ (a) причинность
@pytest.mark.parametrize("name", list(STRATEGIES))
def test_causality_default_params(name, frame4k):
    st = STRATEGIES[name]
    full = st.fn(frame4k, **st.default)
    for cut in (1500, 2600, 3700):
        part = st.fn(frame4k.iloc[:cut], **st.default)
        np.testing.assert_allclose(full.iloc[:cut].to_numpy(float), part.to_numpy(float), atol=1e-9, rtol=0,
                                   equal_nan=True, err_msg=f"{name}: сигнал зависит от будущего (cut={cut})")


@pytest.mark.parametrize("name", NEW)
def test_causality_grid_corners(name, frame4k):
    """Те же проверки на крайних точках сетки (первая и последняя комбинация)."""
    st = STRATEGIES[name]
    combos = list(st.combos())
    for p in (combos[0], combos[-1]):
        full = st.fn(frame4k, **p)
        part = st.fn(frame4k.iloc[:2200], **p)
        np.testing.assert_allclose(full.iloc[:2200].to_numpy(float), part.to_numpy(float), atol=1e-9, rtol=0,
                                   err_msg=f"{name} {p}")


# ------------------------------------------------------------------------------------------ (b) область значений
@pytest.mark.parametrize("name", list(STRATEGIES))
def test_output_domain(name, frame4k):
    st = STRATEGIES[name]
    s = st.fn(frame4k, **st.default)
    assert isinstance(s, pd.Series) and s.index.equals(frame4k.index)
    v = s.dropna().to_numpy(float)
    assert np.isfinite(v).all() and np.abs(v).max() <= 1.5 + 1e-12
    if name in NEW:
        assert s.notna().all()                                  # прогрев => 0.0, а не NaN
        if name not in CAP_OK:
            assert np.abs(v).max() <= 1.0 + 1e-12


def test_voltarget_cap_and_off_switch(frame4k):
    on = STRATEGIES["TSMOM vol-target"].fn(frame4k, h=6, vt=0.5, vol_win=20)
    assert on.abs().max() <= 1.5 + 1e-12
    off = STRATEGIES["TSMOM vol-target"].fn(frame4k, h=6, vt=0.0, vol_win=20)
    assert off.abs().max() <= 1.0 + 1e-12
    en = STRATEGIES["Donchian ensemble (trail)"].fn(frame4k, lo=5, hi=60, vt=0.4)
    assert en.min() >= 0 and en.max() <= 1.5 + 1e-12


def test_warmup_is_flat(frame4k):
    for name, k in (("TSMOM vol-target", 12 * 24), ("MA ribbon vote", 80), ("Ichimoku cloud", 52 + 26 - 1)):
        st = STRATEGIES[name]
        s = st.fn(frame4k, **st.default)
        assert (s.iloc[: k - 1] == 0).all(), name


# ------------------------------------------------------------------------------------------ индикаторы
def test_bars_per_year():
    assert bars_per_year(synth(50, freq="1h")) == pytest.approx(365.25 * 24)
    assert bars_per_year(synth(50, freq="15min")) == pytest.approx(365.25 * 24 * 4)
    assert bars_per_year(synth(50, freq="1D")) == pytest.approx(365.25)


def test_timed_hold_semantics():
    idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    ev = pd.Series([False, True, False, False, False, False, True, True, False, False], index=idx)
    out = timed_hold(ev, 3)
    assert out.tolist() == [0, 1, 1, 1, 0, 0, 1, 1, 1, 1]
    # со стопом, но без срабатывания, результат тот же
    c = pd.Series(100.0, index=idx)
    a = pd.Series(1.0, index=idx)
    assert timed_hold(ev, 3, c, a, 3.0).tolist() == out.tolist()
    # цена ушла ниже стопа (close - 3*ATR на входе = 97) -> выход, ждём следующего события
    c2 = pd.Series([100, 100, 100, 90, 90, 90, 90, 90, 90, 90.0], index=idx)
    assert timed_hold(ev, 3, c2, a, 3.0).tolist() == [0, 1, 1, 0, 0, 0, 1, 1, 1, 1]


def test_parabolic_sar_trend_flips():
    n = 60
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    c = np.r_[np.linspace(100, 130, 30), np.linspace(130, 90, 30)]
    df = pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1.0}, index=idx)
    s = STRATEGIES["Parabolic SAR"].fn(df, af_start=0.02, af_step=0.02, af_max=0.2)
    assert s.iloc[0] == 0 and s.iloc[10] == 1 and s.iloc[-1] == -1


def test_ichimoku_uses_only_past_cloud(frame4k):
    """Изменение данных строго после t не меняет сигнал в t (в т.ч. из-за смещения облака)."""
    st = STRATEGIES["Ichimoku cloud"]
    a = st.fn(frame4k, **st.default)
    b_df = frame4k.copy()
    b_df.iloc[2000:, b_df.columns.get_indexer(["open", "high", "low", "close"])] *= 1.7
    b = st.fn(b_df, **st.default)
    np.testing.assert_allclose(a.iloc[:2000].to_numpy(), b.iloc[:2000].to_numpy(), atol=1e-12)


# ------------------------------------------------------------------------------------------ (c) реальные данные
def _real(tf):
    from tradebot.market import derivs, klines
    return derivs.enrich(klines.load("BTCUSDT", tf, 1095, refresh=False), "BTCUSDT", refresh=False)


_need = [MARKET / f"BTCUSDT_{x}.parquet" for x in ("1h", "4h", "funding", "oi_1h", "oi_4h", "lsr_1h", "lsr_4h")]
needs_real = pytest.mark.skipif(not all(p.exists() for p in _need), reason="нет локального кэша BTCUSDT 1h/4h")


@needs_real
@pytest.mark.realdata
@pytest.mark.parametrize("tf", ["1h", "4h"])
@pytest.mark.parametrize("name", NEW)
def test_real_data_smoke(name, tf):
    df = _real(tf)
    st = STRATEGIES[name]
    s = st.fn(df, **st.default)
    assert s.index.equals(df.index) and np.isfinite(s.to_numpy(float)).all()
    assert (s != 0).any(), f"{name} {tf}: сигнал константно 0"
    res = backtest(df, s)
    assert res.metrics["trades"] > 0, f"{name} {tf}: нет сделок"


# ------------------------------------------------------------------------------------------ (d) скорость
@pytest.fixture(scope="module")
def frame26k():
    return synth(26_000, seed=2)


@pytest.mark.perf
@pytest.mark.parametrize("name", NEW)
def test_timing_default_under_2s(name, frame26k):
    st = STRATEGIES[name]
    t = time.perf_counter()
    st.fn(frame26k, **st.default)
    assert time.perf_counter() - t < 2.0


@pytest.mark.perf
@pytest.mark.parametrize("name", NEW)
def test_timing_full_grid(name, frame26k):
    """Цель — около 2 с на всю сетку; порог тут с запасом на шум загруженной машины."""
    st = STRATEGIES[name]
    t = time.perf_counter()
    for p in st.combos():
        st.fn(frame26k, **p)
    assert time.perf_counter() - t < 5.0


# ------------------------------------------------------------------------------------------ (e) сетки
@pytest.mark.parametrize("name", NEW)
def test_grid_sanity(name):
    st = STRATEGIES[name]
    assert set(st.default) == set(st.grid)
    for k, vals in st.grid.items():
        assert k == k.lower() and k.isidentifier()
        assert all(type(v) in (int, float) for v in vals), (name, k)        # без bool/str
        assert len({type(v) for v in vals}) == 1, f"{name}.{k}: смешаны int и float"
        assert type(st.default[k]) is type(vals[0]) and st.default[k] in vals
    assert sum(1 for _ in st.combos()) <= 100
    assert st.derivative == (name in DERIV_NEW)


@pytest.mark.parametrize("name", NEW)
def test_all_combos_run(name):
    df = synth(3000, seed=3)
    st = STRATEGIES[name]
    for p in st.combos():
        s = st.fn(df, **p)
        assert s.index.equals(df.index) and np.isfinite(s.to_numpy(float)).all(), (name, p)
        assert np.abs(s.to_numpy(float)).max() <= 1.5 + 1e-12


# ------------------------------------------------------------------------------------------ веб-API
def test_api_meta_lists_new_strategies():
    from fastapi.testclient import TestClient
    from tradebot.web.api import app
    m = TestClient(app).get("/api/meta").json()
    by = {s["name"]: s for s in m["strategies"]}
    for name in EXPECTED_NEW:
        assert name in by, name
        spec = by[name]
        assert spec["desc"] and spec["derivative"] is (name in DERIV_NEW)
        assert set(spec["params"]) == set(STRATEGIES[name].grid)
        for k, ps in spec["params"].items():
            assert ps["min"] <= ps["default"] <= ps["max"], (name, k, ps)
            assert ps["kind"] in ("int", "float") and ps["step"] > 0
            assert ps["kind"] == ("int" if isinstance(STRATEGIES[name].default[k], int) else "float")
    # старые пометки не изменились
    assert by["Funding contrarian"]["derivative"] and by["OI flush"]["derivative"]
    assert not by["EMA cross"]["derivative"]
