"""Тесты честного оптимизатора (синтетические данные, без сети)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from tradebot.core import optimize as opt

RANK_FIXTURE = Path(__file__).resolve().parents[1] / "web" / "tests" / "fixtures" / "rank.json"
_TIER = {"robust": 0, "overfit": 1, "few": 2, "loss": 3}
_RANK_KEYS = {"calmar": "calmar", "sharpe": "sharpe", "return": "total_return", "dd": "max_drawdown"}


def _oracle_rank(items: list[dict], criterion: str = "calmar") -> list[dict]:
    """ЭТАЛОН порядка рейтинга для web/tests/fixtures/rank.json (на сервере рейтинга нет: карточки сортирует фронтенд,
    web/src/tabs/lbRank.ts). Сначала ярус вердикта (robust > overfit > few > loss), внутри — критерий по OOS (больше — лучше;
    для 'dd' — ближе к нулю). Элементы без OOS-метрики уходят в конец яруса."""
    key = _RANK_KEYS[criterion]

    def k(it: dict):
        v = (it.get("oos") or {}).get(key)
        if criterion == "dd" and not (it.get("oos") or {}).get("trades"):
            v = None
        return (_TIER[it["verdict"]], v is None, -(v if v is not None else 0.0))
    return sorted(items, key=k)


def _df(n=6000, seed=3):
    idx = pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    return pd.DataFrame({"open": close, "high": close * 1.002, "low": close * 0.998, "close": close}, index=idx)


def _momentum(df, p):
    return np.sign(df["close"] / df["close"].shift(p["n"]) - 1).fillna(0)


def test_min_trades_scaling():
    assert opt.min_trades(730) == 30 and opt.min_trades(365) == 30
    assert opt.min_trades(180) == 15 and opt.min_trades(30) == 10


def test_optimize_best_by_is_reports_oos_and_respects_min_trades():
    df = _df()
    grid = {"n": [2, 5, 10, 24, 48, 96, 5000]}      # n=5000: почти нет сделок -> не проходит минимум
    out = opt.optimize(df, grid, lambda p: _momentum(df, p), days=250, fee=0.0005, slippage=0.0002,
                       criterion="calmar", top=3, curve=True)
    assert out["n_combos"] == 7 and out["n_valid"] < 7
    res = out["results"]
    assert 1 <= len(res) <= 3
    assert all(r["params"]["n"] != 5000 for r in res)
    assert all(r["is"]["trades"] >= out["min_trades"] for r in res)
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)           # лучший по IS первый
    assert {"total_return", "max_drawdown", "sharpe", "trades"} <= set(res[0]["oos"])
    assert res[0]["verdict"] in ("robust", "overfit", "loss") and "curve" in res[0]
    assert out["is_range"]["end"] < out["oos_range"]["start"] or out["is_range"]["end"] <= out["oos_range"]["start"]
    ratio = out["is_range"]["days"] / (out["is_range"]["days"] + out["oos_range"]["days"])
    assert 0.6 < ratio < 0.74


def test_robust_prefers_plateau_over_spike():
    axes = [[1, 2, 3, 4, 5, 6, 7, 8, 9]]
    scores = {(v,): s for v, s in zip(axes[0], [0.1, 0.1, 2.0, 0.1, 0.1, 1.4, 1.5, 1.5, 1.4])}
    rob = opt.robust_scores(scores, axes)
    assert max(scores, key=scores.get) == (3,)              # одиночный пик лидирует по «сырому» скору
    assert max(rob, key=rob.get) in {(7,), (8,)}            # а по robust выигрывает плато


def test_invalid_neighbour_counts_against():
    nan = float("nan")
    rob = opt.robust_scores({(1,): 1.0, (2,): 1.0, (3,): nan}, [[1, 2, 3]])
    assert rob[(1,)] > rob[(2,)] and (3,) not in rob


def _oos(**kw):
    base = {"total_return": 0.11, "cagr": 0.17, "sharpe": 1.0, "avg_month": 0.03, "green_months": 0.6, "trades": 25,
            "max_drawdown": -0.10}
    return {**base, **kw}


def test_verdict_rules():
    IS = {"cagr": 0.5}
    assert opt.verdict(IS, _oos(total_return=-0.1, cagr=-0.2, sharpe=-1), 240) == "loss"
    assert opt.verdict(IS, _oos(cagr=0.4), 240) == "robust"
    assert opt.verdict({"cagr": 2.0}, _oos(total_return=0.01, cagr=0.05, sharpe=0.9), 240) == "overfit"
    assert opt.verdict(IS, _oos(sharpe=0.2), 240) == "overfit"


def test_few_trades_is_not_a_verdict():
    # 3 сделки, +3%, средний месяц 0, зелёных 0% — раньше это был «robust» с Calmar 15
    thin = _oos(total_return=0.03, cagr=0.045, sharpe=0.9, avg_month=0.0, trades=3, max_drawdown=-0.01, green_months=0.0)
    assert opt.verdict({"cagr": 0.1}, thin, 240) == "few"
    assert opt.verdict({"cagr": 0.1}, _oos(trades=9), 100) == "few"      # минимум на OOS — не меньше 10
    assert opt.verdict({"cagr": 0.1}, _oos(trades=10), 100) == "robust"
    assert opt.verdict({"cagr": 0.1}, _oos(trades=19), 240) == "few"     # на 240 днях минимум = 20
    assert opt.verdict({"cagr": 0.1}, _oos(trades=20), 240) == "robust"


def test_losing_in_sample_is_never_robust():
    # IS -34%: лучшие параметры не заработали даже на подборе; плюс на OOS — удача (случай Bollinger reversion 30/1.5)
    assert opt.verdict({"cagr": -0.4, "total_return": -0.34}, _oos(), 240) == "overfit"
    assert opt.verdict({"cagr": 0.0}, _oos(), 240) == "overfit"


def test_robust_requires_positive_month_and_green_share():
    assert opt.verdict({"cagr": 0.3}, _oos(avg_month=0.0), 240) == "overfit"
    assert opt.verdict({"cagr": 0.3}, _oos(green_months=0.3), 240) == "overfit"


def test_calmar_floor():
    assert abs(opt.calmar_floored({"cagr": 0.045, "max_drawdown": -0.01}) - 0.9) < 1e-9     # пол 5%, а не 4.5
    assert abs(opt.calmar_floored({"cagr": 0.3, "max_drawdown": -0.15}) - 2.0) < 1e-9
    assert opt.score_of({"cagr": 0.045, "max_drawdown": -0.01, "calmar": 15.0, "liquidations": 0}, "calmar") < 1.0


def test_ranking_tiers_then_criterion():
    thin = {"name": "Funding", "verdict": "few", "oos": {"calmar": opt.calmar_floored({"cagr": 0.045, "max_drawdown": -0.01}),
                                                         "sharpe": 0.9, "total_return": 0.03, "trades": 3, "max_drawdown": -0.01}}
    solid = {"name": "EMA+ADX", "verdict": "overfit", "oos": {"calmar": 1.1, "sharpe": 0.4, "total_return": 0.11, "trades": 9,
                                                            "max_drawdown": -0.10}}
    good = {"name": "Good", "verdict": "robust", "oos": {"calmar": 0.5, "sharpe": 0.6, "total_return": 0.05, "trades": 40, "max_drawdown": -0.2}}
    bad = {"name": "Bad", "verdict": "loss", "oos": {"calmar": 9.0, "sharpe": 9.0, "total_return": -0.1, "trades": 40, "max_drawdown": -0.3}}
    for crit in ("calmar", "sharpe", "return", "dd"):
        names = [i["name"] for i in _oracle_rank([bad, thin, solid, good], crit)]
        assert names == ["Good", "EMA+ADX", "Funding", "Bad"], (crit, names)
    a = {"name": "a", "verdict": "robust", "oos": {"calmar": 1.0, "sharpe": 2, "total_return": 0.1, "trades": 30, "max_drawdown": -0.1}}
    b = {"name": "b", "verdict": "robust", "oos": {"calmar": 2.0, "sharpe": 1, "total_return": 0.2, "trades": 30, "max_drawdown": -0.3}}
    assert [i["name"] for i in _oracle_rank([a, b], "calmar")] == ["b", "a"]
    assert [i["name"] for i in _oracle_rank([a, b], "sharpe")] == ["a", "b"]
    assert [i["name"] for i in _oracle_rank([a, b], "dd")] == ["a", "b"]


def test_rank_fixture_order_matches_oracle():
    """Контракт с фронтендом: web/tests/fixtures/rank.json читает и TS-тест (leaderboard.test.ts, splitRanked); здесь — та же
    фикстура против эталона порядка. Расхождение любой из сторон с фикстурой ломает один из двух тестов."""
    fx = json.loads(RANK_FIXTURE.read_text(encoding="utf-8"))
    assert set(fx["expected"]) == set(_RANK_KEYS)
    for crit, names in fx["expected"].items():
        assert [i["name"] for i in _oracle_rank(fx["items"], crit)] == names, crit


def test_short_oos_window_is_always_few():
    great = _oos(trades=60, sharpe=4.96, cagr=3.0, total_return=0.4)
    assert opt.verdict({"cagr": 0.5}, great, 60) == "few"          # <90 дн. OOS — выводов нет, даже при отличных метриках
    assert opt.verdict({"cagr": 0.5}, great, 89) == "few"
    assert opt.verdict({"cagr": 0.5}, great, 120) == "robust"
    assert opt.verdict({"cagr": 0.5}, _oos(total_return=-0.1), 60) == "loss"


def test_optimize_reports_short_oos():
    df = _df(n=2500)
    out = opt.optimize(df, {"n": [5, 24, 48]}, lambda p: _momentum(df, p), days=150, fee=0.0005, slippage=0.0002, criterion="calmar", top=2)
    assert out["short_oos"] is True and out["oos_days"] < 90
    assert all(r["verdict"] in ("few", "loss") for r in out["results"])
    big = _df(n=24000)
    long = opt.optimize(big, {"n": [5, 24, 48]}, lambda p: _momentum(big, p), days=1000, fee=0.0005, slippage=0.0002, criterion="calmar", top=1)
    assert long["short_oos"] is False and long["oos_days"] > 180
