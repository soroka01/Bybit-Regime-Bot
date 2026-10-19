"""Аудит core/indicators + research: мемоизация donchian_trail_state (результат прежний) и smoke-тест research/sweep."""
import numpy as np
import pandas as pd
import pytest

from _synth import bars, build_market
from tradebot.core import indicators as ind
from tradebot.core.strategies import STRATEGIES, Strategy
from tradebot.market import derivs, klines
from tradebot.research import sweep


def _closes(n=800, seed=1, nan_head=0):
    c = bars(1, n, seed)["close"].copy()
    if nan_head:
        c.iloc[:nan_head] = np.nan
    return c


@pytest.fixture(autouse=True)
def _clean_cache():
    ind._TRAIL_CACHE.clear()
    yield
    ind._TRAIL_CACHE.clear()


# ---------------------------------------------------------------- C12: мемоизация
@pytest.mark.parametrize("n", [5, 10, 20, 60, 250])
@pytest.mark.parametrize("nan_head", [0, 7])
def test_donchian_trail_memo_matches_direct_computation(n, nan_head):
    c = _closes(nan_head=nan_head)
    direct = ind._donchian_trail_state(c, n)
    cold = ind.donchian_trail_state(c, n)
    warm = ind.donchian_trail_state(c, n)
    assert np.array_equal(direct, cold) and np.array_equal(direct, warm)
    assert warm.dtype == direct.dtype == np.float64


def test_donchian_trail_memo_hits_and_returns_independent_arrays(monkeypatch):
    c = _closes()
    calls = []
    real = ind._donchian_trail_state
    monkeypatch.setattr(ind, "_donchian_trail_state", lambda *a: calls.append(1) or real(*a))
    a = ind.donchian_trail_state(c, 20)
    b = ind.donchian_trail_state(c.copy(), 20)                    # другой объект Series с тем же содержимым — тоже попадание
    assert len(calls) == 1
    b[:] = 99.0                                                   # правка результата не портит кэш
    assert np.array_equal(ind.donchian_trail_state(c, 20), a)
    ind.donchian_trail_state(c, 21)                               # другое окно / другое содержимое — отдельные записи
    ind.donchian_trail_state(c * 1.0001, 20)
    assert len(calls) == 3


def test_donchian_trail_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(ind, "_TRAIL_CACHE_SIZE", 4)
    c = _closes(200)
    for n in range(3, 12):
        ind.donchian_trail_state(c, n)
    assert len(ind._TRAIL_CACHE) == 4


def test_donchian_ensemble_identical_cold_and_warm():
    df = bars(1, 1500, 9)
    fn = STRATEGIES["Donchian ensemble (trail)"].fn
    for p in ({"lo": 5, "hi": 60, "vt": 0.0}, {"lo": 10, "hi": 120, "vt": 0.25}, {"lo": 20, "hi": 250, "vt": 0.4}):
        ind._TRAIL_CACHE.clear()
        cold = fn(df, **p)
        warm = fn(df, **p)                                         # полностью из кэша
        # эталон без кэша
        orig = ind.donchian_trail_state
        try:
            import tradebot.core.strategies as S
            S.donchian_trail_state = ind._donchian_trail_state
            ref = fn(df, **p)
        finally:
            S.donchian_trail_state = orig
        pd.testing.assert_series_equal(cold, ref)
        pd.testing.assert_series_equal(warm, ref)


# ---------------------------------------------------------------- C3: research/sweep
@pytest.fixture
def sweep_env(tmp_path, monkeypatch):
    d = tmp_path / "market"
    d.mkdir()
    monkeypatch.setattr(klines, "DATA_DIR", d)
    build_market(d)
    # «сеть»: load читает локальный файл целиком (синтетические данные старше «сейчас»), enrich — без докачки
    monkeypatch.setattr(klines, "load", lambda symbol, tf, days=1095, refresh=True: pd.read_parquet(klines._path(symbol, tf)))
    return d


def _tiny(name, fn, derivative=False):
    return Strategy(name, "тест", fn, {"n": [5, 10]}, {"n": 5}, derivative=derivative)


def test_sweep_enriches_and_skips_unrunnable(sweep_env, monkeypatch, capsys, caplog):
    base = STRATEGIES

    def needs_missing(df, n):
        return df["no_such_column"] * n
    picked = {
        "EMA cross": base["EMA cross"],
        "OI flush": base["OI flush"],            # деривативная: раньше без enrich падала KeyError и обрывала перебор
        "Broken": _tiny("Broken", needs_missing),
    }
    monkeypatch.setattr(sweep, "STRATEGIES", picked)
    res, grid = sweep.run("BTCUSDT", ["1h"], 1.0, False, 0.00055, 0.0002, 365, refresh=False)
    names = set(res["strategy"])
    assert {"EMA cross", "OI flush"} <= names and "Broken" not in names
    assert set(grid["strategy"]) == names
    assert any("Broken" in r.getMessage() and "пропущена" in r.getMessage() for r in caplog.records)
    assert "пропущена" in capsys.readouterr().out
    assert {"is_total_return", "oos_total_return"} <= set(res.columns)


def test_sweep_runnable_reports_reason(sweep_env):
    df = derivs.enrich(pd.read_parquet(klines._path("BTCUSDT", "1h")), "BTCUSDT", refresh=False)
    assert sweep.runnable(STRATEGIES["EMA cross"], df) is None
    assert sweep.runnable(STRATEGIES["OI flush"], df) is None
    plain = pd.read_parquet(klines._path("BTCUSDT", "1h"))                       # без деривативных колонок
    assert "oi" in (sweep.runnable(STRATEGIES["OI flush"], plain) or "")
