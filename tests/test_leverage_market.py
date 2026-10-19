"""Плечо 1x…100x и рынок (перп/спот) в веб-бэктестах: модель ликвидации движка, валидация, спот-семантика, кэши,
список плеч walk-forward с сервера и сверка констант TS <-> сервер."""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from _synth import synth_market  # noqa: F401
from tradebot.core import engine
from tradebot.core.engine import MAINT_MARGIN_RATE, backtest, liquidation_threshold
from tradebot.web import api as api_mod, schemas, service
from tradebot.web.api import app

client = TestClient(app)
WEB_SRC = Path(__file__).resolve().parents[1] / "web" / "src"


def post(url, body, status=200):
    r = client.post(url, json=body)
    assert r.status_code == status, r.text
    return r.json()


# ---------------------------------------------------------------- движок: модель ликвидации
def _frame(n=12, bar=6, adverse=0.0, side="long"):
    """Цена стоит на 100; на баре `bar` экстремум уходит против позиции на `adverse` (доля, > 0) от прошлого close."""
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    close = pd.Series(100.0, index=idx)
    low, high = close.copy(), close.copy()
    if side == "long":
        low.iloc[bar] = 100.0 * (1 - adverse)
    else:
        high.iloc[bar] = 100.0 * (1 + adverse)
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close}, index=idx), pd.Series(1.0 if side == "long" else -1.0, index=idx)


def _liqs(lev, adverse, side="long", **kw):
    df, sig = _frame(adverse=adverse, side=side)
    r = backtest(df, sig, fee=0.0, slippage=0.0, leverage=lev, **kw)
    return r.metrics["liquidations"], r


def test_threshold_formula_and_clamps():
    assert MAINT_MARGIN_RATE == 0.005
    assert liquidation_threshold(1) == pytest.approx(-0.995)
    assert liquidation_threshold(3) == pytest.approx(-0.985)
    assert liquidation_threshold(10) == pytest.approx(-0.95)
    assert liquidation_threshold(50) == pytest.approx(-0.75)
    assert liquidation_threshold(100) == pytest.approx(-0.5)
    assert liquidation_threshold(150) == -0.5        # не жёстче -0.5
    assert liquidation_threshold(0.5) == -0.995       # не мягче -0.995
    assert liquidation_threshold(10, mmr=0.0) == -0.995       # -(1 - 0) = -1 зажато в -0.995


@pytest.mark.parametrize("lev", [1, 3, 10, 50, 100])
def test_liquidation_distance_matches_documented_move(lev):
    """Ликвидация при движении против позиции на (1/lev − MMR): чуть меньше — жив, чуть больше — ликвидирован (-100 % свечи)."""
    move = 1 / lev - MAINT_MARGIN_RATE
    alive, _ = _liqs(lev, move * 0.99)
    dead, r = _liqs(lev, move * 1.01)
    assert alive == 0
    assert dead == 1
    assert r.bar_returns.min() == -1.0 and r.equity.iloc[-1] == 0.0


def test_documented_examples():
    assert _liqs(100, 0.0051)[0] == 1 and _liqs(100, 0.0049)[0] == 0         # 100x: ≈ 0.5 %
    assert _liqs(3, 0.329)[0] == 1 and _liqs(3, 0.327)[0] == 0                # 3x: ≈ 32.8 %
    assert _liqs(10, 0.0951)[0] == 1 and _liqs(10, 0.094)[0] == 0
    # старая модель (lev*adverse <= -0.95) на 100x ловила только 0.95 %: 0.6 % раньше выживало
    assert _liqs(100, 0.006)[0] == 1


@pytest.mark.parametrize("lev", [3, 10, 50, 100])
def test_short_side_liquidation(lev):
    move = 1 / lev - MAINT_MARGIN_RATE
    assert _liqs(lev, move * 0.99, side="short")[0] == 0
    assert _liqs(lev, move * 1.01, side="short")[0] == 1


