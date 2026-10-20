"""Контракт «бэкенд <-> web/src/types.ts»: поля верхнего уровня интерфейсов TS сверяются с РЕАЛЬНЫМИ ответами API
(TestClient + синтетический рынок, без сети). Правила для каждого проверяемого объекта:
  1) каждое обязательное поле TS есть в ответе;
  2) в ответе нет полей, не объявленных в TS (ни обязательных, ни необязательных);
  3) необязательные поля реально встречаются хотя бы в одном ответе теста (иначе тип «врёт» и устарел).
Без генераторов openapi: TS-интерфейсы разбираются небольшим парсером ниже."""
import json
import re
import sqlite3
import sys
import types
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from _synth import synth_market  # noqa: F401
from tradebot.web.api import app

client = TestClient(app)
TYPES_TS = Path(__file__).resolve().parents[1] / "web" / "src" / "types.ts"


# ---------------------------------------------------------------- разбор types.ts
def _strip_comments(src: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", src, flags=re.S))


def _match_brace(src: str, i: int) -> int:
    depth = 0
    for j in range(i, len(src)):
        depth += src[j] == "{"
        depth -= src[j] == "}"
        if depth == 0:
            return j
    raise ValueError("несбалансированные скобки в types.ts")


def _split_members(body: str) -> list[str]:
    """Члены интерфейса верхнего уровня: разделители `;`/перевод строки на нулевой глубине {} [] () <>."""
    out, cur, depth = [], "", 0
    for k, ch in enumerate(body):
        if ch in "{[(<" or (ch == ">" and body[k - 1:k] != "="):
            depth += ch in "{[(<"
            depth -= ch == ">"
        elif ch in "}])":
            depth -= 1
        if ch in ";\n" and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [m.strip() for m in out if m.strip()]


def parse_interfaces(src: str) -> dict[str, dict]:
    """{имя: {"required": set, "optional": set, "extends": [имена]}} для всех `interface` (с export и без)."""
    src = _strip_comments(src)
    res = {}
    for m in re.finditer(r"(?:export )?interface (\w+)(?:\s+extends\s+([\w\s,]+?))?\s*\{", src):
        end = _match_brace(src, m.end() - 1)
        req, opt = set(), set()
        for member in _split_members(src[m.end():end]):
            f = re.match(r"(?:readonly\s+)?(\w+)(\?)?\s*:", member)
            assert f, f"не разобрал член интерфейса {m.group(1)}: {member!r}"
            (opt if f.group(2) else req).add(f.group(1))
        res[m.group(1)] = {"required": req, "optional": opt,
                           "extends": [e.strip() for e in (m.group(2) or "").split(",") if e.strip()]}
    return res


def fields_of(name: str, ifaces: dict) -> tuple[set, set]:
    spec = ifaces[name]
    req, opt = set(spec["required"]), set(spec["optional"])
    for base in spec["extends"]:
        r, o = fields_of(base, ifaces)
        req |= r
        opt |= o
    return req, opt


IFACES = parse_interfaces(TYPES_TS.read_text(encoding="utf-8"))


class Contract:
    def __init__(self):
        self.seen = defaultdict(set)

    def check(self, obj, typ: str, where: str = "", partial: bool = False) -> None:
        req, opt = fields_of(typ, IFACES)
        assert isinstance(obj, dict), f"{where or typ}: ожидался объект, пришло {type(obj).__name__}"
        keys = set(obj)
        self.seen[typ] |= keys
        label = f"{typ}{' @ ' + where if where else ''}"
        if not partial:
            assert not (req - keys), f"{label}: в ответе нет обязательных полей TS: {sorted(req - keys)}"
        assert not (keys - req - opt), f"{label}: поля ответа не объявлены в types.ts: {sorted(keys - req - opt)}"

    def optionals_seen(self, *types_: str, exempt: dict[str, set] | None = None) -> None:
        for t in types_:
            _, opt = fields_of(t, IFACES)
            missing = opt - self.seen[t] - (exempt or {}).get(t, set())
            assert not missing, f"{t}: необязательные поля ни разу не встретились в ответах (устаревший тип?): {sorted(missing)}"


def test_parser_handles_inline_multiline_and_extends():
    ifc = parse_interfaces(TYPES_TS.read_text(encoding="utf-8"))
    assert ifc["FetchEstimate"]["required"] == {"days", "klines_requests", "derivs_requests", "requests", "minutes"}   # в одну строку
    assert {"params", "strips", "legs", "days_effective"} <= ifc["BacktestResult"]["optional"]
    assert ifc["LiveSnapshot"]["required"] == {"available"} and "legs" in ifc["LiveSnapshot"]["optional"]       # многострочные вложенные типы
    assert fields_of("SweepBody", ifc)[0] >= {"symbol", "x"} and "y" in fields_of("SweepBody", ifc)[1]            # extends
    assert "position" not in ifc["BacktestResult"]["optional"] | ifc["BacktestResult"]["required"]


def test_parser_detects_drift():
    c = Contract()
    with pytest.raises(AssertionError, match="не объявлены"):
        c.check({"available": True, "surprise": 1}, "LiveSnapshot")
    with pytest.raises(AssertionError, match="нет обязательных"):
        c.check({}, "LiveSnapshot")


# ---------------------------------------------------------------- вспомогательное
def post(url, body):
    r = client.post(url, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def check_backtest_like(c: Contract, j: dict, where: str) -> None:
    c.check(j, "BacktestResult", where)
    c.check(j["metrics"], "Metrics", where + ".metrics")
    for k in ("entries", "exits"):
        assert j["markers"][k], f"{where}: маркеры {k} пусты — контракт не проверен"
        for mk in j["markers"][k][:5]:
            c.check(mk, "Marker", f"{where}.markers.{k}")
    assert j["trades"], where
    c.check(j["trades"][0], "TradeRow", where + ".trades")
    n = len(j["t"])
    assert all(len(j[k]) == n for k in ("equity", "bh", "drawdown", "price"))


def check_opt_row(c: Contract, row: dict, where: str) -> None:
    c.check(row, "OptRow", where)
    for k in ("is", "oos", "full"):
        c.check(row[k], "Metrics", f"{where}.{k}")


# ---------------------------------------------------------------- тесты контракта
def test_contract_meta(synth_market):
    c = Contract()
    j = client.get("/api/meta").json()
    c.check(j, "Meta")
    for st in j["strategies"]:
        c.check(st, "StrategyMeta", st["name"])
        for p in st["params"].values():
            c.check(p, "ParamSpec", st["name"])
    for leg in j["legs"]:
        c.check(leg, "LegCfg", "legs")
    for est in j["fetch_estimate"].values():
        c.check(est, "FetchEstimate")
    c.optionals_seen("Meta", "StrategyMeta", "ParamSpec", "LegCfg")


def test_contract_backtest_and_portfolio(synth_market):
    c = Contract()
    bt = post("/api/backtest", {"strategy": "EMA cross", "tf": "1h", "days": 60, "params": {"fast": 12, "slow": 50}})
    check_backtest_like(c, bt, "backtest")
    legs = client.get("/api/meta").json()["legs"]
    pf = post("/api/portfolio", {"legs": legs, "days": 90, "leverage": 2})
    check_backtest_like(c, pf, "portfolio")
    for leg in pf["legs"]:
        assert set(leg) == fields_of("LegCfg", IFACES)[0] | {"key"}
    assert {"t", "exposure", "legs"} == set(pf["strips"])
    assert "market" not in bt and "effective" not in bt and "market" not in pf     # перп: эти поля не передаются
    sp = post("/api/backtest", {"strategy": "EMA cross", "tf": "1h", "days": 60, "market": "spot", "leverage": 20})
    check_backtest_like(c, sp, "backtest spot")
    c.check(sp["effective"], "MarketEffective", "backtest spot.effective")
    spf = post("/api/portfolio", {"legs": legs, "days": 90, "market": "spot"})
    check_backtest_like(c, spf, "portfolio spot")
    c.check(spf["effective"], "MarketEffective", "portfolio spot.effective")
    c.optionals_seen("BacktestResult", "Marker", "Metrics", "TradeRow")           # params/days_effective/strips/legs/market/effective, ret, extra metrics


def test_contract_sweep(synth_market):
    c = Contract()
    one = post("/api/sweep", {"strategy": "EMA cross", "tf": "1h", "days": 60, "params": {"slow": 60},
                              "x": {"name": "fast", "min": 5, "max": 20, "n": 4}})
    c.check(one, "SweepResult", "1d")
    assert one["points"] and one["y"] is None
    for p in one["points"]:
        c.check(p, "Metrics", "1d.points", partial=True)          # при нуле сделок точка может быть пустой
    two = post("/api/sweep", {"strategy": "EMA cross", "tf": "1h", "days": 60,
                              "x": {"name": "fast", "min": 5, "max": 20, "n": 3}, "y": {"name": "slow", "min": 30, "max": 100, "n": 3}})
    c.check(two, "SweepResult", "2d")
    assert two["points"] is None and two["y"] and two["grids"]
    spot = post("/api/sweep", {"strategy": "EMA cross", "tf": "1h", "days": 60, "market": "spot", "params": {"slow": 60},
                               "x": {"name": "fast", "min": 5, "max": 20, "n": 3}})
    c.check(spot, "SweepResult", "spot")
    c.check(spot["effective"], "MarketEffective", "spot.effective")
    c.optionals_seen("SweepResult")


def test_contract_optimize(synth_market):
    c = Contract()
    j = post("/api/optimize", {"tf": "1h", "strategy": "EMA cross", "days": 120, "top": 3})
    c.check(j, "OptResult")
    assert j["results"]
    for i, row in enumerate(j["results"]):
        check_opt_row(c, row, f"results[{i}]")
    for sp in j["param_specs"].values():
        c.check(sp, "ParamSpec", "param_specs")
    for r in ("is_range", "oos_range"):
        c.check(j[r], "Range", r)
    spot = post("/api/optimize", {"tf": "1h", "strategy": "EMA cross", "days": 120, "top": 1, "market": "spot"})
    c.check(spot, "OptResult", "spot")
    c.check(spot["effective"], "MarketEffective", "spot.effective")
    c.optionals_seen("OptResult")
    c.optionals_seen("OptRow", exempt={"OptRow": {"curve"}})         # curve есть только у карточки рейтинга (см. тест ниже)


def test_contract_leaderboard(synth_market):
    c = Contract()
    item = post("/api/leaderboard/item", {"tf": "1h", "strategy": "Supertrend", "days": 120})
    c.check(item, "LbItem", "item")
    assert item["result"]
    check_opt_row(c, item["result"], "item.result")
    assert item["result"]["curve"], "карточка должна нести кривую OOS"
    for k in ("is_range", "oos_range"):
        c.check(item[k], "Range", k)
    cached = post("/api/leaderboard/cached", {"tf": "1h", "days": 120})
    c.check(cached, "LbCached")
    assert cached["items"], "кэш должен вернуть только что посчитанную карточку"
    for it in cached["items"]:
        c.check(it, "LbItem", "cached.items")
    c.check(cached["plan"], "LbPlan", "plan")
    for s in cached["plan"]["strategies"]:
        c.check(s, "LbPlanItem", "plan.strategies")
    plan = client.get("/api/leaderboard/plan", params={"tf": "1h"}).json()
    c.check(plan, "LbPlan", "GET plan")
    spot = post("/api/leaderboard/item", {"tf": "1h", "strategy": "Supertrend", "days": 120, "market": "spot"})
    c.check(spot, "LbItem", "item spot")
    c.check(spot["effective"], "MarketEffective", "item spot.effective")
    c.optionals_seen("LbItem", "OptRow", exempt={"LbItem": {"reason"}})      # reason — только у карточки без результата


def test_contract_lb_item_without_result_is_declared(synth_market, monkeypatch):
    """Карточка без результата (мало сделок на IS): реальный ответ эндпоинта, те же правила, плюс `reason`."""
    from tradebot.web import research
    monkeypatch.setattr(research, "_optimize", lambda *a, **k: {"results": [], "n_combos": 1})
    c = Contract()
    item = post("/api/leaderboard/item", {"tf": "1h", "strategy": "Supertrend", "days": 120})
    assert item["result"] is None and item["reason"]
    c.check(item, "LbItem", "no-result")


def test_contract_data_status_and_update(synth_market, monkeypatch):
    from tradebot.market import derivs, klines
    c = Contract()
    st = client.get("/api/data/status", params={"symbol": "BTCUSDT", "tf": "1h"}).json()
    c.check(st, "DataStatus")
    c.check(st["estimate"], "FetchEstimate", "estimate")
    missing = client.get("/api/data/status", params={"symbol": "ETHUSDT", "tf": "1h"}).json()        # данных нет: last/first = null
    c.check(missing, "DataStatus", "no data")

    path = klines._path("BTCUSDT", "1h")
    monkeypatch.setattr(klines, "load", lambda *a, **k: pd.read_parquet(path))      # офлайн: «докачка» читает локальный файл
    monkeypatch.setattr(derivs, "enrich", lambda df, *a, **k: df)
    up = post("/api/data/update", {"symbol": "BTCUSDT", "tf": "1h"})
    c.check(up, "UpdateResult")
    c.check(up["status"], "DataStatus", "update.status")
    c.optionals_seen("UpdateResult", "DataStatus")


def test_contract_live(monkeypatch, tmp_path):
    from tradebot.trading import state
    c = Contract()
    real = state.read_snapshot
    # без БД (значения по умолчанию)
    tmp = tmp_path / "no_such_live.db"
    mod = types.ModuleType("tradebot.trading.state")
    mod.read_snapshot = lambda: real(tmp)
    monkeypatch.setitem(sys.modules, "tradebot.trading.state", mod)
    import tradebot.trading as pkg
    monkeypatch.setattr(pkg, "state", mod, raising=False)
    j = client.get("/api/live").json()
    c.check(j, "LiveSnapshot", "defaults")
    for leg in j["legs"]:
        assert set(leg) == {"key", "strategy", "tf", "params", "long_only", "weight", "enabled", "signal", "updated"}
    # с БД: сделки, события, история капитала
    p = tmp_path / "s.db"
    st = state.Store(p)
    st.set("equity", 1000.0)
    st.log_signals("b", {"SMA cross@4h": 1.0}, 0.5)
    st.add_equity(1000.0)
    st.add_event("info", "x")
    mod.read_snapshot = lambda: real(p)
    j2 = client.get("/api/live").json()
    c.check(j2, "LiveSnapshot", "with db")
    assert j2["events"] and j2["equity_history"]
    assert set(j2["events"][0]) == {"ts", "level", "text"}
    # бот недоступен
    mod.read_snapshot = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    c.check(client.get("/api/live").json(), "LiveSnapshot", "unavailable")
    c.optionals_seen("LiveSnapshot")


def test_contract_stats(synth_market, monkeypatch, tmp_path):
    """GET /api/stats: верхний уровень и вложенные объекты сверяются с types.ts (золотая БД + синтетический рынок, без сети)."""
    import _stats_fixture as fx
    from tradebot.trading import state
    from tradebot.web import stats
    stats._cache.clear()
    monkeypatch.setattr(stats, "_now", lambda: fx.NOW1)
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "bot.db")
    c = Contract()
    na = client.get("/api/stats?range=7d").json()                                  # БД нет
    c.check(na, "StatsResponse", "no_db")
    assert na["available"] is False and na["reason"] == "no_db" and na["message"]
    fx.golden_db(state.DB_PATH)
    for rk in ("7d", "all", "1d", "30d"):
        j = client.get("/api/stats", params={"range": rk}).json()
        c.check(j, "StatsResponse", rk)
        assert j["available"] is True and j["range"] == rk
        c.check(j["window"], "StatsWindow", rk + ".window")
        c.check(j["config"], "StatsConfig", rk + ".config")
        c.check(j["data"], "StatsData", rk + ".data")
        c.check(j["equity"], "StatsEquity", rk + ".equity")
        for d in (j["equity"]["best_day"], j["equity"]["worst_day"]):
            d is None or c.check(d, "StatsDay", rk + ".day")
        c.check(j["kpis"], "StatsKpis", rk + ".kpis")
        for k in ("best_trade", "worst_trade"):
            j["kpis"][k] is None or c.check(j["kpis"][k], "StatsTradeRef", rk + "." + k)
        c.check(j["open_position"], "StatsOpenPosition", rk + ".open_position")
        c.check(j["reconciliation"], "StatsReconciliation", rk + ".reconciliation")
        c.check(j["comparison"], "StatsComparison", rk + ".comparison")
        if j["comparison"]["available"]:
            for grp in ("kpis", "equity"):
                for row in j["comparison"][grp].values():
                    c.check(row, "StatsCmpRow", rk + ".cmp")
        s = j["series"]
        c.check(s, "StatsSeries", rk + ".series")
        c.check(s["equity"], "StatsEquitySeries", rk + ".series.equity")
        c.check(s["drawdown"], "StatsDrawdownSeries", rk + ".series.drawdown")
        c.check(s["histogram"], "StatsHistogram", rk + ".series.histogram")
        for row in s["pnl_buckets"]["rows"][:3]:
            c.check(row, "StatsPnlBucket", rk + ".pnl_buckets")
        for row in s["calendar"][:3]:
            c.check(row, "StatsCalendarCell", rk + ".calendar")
        for row in s["daily_returns"][:3]:
            c.check(row, "StatsDay", rk + ".daily_returns")
        c.check(j["breakdown"], "StatsBreakdown", rk + ".breakdown")
        for row in j["breakdown"]["by_reason"] + j["breakdown"]["by_side"]:
            c.check(row, "StatsGroupRow", rk + ".breakdown")
        c.check(j["trades"], "StatsTrades", rk + ".trades")
        for row in j["trades"]["rows"]:
            c.check(row, "StatsTradeRow", rk + ".trade")
            for f in row["fills"]:
                c.check(f, "StatsFill", rk + ".fill")
        for w in j["warnings"]:
            c.check(w, "StatsWarning", rk + ".warning")
        assert (j["trades"]["rows"] or rk == "1d") and j["definitions"] and all(isinstance(v, str) for v in j["definitions"].values())
    # расхождение позиций: открытая позиция в kv без fills
    db = sqlite3.connect(str(state.DB_PATH))
    db.execute("UPDATE kv SET v=? WHERE k='position'", (json.dumps({"side": "short", "qty": 2.0, "entry": 1.0, "upnl": 0.0, "mark": 1.0}),))
    db.commit()
    db.close()
    stats._cache.clear()
    j = client.get("/api/stats?range=all").json()
    c.check(j, "StatsResponse", "mismatch")
    c.check(j["position_mismatch"], "StatsPositionMismatch", "mismatch")
    c.check(j["position_mismatch"]["replayed"], "StatsPositionSide", "mismatch.replayed")
    c.optionals_seen("StatsResponse", "StatsComparison", "StatsFill", "StatsOpenPosition", "StatsPositionMismatch", exempt={})
    # необязательные поля предупреждений зависят от кода: хотя бы объявленные ключи не расходятся с кодами предупреждений
    assert {w["code"] for w in j["warnings"]} >= {"position_mismatch", "reconstructed_trades"}


def test_metrics_required_keys_hold_in_every_context(synth_market):
    """Metrics: 13 «всегдашних» ключей присутствуют и в бэктесте, и в IS/OOS/full подбора, и в точках развёртки."""
    req, opt = fields_of("Metrics", IFACES)
    assert len(req) == 13 and opt == {"worst_month", "profit_factor", "avg_trade", "sortino", "exposure", "bh_max_drawdown"}
    bt = post("/api/backtest", {"strategy": "EMA cross", "tf": "1h", "days": 60})
    assert req | opt == set(bt["metrics"])
    o = post("/api/optimize", {"tf": "1h", "strategy": "EMA cross", "days": 120, "top": 1})["results"][0]
    for k in ("is", "oos", "full"):
        assert set(o[k]) == req
