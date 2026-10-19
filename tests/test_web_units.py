"""Юнит-тесты чистых функций веба: даунсэмплинг, clean(), границы слайдеров, маркеры сделок, фасад service."""
import json
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from _synth import synth_market  # noqa: F401
from tradebot.core.strategies import Strategy
from tradebot.web import serialize as S
from tradebot.web import service


# ---------------------------------------------------------------- pick_indices / bucket_mean
def test_pick_indices_small_series_is_identity():
    for m in (0, 1, 5, 1500):
        assert S.pick_indices(np.arange(m, dtype=float), 1500).tolist() == list(range(m))


@pytest.mark.parametrize("seed,m,n", [(1, 10_000, 1500), (2, 1501, 1500), (3, 100_000, 200), (4, 5000, 3)])
def test_pick_indices_keeps_endpoints_and_global_extrema(seed, m, n):
    v = np.cumsum(np.random.default_rng(seed).normal(size=m))
    idx = S.pick_indices(v, n)
    assert idx[0] == 0 and idx[-1] == m - 1
    assert int(v.argmin()) in idx and int(v.argmax()) in idx
    assert (np.diff(idx) > 0).all()                       # отсортировано, без повторов
    assert len(idx) <= max(n // 2, 1) * 2 + 2 and len(idx) < m


def test_pick_indices_with_nan_and_flat():
    v = np.cumsum(np.random.default_rng(0).normal(size=4000))
    v[100:200] = np.nan
    idx = S.pick_indices(v, 500)
    assert idx[0] == 0 and idx[-1] == 3999 and len(idx) <= 502
    flat = S.pick_indices(np.ones(4000), 500)
    assert flat[0] == 0 and flat[-1] == 3999


@pytest.mark.parametrize("m,n", [(1000, 400), (401, 400), (400, 400), (7, 400), (12345, 500)])
def test_bucket_mean_lengths_and_mass(m, n):
    rng = np.random.default_rng(m)
    v, t = rng.normal(size=m), np.arange(m, dtype="int64") * 3600
    bt, bv = S.bucket_mean(v, t, n)
    assert len(bt) == len(bv) == min(m, n)
    assert (np.diff(bt) > 0).all() or len(bt) < 2
    if m > n:
        edges = np.linspace(0, m, n + 1).astype(int)
        cnt = np.diff(edges)
        assert np.isclose((bv * cnt).sum(), v.sum())      # среднее по бакетам сохраняет сумму
        assert bt[0] == t[0]
    else:
        assert np.array_equal(bv, v) and np.array_equal(bt, t)


def test_fl_and_num_handle_nonfinite():
    assert S.fl([1.23456789, np.nan, np.inf, -np.inf], 3) == [1.235, None, None, None]
    assert S.num(float("nan")) is None and S.num("x") is None and S.num(None) is None and S.num(1.23456789, 3) == 1.235


# ---------------------------------------------------------------- clean()
def test_clean_nonfinite_and_numpy_types():
    obj = {"nan": float("nan"), "inf": float("inf"), "ninf": -float("inf"), "negzero": -0.0,
           "np_f32": np.float32(0.1), "np_i": np.int64(7), "np_b": np.bool_(True), "np_nan": np.float64("nan"),
           "arr": np.array([1.0, np.nan, np.inf]), "tuple": (1, np.int32(2)), 5: "intkey",
           "ts": pd.Timestamp("2026-01-01", tz="UTC"), "nat": pd.NaT, "none": None, "s": "x", "b": False}
    out = S.clean(obj)
    assert out["nan"] is None and out["inf"] is None and out["ninf"] is None and out["np_nan"] is None
    assert out["negzero"] == 0 and out["np_f32"] == 0.1 and out["np_i"] == 7 and type(out["np_i"]) is int
    assert out["np_b"] is True and out["arr"] == [1.0, None, None] and out["tuple"] == [1, 2]
    assert out["5"] == "intkey" and out["ts"].startswith("2026-01-01T00:00:00") and out["nat"] is None
    assert out["none"] is None and out["b"] is False
    json.dumps(out, allow_nan=False)                       # строго JSON-совместимо


def test_clean_frames_series_and_nat():
    df = pd.DataFrame({"a": [1.0, np.nan], "t": [pd.Timestamp("2026-01-01"), pd.NaT], "i": [1, 2]})
    out = S.clean({"df": df, "series": pd.Series([1.5, np.nan, 3.0])})
    assert out["df"][0]["a"] == 1.0 and out["df"][1]["a"] is None and out["df"][1]["t"] is None
    assert out["df"][0]["t"].startswith("2026-01-01") and out["series"] == [1.5, None, 3.0]
    json.dumps(out, allow_nan=False)


def test_clean_unknown_types_become_strings():
    assert S.clean({"o": object}) == {"o": str(object)}
    assert S.clean(pd.Timedelta(hours=1)) == str(pd.Timedelta(hours=1))


# ---------------------------------------------------------------- param_specs
def _strategy(grid, default):
    return Strategy("t", "d", lambda df, **p: pd.Series(0.0, index=df.index), grid, default)


def _check_contains(st):
    specs = service.param_specs(st)
    for name, sp in specs.items():
        assert sp["min"] <= min(st.grid[name]) and sp["max"] >= max(st.grid[name]), (name, sp)
        assert sp["min"] <= sp["default"] <= sp["max"], (name, sp)
        assert sp["step"] > 0
    return specs


def test_param_specs_single_value_grid():
    sp = _check_contains(_strategy({"n": [5], "x": [2.0]}, {"n": 5, "x": 2.0}))
    assert sp["n"]["kind"] == "int" and sp["n"]["min"] == 3 and sp["n"]["max"] == 8 and sp["n"]["step"] == 1
    assert sp["x"]["kind"] == "float" and sp["x"]["step"] == 0.1 and sp["x"]["min"] < 2.0 < sp["x"]["max"]
    assert sp["n"]["grid"] == [5]


def test_param_specs_zero_and_negative_grids():
    sp = _check_contains(_strategy({"z": [0], "neg": [-4.0, -2.0, -1.0], "mix": [-3, 0, 5], "negint": [-10, -5]},
                                   {"z": 0, "neg": -2.0, "mix": 0, "negint": -5}))
    assert sp["z"]["min"] == sp["z"]["max"] == 0           # вырожденный слайдер, но не падает
    assert sp["neg"]["max"] > -1.0 and sp["neg"]["min"] < -4.0
    assert sp["mix"]["kind"] == "int" and sp["mix"]["min"] <= -3 and sp["mix"]["max"] >= 5
    assert sp["negint"]["min"] < -10 and sp["negint"]["max"] > -5


def test_param_specs_all_registered_strategies_are_consistent():
    from tradebot.core.strategies import STRATEGIES
    for st in STRATEGIES.values():
        _check_contains(st)


def test_clean_params_clamps_and_rejects_garbage():
    st = _strategy({"n": [5, 10], "x": [1.0, 2.0]}, {"n": 5, "x": 1.0})
    sp = service.param_specs(st)
    out = service.clean_params(st, {"n": 10 ** 9, "x": float("nan")})
    assert out == {"n": sp["n"]["max"], "x": 1.0} and isinstance(out["n"], int) and isinstance(out["x"], float)
    assert service.clean_params(st, {"n": -5, "x": "boom", "extra": 3}) == {"n": sp["n"]["min"], "x": 1.0}


# ---------------------------------------------------------------- _markers
def _fake_result(n_trades, open_last, n_bars=None):
    n_bars = n_bars or n_trades * 3 + 5
    idx = pd.date_range("2026-01-01", periods=n_bars, freq="h", tz="UTC")
    sub = pd.DataFrame({"close": np.linspace(100, 200, n_bars)}, index=idx)
    entry = [idx[3 * k + 1] for k in range(n_trades)]
    exit_ = [idx[3 * k + 3] for k in range(n_trades)]
    trades = pd.DataFrame({"entry": entry, "exit": exit_, "side": ["long" if k % 2 == 0 else "short" for k in range(n_trades)],
                           "return": np.linspace(-0.01, 0.02, n_trades)})
    pos = pd.Series(0.0, index=idx)
    if open_last:
        pos.iloc[-1] = 1.0
    return sub, SimpleNamespace(trades=trades, position=pos)


def test_markers_empty():
    sub, res = _fake_result(1, False)
    res.trades = res.trades.iloc[:0]
    assert service._markers(sub, res) == {"entries": [], "exits": [], "truncated": False}


@pytest.mark.parametrize("open_last", [False, True])
def test_markers_without_truncation_pair_up(open_last):
    sub, res = _fake_result(10, open_last)
    m = service._markers(sub, res, cap=50)
    assert m["truncated"] is False and len(m["entries"]) == 10 and len(m["exits"]) == (9 if open_last else 10)
    assert [e["side"] for e in m["exits"]] == [e["side"] for e in m["entries"]][:len(m["exits"])]


@pytest.mark.parametrize("open_last", [False, True])
def test_markers_truncation_keeps_entries_and_exits_consistent(open_last):
    """Усечение по числу сделок: каждый оставшийся выход принадлежит сделке, чей вход тоже остался."""
    n, cap = 40, 12
    sub, res = _fake_result(n, open_last)
    m = service._markers(sub, res, cap=cap)
    assert m["truncated"] is True and len(m["entries"]) == cap
    assert len(m["exits"]) == (cap - 1 if open_last else cap)
    first_entry_t = m["entries"][0]["t"]
    assert all(x["t"] > first_entry_t for x in m["exits"])                 # нет «осиротевших» выходов
    last_entries = res.trades["entry"].iloc[-cap:]
    assert m["entries"][-1]["side"] == res.trades["side"].iloc[-1]
    assert len(last_entries) == cap
    # выход k-й оставшейся пары позже её входа
    for e, x in zip(m["entries"], m["exits"]):
        assert x["t"] > e["t"] and x["side"] == e["side"]


# ---------------------------------------------------------------- даунсэмплинг payload: _pick / _package / маркеры на линии цены
def _big_result(n_bars=9000, spike_at=4321, n_trades=300, seed=0):
    from tradebot.web import backtests as B
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n_bars, freq="h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n_bars)))
    close[spike_at] *= 1.5                                    # одиночный выброс цены
    sub = pd.DataFrame({"close": close}, index=idx)
    eq = pd.Series(np.exp(np.cumsum(rng.normal(0, 0.0005, n_bars))), index=idx)
    step = n_bars // (n_trades + 1)
    entry = [idx[1 + step * k] for k in range(n_trades)]
    exit_ = [idx[1 + step * k + step // 2] for k in range(n_trades)]
    trades = pd.DataFrame({"entry": entry, "exit": exit_, "side": ["long" if k % 2 == 0 else "short" for k in range(n_trades)],
                           "return": rng.normal(0, 0.01, n_trades)})
    pos = pd.Series(0.0, index=idx)
    metrics = {"total_return": 0.1}
    res = SimpleNamespace(trades=trades, position=pos, equity=eq, bh_equity=eq * 0.9 + 0.1, metrics=metrics)
    return B, sub, res


def test_package_keeps_price_spike_and_aligned_lengths():
    B, sub, res = _big_result()
    out = B._package(sub, res)
    n = len(out["t"])
    assert B.SERIES_CAP * 0.5 < n <= B.SERIES_CAP < len(sub)
    for k in ("equity", "bh", "drawdown", "price"):
        assert len(out[k]) == n, k
    assert "position" not in out
    assert max(out["price"]) == pytest.approx(sub["close"].max(), abs=1e-3)       # выброс цены не потерян
    assert (np.diff(out["t"]) > 0).all() and out["t"][0] == S.secs(sub.index)[0] and out["t"][-1] == S.secs(sub.index)[-1]
    dd = res.equity / res.equity.cummax() - 1
    assert min(out["drawdown"]) == pytest.approx(dd.min(), abs=1e-5)


def test_package_markers_lie_on_sampled_price_line():
    B, sub, res = _big_result(n_trades=900)
    out = B._package(sub, res)
    sampled = dict(zip(out["t"], out["price"]))
    ms = out["markers"]["entries"] + out["markers"]["exits"]
    assert len(ms) == 1800 and len(out["t"]) == B.SERIES_CAP                          # запас исчерпан: часть маркеров притянута
    for m in ms:
        assert m["t"] in sampled and sampled[m["t"]] == pytest.approx(m["p"], abs=1e-4)
    # новые маркеры (приоритет) лежат на своих точных барах
    exact = {int(S.secs(sub.index[i:i + 1])[0]): round(float(sub["close"].iloc[i]), 4) for i in range(len(sub))}
    last = out["markers"]["entries"][-1]
    assert exact[last["t"]] == last["p"]


def test_package_marker_bars_are_added_when_room():
    B, sub, res = _big_result(n_trades=20)
    out = B._package(sub, res)
    ts = set(out["t"])
    pe, ix = B._trade_geometry(sub, res.trades)[2], B._trade_geometry(sub, res.trades)[1]
    for k in range(20):
        assert int(S.secs(sub.index[pe[k]:pe[k] + 1])[0]) in ts and int(S.secs(sub.index[ix[k]:ix[k] + 1])[0]) in ts
    for m in out["markers"]["entries"]:
        i = int(np.searchsorted(S.secs(sub.index), m["t"]))
        assert m["p"] == round(float(sub["close"].iloc[i]), 4)                    # без притяжки: точная цена бара


def test_package_small_series_not_downsampled():
    B, sub, res = _big_result(n_bars=2000, spike_at=100, n_trades=10)
    out = B._package(sub, res)
    assert len(out["t"]) == 2000 == len(out["price"]) and out["price"][100] == round(float(sub["close"].iloc[100]), 4)


def test_pick_respects_cap_and_includes_extrema():
    B, sub, res = _big_result(n_bars=50_000)
    eq, price = res.equity.to_numpy(), sub["close"].to_numpy()
    dd = eq / np.maximum.accumulate(eq) - 1
    pick = B._pick(eq, price, dd, marker_bars=list(range(0, 50_000, 7)))
    assert len(pick) <= B.SERIES_CAP and (np.diff(pick) > 0).all()
    assert {0, 49_999, int(price.argmax()), int(price.argmin()), int(eq.argmax()), int(dd.argmin())} <= set(pick.tolist())


# ---------------------------------------------------------------- фасад service

def test_facade_reexports_everything():
    for name in ("Deadline", "DataMissing", "DataEmpty", "run_backtest", "run_sweep", "run_portfolio", "run_optimize",
                 "run_lb_item", "lb_cached", "lb_plan", "lb_key", "meta", "param_specs", "clean_params", "get_strategy",
                 "get_df", "get_df_lite", "invalidate_data", "data_status", "update_data", "fetch_data", "data_version",
                 "data_fingerprint", "data_sig", "purge_cache", "wf_table", "wf_windows", "wf_curve", "eff_days",
                 "_optimize", "_signal", "_slice", "_markers", "_package", "_info_cache", "_df_cache", "MARKET", "RESULTS",
                 "TIMEFRAMES", "DERIV_STRATEGIES", "WF_LEVS", "PORTFOLIO_TFS", "MAX_SWEEP_CELLS", "fetch_estimate"):
        assert hasattr(service, name), name


def test_facade_monkeypatch_reaches_submodules(synth_market, monkeypatch):
    from tradebot.web.schemas import BacktestReq
    calls = []
    real = service.get_df
    monkeypatch.setattr(service, "get_df", lambda s, t: calls.append((s, t)) or real(s, t))
    service.run_backtest(BacktestReq(strategy="EMA cross", tf="1h", days=60))
    assert calls == [("BTCUSDT", "1h")]
    calls.clear()
    monkeypatch.setattr(service, "invalidate_data", lambda symbol=None: calls.append(("inv", symbol)))
    monkeypatch.setattr(service, "purge_cache", lambda symbol: calls.append(("purge", symbol)) or 0)
    from tradebot.market import derivs, klines
    monkeypatch.setattr(klines, "load", lambda symbol, tf, days=1095, refresh=True: pd.read_parquet(klines._path(symbol, tf)))
    monkeypatch.setattr(derivs, "enrich", lambda df, *a, **k: df)
    service.update_data("BTCUSDT", "1h")
    assert ("inv", "BTCUSDT") in calls and ("purge", "BTCUSDT") in calls


def test_facade_patched_info_cache_and_dirs_are_used(synth_market, monkeypatch, tmp_path):
    fresh = {}
    monkeypatch.setattr(service, "_info_cache", fresh)
    service.data_status("BTCUSDT", "1h")
    assert fresh                                           # _file_info положил записи в подменённый словарь
    other = tmp_path / "other_market"
    other.mkdir()
    monkeypatch.setattr(service, "MARKET", other)
    assert service.available("BTCUSDT", "1h") is False     # деривативные файлы ищутся в подменённой папке


@pytest.mark.parametrize("first", ["tradebot.web.data", "tradebot.web.meta", "tradebot.web.backtests", "tradebot.web.research",
                                   "tradebot.web.wf", "tradebot.web.common", "tradebot.web.service", "tradebot.web.api"])
def test_import_order_independent(first):
    code = f"import {first}; from tradebot.web import service, api; assert service.run_backtest and api.app"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