def test_no_liquidation_without_leverage_and_mmr_param():
    assert _liqs(1, 0.5)[0] == 0                        # -50 % за свечу на 1x — не ликвидация
    assert _liqs(1, 0.99)[0] == 0                       # даже -99 %: порог -99.5 %
    assert _liqs(1, 0.996)[0] == 1
    # mmr = 0: порог -(1 - 0) зажат в -0.995, на 10x ликвидация только при lev*adv <= -0.995
    assert _liqs(10, 0.0951, mmr=0.0)[0] == 0
    assert _liqs(10, 0.0996, mmr=0.0)[0] == 1
    assert _liqs(10, 0.0951, mmr=0.005)[0] == 1


def test_engine_default_args_unchanged():
    import inspect
    sig = inspect.signature(backtest)
    assert list(sig.parameters)[:6] == ["df", "signal", "fee", "slippage", "leverage", "long_only"]
    assert sig.parameters["mmr"].default == engine.MAINT_MARGIN_RATE


# ---------------------------------------------------------------- валидация плеча на сервере
@pytest.mark.parametrize("lev,ok", [(0.5, False), (0.99, False), (101, False), (100.5, False), (1, True), (100, True), (37.5, True)])
def test_backtest_leverage_bounds(synth_market, lev, ok):
    post("/api/backtest", {"strategy": "EMA cross", "tf": "1h", "days": 60, "leverage": lev}, 200 if ok else 422)


def test_leverage_bounds_all_request_models(synth_market):
    legs = client.get("/api/meta").json()["legs"]
    sweep_axis = {"x": {"name": "fast", "min": 5, "max": 20, "n": 3}}
    bodies = {
        "/api/backtest": {"strategy": "EMA cross"},
        "/api/sweep": {"strategy": "EMA cross", **sweep_axis},
        "/api/portfolio": {"legs": legs},
        "/api/optimize": {"strategy": "EMA cross"},
        "/api/leaderboard/item": {"strategy": "Supertrend"},
        "/api/leaderboard/cached": {},
    }
    for url, b in bodies.items():
        for bad in (0.5, 101):
            assert client.post(url, json={**b, "leverage": bad}).status_code == 422, (url, bad)
        assert client.post(url, json={**b, "leverage": 0.99}).status_code == 422, url
    # верхняя граница принимается везде, где нет тяжёлого расчёта; тяжёлые — одним бэктестом и портфелем
    assert client.post("/api/leaderboard/cached", json={"leverage": 100}).status_code == 200
    assert client.post("/api/portfolio", json={"legs": legs, "days": 90, "leverage": 100}).status_code == 200


def test_market_whitelist_422(synth_market):
    for url, b in (("/api/backtest", {"strategy": "EMA cross"}), ("/api/leaderboard/cached", {})):
        assert client.post(url, json={**b, "market": "margin"}).status_code == 422
        assert client.post(url, json={**b, "market": "SPOT"}).status_code == 422


def test_liquidations_at_100x_through_api(synth_market):
    """Синтетические свечи ±0.3 % + шаг 0.4 %: на 100x ликвидации неизбежны, на 1x их нет."""
    hi = post("/api/backtest", {"strategy": "EMA cross", "tf": "1h", "days": 60, "leverage": 100})
    lo = post("/api/backtest", {"strategy": "EMA cross", "tf": "1h", "days": 60, "leverage": 1})
    assert hi["metrics"]["liquidations"] > 0 and hi["metrics"]["total_return"] <= -0.99
    assert lo["metrics"]["liquidations"] == 0


# ---------------------------------------------------------------- спот
SPOT_BT = {"strategy": "EMA cross", "tf": "1h", "days": 60, "params": {"fast": 12, "slow": 50}}


def _zero_funding(monkeypatch):
    orig = service.get_df

    def patched(symbol, tf):
        df = orig(symbol, tf).copy()
        df["funding"] = 0.0
        return df
    monkeypatch.setattr(service, "get_df", patched)
    api_mod._cache.clear()           # ответы кэшируются по телу запроса — подмена данных кэш не инвалидирует


def test_spot_forces_leverage_long_only_and_echoes(synth_market):
    sp = post("/api/backtest", {**SPOT_BT, "market": "spot", "leverage": 20, "long_only": False})
    assert sp["market"] == "spot"
    assert sp["effective"] == {"leverage": 1.0, "long_only": True, "fee": 0.001}
    assert sp["metrics"]["liquidations"] == 0
    perp = post("/api/backtest", SPOT_BT)
    assert "market" not in perp and "effective" not in perp          # ответы перпа остаются прежними


def test_spot_has_no_shorts(synth_market):
    perp = post("/api/backtest", SPOT_BT)
    assert any(t["side"] == "short" for t in perp["trades"]), "для проверки нужна стратегия, шортящая в перпе"
    sp = post("/api/backtest", {**SPOT_BT, "market": "spot"})
    assert sp["trades"] and all(t["side"] == "long" for t in sp["trades"])
    assert all(m["side"] == "long" for m in sp["markers"]["entries"] + sp["markers"]["exits"])


def test_spot_equals_perp_lev1_longonly_without_funding(synth_market, monkeypatch):
    sp = post("/api/backtest", {**SPOT_BT, "market": "spot", "leverage": 20})
    with_funding = post("/api/backtest", {**SPOT_BT, "long_only": True, "fee": 0.001})
    _zero_funding(monkeypatch)
    ref = post("/api/backtest", {**SPOT_BT, "long_only": True, "fee": 0.001})
    assert sp["equity"] == ref["equity"] and sp["metrics"] == ref["metrics"] and sp["trades"] == ref["trades"]
    assert with_funding["equity"] != sp["equity"], "фандинг в перпе должен влиять на результат"


def test_spot_fee_default_and_explicit(synth_market):
    default_fee = post("/api/backtest", {**SPOT_BT, "market": "spot"})
    assert default_fee["effective"]["fee"] == 0.001
    explicit = post("/api/backtest", {**SPOT_BT, "market": "spot", "fee": 0.00055})
    assert explicit["effective"]["fee"] == 0.00055
    assert explicit["equity"] != default_fee["equity"]
    explicit_same = post("/api/backtest", {**SPOT_BT, "market": "spot", "fee": 0.001})
    assert explicit_same["equity"] == default_fee["equity"]
    assert schemas.SPOT_FEE == 0.001 and schemas.PERP_FEE == 0.00055
    assert schemas.BacktestReq(strategy="x").fee == 0.00055                    # перп: умолчание прежнее


def test_spot_request_is_canonical_for_cache_keys():
    a = schemas.BacktestReq(strategy="x", market="spot", leverage=20, long_only=False)
    b = schemas.BacktestReq(strategy="x", market="spot", leverage=1, long_only=True)
    assert a.model_dump_json() == b.model_dump_json()
    p = schemas.PortfolioReq(legs=[schemas.LegReq(strategy="x", tf="4h")], market="spot")
    assert p.legs[0].long_only and p.leverage == 1.0 and p.fee == 0.001
    assert schemas.BacktestReq(strategy="x", market="perp").model_dump_json() != a.model_dump_json()


def test_apply_market_helper():
    from tradebot.web.backtests import apply_market
    e = apply_market(schemas.BacktestReq(strategy="x", market="spot", leverage=50))
    assert (e.market, e.leverage, e.long_only, e.fee, e.drop_funding) == ("spot", 1.0, True, 0.001, True)
    e = apply_market(schemas.BacktestReq(strategy="x", leverage=50, long_only=False))
    assert (e.market, e.leverage, e.long_only, e.fee, e.drop_funding) == ("perp", 50.0, False, 0.00055, False)
    assert e.echo() == {}
    df = pd.DataFrame({"close": [1.0, 2.0], "funding": [0.1, 0.1]})
    assert "funding" not in apply_market(schemas.BacktestReq(strategy="x", market="spot")).bt_df(df)
    assert "funding" in e.bt_df(df)


def test_spot_portfolio_per_leg_long_only(synth_market, monkeypatch):
    legs = client.get("/api/meta").json()["legs"]
    assert any(not l["long_only"] for l in legs)
    sp = post("/api/portfolio", {"legs": legs, "days": 90, "leverage": 5, "market": "spot"})
    assert sp["market"] == "spot" and sp["effective"] == {"leverage": 1.0, "long_only": True, "fee": 0.001}
    assert all(l["long_only"] for l in sp["legs"])
    assert min(v for v in sp["strips"]["exposure"] if v is not None) >= 0          # ни одна нога не даёт шорт
    for vals in sp["strips"]["legs"].values():
        assert min(v for v in vals if v is not None) >= 0
    assert all(t["side"] == "long" for t in sp["trades"])
    # то же самое, что перп 1x с лонг-ногами, комиссией спота и без фандинга
    _zero_funding(monkeypatch)
    ref_legs = [{**l, "long_only": True} for l in legs]
    ref = post("/api/portfolio", {"legs": ref_legs, "days": 90, "leverage": 1, "fee": 0.001})
    assert sp["equity"] == ref["equity"] and sp["metrics"] == ref["metrics"]


def test_spot_sweep_and_derivative_strategy(synth_market):
    sw = post("/api/sweep", {"strategy": "EMA cross", "tf": "1h", "days": 60, "market": "spot", "leverage": 10,
                             "x": {"name": "fast", "min": 5, "max": 20, "n": 3}})
    assert sw["market"] == "spot" and sw["effective"]["leverage"] == 1.0
    assert all(p.get("liquidations", 0) == 0 for p in sw["points"])
    # деривативная стратегия в споте: сигналы считаются (по данным перпа), фандинг не начисляется, шортов нет
    oi = post("/api/backtest", {"strategy": "OI flush", "tf": "1h", "days": 90, "market": "spot"})
    assert oi["market"] == "spot" and all(t["side"] == "long" for t in oi["trades"])


def test_spot_is_part_of_all_cache_keys(synth_market):
    # leaderboard: диск
    body = {"tf": "1h", "strategy": "Supertrend", "days": 120}
    perp = post("/api/leaderboard/item", body)
    assert perp["from_cache"] is False and "market" not in perp and perp["funding_included"] is True
    spot = post("/api/leaderboard/item", {**body, "market": "spot"})
    assert spot["from_cache"] is False, "спот не должен брать карточку перпа"
    assert spot["market"] == "spot" and spot["effective"] == {"leverage": 1.0, "long_only": True, "fee": 0.001}
    assert spot["funding_included"] is False
    assert post("/api/leaderboard/item", {**body, "market": "spot"})["from_cache"] is True
    cached_spot = post("/api/leaderboard/cached", {"tf": "1h", "days": 120, "market": "spot"})
    cached_perp = post("/api/leaderboard/cached", {"tf": "1h", "days": 120})
    assert [i["strategy"] for i in cached_spot["items"]] == ["Supertrend"] and cached_spot["items"][0]["market"] == "spot"
    assert [i["strategy"] for i in cached_perp["items"]] == ["Supertrend"] and "market" not in cached_perp["items"][0]
    assert post("/api/leaderboard/cached", {"tf": "1h", "days": 120, "market": "spot", "leverage": 50})["items"], \
        "спот каноничен: плечо в запросе не влияет на ключ"
    # optimize: диск + память процесса
    ob = {"tf": "1h", "strategy": "EMA cross", "days": 120, "top": 2}
    o_perp = post("/api/optimize", ob)
    o_spot = post("/api/optimize", {**ob, "market": "spot"})
    assert o_perp["from_cache"] is False and o_spot["from_cache"] is False
    assert o_spot["market"] == "spot" and "market" not in o_perp
    assert post("/api/optimize", {**ob, "market": "spot"})["from_cache"] is True
    # backtest: кэш процесса (ключ = тело запроса)
    a = post("/api/backtest", {**SPOT_BT, "market": "spot"})
    b = post("/api/backtest", SPOT_BT)
    assert a["equity"] != b["equity"]


def test_optimize_spot_has_no_shorts_and_matches_perp_lev1_longonly(synth_market, monkeypatch):
    ob = {"tf": "1h", "strategy": "EMA cross", "days": 120, "top": 3}
    sp = post("/api/optimize", {**ob, "market": "spot", "leverage": 30})
    _zero_funding(monkeypatch)
    ref = post("/api/optimize", {**ob, "long_only": True, "fee": 0.001})
    assert [r["params"] for r in sp["results"]] == [r["params"] for r in ref["results"]]
    assert [r["full"] for r in sp["results"]] == [r["full"] for r in ref["results"]]


# ---------------------------------------------------------------- walk-forward: список плеч с сервера
def test_wf_levs_come_from_result_files(synth_market):
    _, res = synth_market
    row = "tf,strategy,variant,windows,windows_positive,wf_total_return,wf_max_drawdown,wf_liquidations\n1h,S,base,8,0.5,-0.9,-1.0,0\n"
    for name in ("wf_BTCUSDT.csv", "wf_BTCUSDT_lev3.csv", "wf_BTCUSDT_lev10.csv", "wf_BTCUSDT_lev20.csv"):
        (res / name).write_text(row)
    (res / "wf_BTCUSDT_lev500.csv").write_text(row)            # вне диапазона — игнорируется
    (res / "wf_BTCUSDT_levx.csv").write_text(row)
    (res / "wf_windows_BTCUSDT_lev7.csv").write_text(row)      # другой вид файла — не плечо
    assert client.get("/api/meta").json()["walkforward_levs"] == [1, 3, 10, 20]
    for lev in (1, 3, 10, 20):
        r = client.get("/api/walkforward", params={"lev": lev})
        assert r.status_code == 200 and r.json()[0]["strategy"] == "S"
    assert client.get("/api/walkforward", params={"lev": 7}).status_code == 422       # файла нет
    assert client.get("/api/walkforward", params={"lev": 4}).status_code == 422


def test_wf_levs_empty_without_results(synth_market):
    assert client.get("/api/meta").json()["walkforward_levs"] == []
    assert client.get("/api/walkforward", params={"lev": 1}).status_code == 404       # как раньше: нет файла


# ---------------------------------------------------------------- константы TS <-> сервер
def test_ts_constants_match_server():
    lev = (WEB_SRC / "leverage.ts").read_text(encoding="utf-8")
    mkt = (WEB_SRC / "market.ts").read_text(encoding="utf-8")

    def const(src, name):
        m = re.search(rf"export const {name}\s*=\s*([0-9.]+)", src)
        assert m, name
        return float(m.group(1))
    assert const(lev, "MAX_LEVERAGE") == schemas.MAX_LEVERAGE
    assert const(lev, "BOT_MAX_LEVERAGE") == schemas.BOT_MAX_LEVERAGE
    assert const(lev, "MMR") == MAINT_MARGIN_RATE
    assert const(lev, "MIN_LEVERAGE") == 1
    fee = re.search(r"FEE_PCT[^=]*=\s*\{\s*perp:\s*([0-9.]+),\s*spot:\s*([0-9.]+)", mkt)
    assert fee and float(fee.group(1)) / 100 == pytest.approx(schemas.PERP_FEE) and float(fee.group(2)) / 100 == pytest.approx(schemas.SPOT_FEE)
    from tradebot.trading.config import MAX_LEVERAGE as BOT
    assert schemas.BOT_MAX_LEVERAGE == BOT == 3          # потолок живого бота не менялся
