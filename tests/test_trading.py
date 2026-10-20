"""Тесты торгового слоя на фейках (без сети и ключей)."""
import asyncio
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from tradebot.core.portfolio import DEFAULT_LEGS
from tradebot.trading import risk
from tradebot.trading.exchange import ExchangeError, Instrument, round_down
from fakes.exchange import DryRunExchange
from tradebot.trading.executor import (SIZING_EQUITY_FRACTION, Executor, make_link_id, needs_trade, plan_orders, stop_price,
                                       target_qty)
from tradebot.trading.market_feed import FeedError, compute_target, expected_last_bar, validate_window
from tradebot.trading.state import Store, read_snapshot

INST = Instrument(0.001, 0.001, 0.1)
BAR = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


class Sink:
    def __init__(self):
        self.msgs = []

    def __call__(self, level, text):
        self.msgs.append((level, text))

    def has(self, s):
        return any(s in t for _, t in self.msgs)


@pytest.fixture
def env(tmp_path):
    st = Store(tmp_path / "bot.db")
    st.set_setting("enabled", True)
    st.set_setting("leverage", 2)
    # баланс 1100 USDT: 0.5 * 1100 * 0.97 (технический резерв 3%) * 2 / 100_000 = 0.01067 -> 0.010 BTC
    ex = DryRunExchange(equity=1100, mark=100_000)
    sink = Sink()
    exe = Executor(ex, st, sink)
    exe.poll_delay = 0
    run(exe.setup())
    return st, ex, exe, sink


# ---------- чистая логика ----------
def test_target_qty_rounding_and_min():
    assert SIZING_EQUITY_FRACTION == 0.97                           # технический резерв 3% закреплён тестом
    assert target_qty(0.5, 1100, 2, 100_000, INST) == 0.01          # 0.5*1100*0.97*2/1e5 = 0.01067 -> вниз
    assert target_qty(0.5, 1000, 2, 100_000, INST) == 0.009         # 0.0097 -> вниз (резерв 3% съедает шаг)
    assert target_qty(-0.5, 1100, 2, 30_000, INST) == -0.035        # 0.03557.. -> вниз, знак от экспозиции
    assert target_qty(0.01, 1000, 1, 100_000, INST) == 0.0          # < minOrderQty
    assert target_qty(1.0, 1000, 2, 100_000, INST, fraction=1.0) == 0.02      # доля — именованный параметр
    assert target_qty(1.0, 0, 2, 100_000, INST) == 0.0 and target_qty(1.0, -5, 2, 100_000, INST) == 0.0   # баланс <= 0
    assert round_down(0.0299999, 0.001) == 0.029
    with pytest.raises(AssertionError):
        target_qty(1, 1000, 4, 100_000, INST)                        # плечо > MAX_LEVERAGE


def test_flip_and_reduce_plans():
    assert plan_orders(0.5, -0.3, INST) == [("sell", 0.5, True), ("sell", 0.3, False)]
    assert plan_orders(-0.5, 0.3, INST) == [("buy", 0.5, True), ("buy", 0.3, False)]
    assert plan_orders(0.5, 0.0, INST) == [("sell", 0.5, True)]
    assert plan_orders(0.5, 0.2, INST) == [("sell", 0.3, True)]
    assert plan_orders(0.2, 0.5, INST) == [("buy", 0.3, False)]
    assert plan_orders(0.0, 0.5, INST) == [("buy", 0.5, False)]
    assert plan_orders(0.5, 0.5, INST) == []
    assert plan_orders(0.010, 0.015, INST) == [("buy", 0.005, False)]     # 0.015 - 0.010 = 0.00499..: не теряем шаг
    assert plan_orders(0.0036, 0.010, INST) == [("buy", 0.006, False)]


def test_deadband():
    # четвёртый аргумент — УДЕРЖИВАЕМЫЙ размер acted_qty, а не пересчитанный
    assert needs_trade(None, 0.5, 0.0, 0.01)                 # первый раз
    assert needs_trade(0.5, 1.0, 0.01, 0.02)                 # экспозиция сменилась
    assert not needs_trade(0.5, 0.5, 0.0095, 0.01)           # ошибка 5%
    assert needs_trade(0.5, 0.5, 0.0085, 0.01)               # ошибка 15%
    assert needs_trade(0.5, 0.5, -0.01, 0.01)                # позиция развёрнута снаружи
    assert needs_trade(0.5, 0.0, 0.01, 0.0) and not needs_trade(0.0, 0.0, 0.0, 0.0)


def test_link_id_len_and_unique():
    ids = {make_link_id(BAR, i, "buy", 0.123456789) for i in range(1000)}
    assert len(ids) == 1000 and all(len(x) <= 36 for x in ids)


def test_kill_check():
    assert risk.check_kill(740, 1000, 1000, 0.25, 0.10) is not None      # просадка 26%
    assert "дневной" in risk.check_kill(890, 900, 1000, 0.25, 0.10)
    assert risk.check_kill(950, 1000, 1000, 0.25, 0.10) is None


# ---------- исполнитель на DryRunExchange ----------
def test_open_stop_and_idempotent(env):
    st, ex, exe, sink = env
    assert run(exe.reconcile(0.5, BAR)) == "выполнено"
    assert ex.qty == pytest.approx(0.01)
    assert ex.stop == pytest.approx(92_000)                   # 8% от входа
    n = len(ex.orders)
    for _ in range(3):
        assert run(exe.reconcile(0.5, BAR)) == "без изменений"
    assert len(ex.orders) == n
    assert st.trades(5)[0]["side"] == "buy" and sink.has("Открыт ЛОНГ")


def test_flip_in_one_way(env):
    st, ex, exe, _ = env
    run(exe.reconcile(0.5, BAR))
    run(exe.reconcile(-0.25, BAR))
    assert ex.qty == pytest.approx(-0.005)
    assert [o["reduce_only"] for o in ex.orders[-2:]] == [True, False]
    assert ex.stop == pytest.approx(108_000)                  # шорт: стоп выше входа


def test_disabled_sends_nothing(env):
    st, ex, exe, _ = env
    st.set_setting("enabled", False)
    assert run(exe.reconcile(1.0, BAR)) == "торговля выключена" and not ex.orders


def test_below_min_qty_is_target_zero(env):
    st, ex, exe, _ = env
    run(exe.reconcile(0.5, BAR))
    assert ex.qty > 0
    ex.cash = 10.0                                             # баланс упал: 0.6*10*0.97*2/1e5 < minQty -> закрыть
    run(exe.reconcile(0.6, BAR))
    assert ex.qty == 0 and st.get("acted_exposure") == 0.6 and st.get("acted_qty") == 0.0 and not st.get("stop_armed")
    n = len(ex.orders)
    assert run(exe.reconcile(0.6, BAR)) == "без изменений" and len(ex.orders) == n


def test_stale_orders_cancelled(env):
    _, ex, exe, _ = env
    run(exe.reconcile(0.5, BAR))                               # появился SL-ордер
    ex.stale_orders = 2
    run(exe.reconcile(0.5, BAR))
    assert ex.cancelled == 2 and all(i.startswith('stale') for i in ex.cancelled_ids)


def test_ambiguous_timeout_after_fill_no_duplicate(env):
    _, ex, exe, _ = env
    ex.fail_next = "after"
    run(exe.reconcile(0.5, BAR))
    assert ex.qty == pytest.approx(0.01) and len(ex.orders) == 1


def test_ambiguous_timeout_before_fill_retries_once(env):
    _, ex, exe, _ = env
    ex.fail_next = "before"
    run(exe.reconcile(0.5, BAR))
    assert ex.qty == pytest.approx(0.01) and len(ex.orders) == 1


def test_stopout_latch_then_release(env):
    st, ex, exe, sink = env
    run(exe.reconcile(0.5, BAR))
    ex.stop_out()
    assert run(exe.reconcile(0.5, BAR)).startswith("защёлка") and st.get("latch") and ex.qty == 0
    assert run(exe.reconcile(0.5, BAR)).startswith("защёлка")      # всё ещё стоим
    assert run(exe.reconcile(1.0, BAR)) == "выполнено" and not st.get("latch") and ex.qty > 0


def test_no_latch_when_target_changed(env):
    st, ex, exe, _ = env
    run(exe.reconcile(0.5, BAR))
    ex.stop_out()
    run(exe.reconcile(0.0, BAR))
    assert not st.get("latch")


def test_stop_not_applied_flattens_and_disables(env):
    st, ex, exe, sink = env
    ex.stop_noop = True
    run(exe.reconcile(0.5, BAR))
    assert ex.qty == 0 and not st.settings()["enabled"] and sink.has("стоп")


def test_kill_switch_flattens_and_blocks(env):
    st, ex, exe, _ = env
    run(exe.reconcile(0.5, BAR))
    run(exe.kill("тест"))
    assert ex.qty == 0 and st.get("killed") and not st.settings()["enabled"]
    st.set_setting("enabled", True)
    assert run(exe.reconcile(0.5, BAR)) == "kill-switch" and ex.qty == 0


def test_panic_and_close_all_latch(env):
    st, ex, exe, _ = env
    st.set("target_exposure", 0.5)
    run(exe.reconcile(0.5, BAR))
    run(exe.close_all())
    assert ex.qty == 0 and st.get("latch")
    run(exe.reconcile(0.5, BAR))
    assert ex.qty == 0
    run(exe.reconcile(1.0, BAR))                                  # сигнал сменился -> защёлка снята
    assert ex.qty > 0
    run(exe.panic())
    assert ex.qty == 0 and not st.settings()["enabled"]


def test_leverage_mismatch_blocks_trading(env):
    st, ex, exe, _ = env
    ex.leverage_override = 5.0
    assert run(exe.setup()) is False
    assert run(exe.reconcile(0.5, BAR)) == "аккаунт не настроен" and not ex.orders


def test_lock_serialises_orders(env):
    _, ex, exe, _ = env

    async def go():
        await asyncio.gather(exe.reconcile(0.5, BAR), exe.close_all(), exe.reconcile(0.5, BAR))
    run(go())
    assert not exe.lock.locked()


# ---------- данные/сигналы ----------
def _df(n, step, nan_at=None):
    idx = pd.date_range("2026-01-01", periods=n, freq=step, tz="UTC")
    rng = np.random.default_rng(0)
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    d = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1.0, "oi": 1000 + np.cumsum(rng.normal(0, 5, n))}, index=idx)
    if nan_at is not None:
        d.iloc[nan_at, d.columns.get_loc("close")] = np.nan
    return d


def test_validate_window_checks():
    d = _df(400, "1h")
    last = d.index[-1]
    assert len(validate_window(d, "1h", {"close"}, last)) == 300
    with pytest.raises(FeedError):
        validate_window(d, "1h", {"close"}, last + pd.Timedelta(hours=1))   # не та последняя свеча
    with pytest.raises(FeedError):
        validate_window(d.drop(d.index[-50]), "1h", {"close"}, last)         # дыра
    with pytest.raises(FeedError):
        validate_window(_df(400, "1h", nan_at=-10), "1h", {"close"}, last)   # NaN
    assert expected_last_bar(pd.Timestamp("2026-01-01 12:00:10", tz="UTC"), "1h") == pd.Timestamp("2026-01-01 11:00", tz="UTC")


def test_compute_target_and_skip_oi():
    dfs = {"1h": _df(300, "1h"), "4h": _df(300, "4h"), "6h": _df(480, "6h")}
    legs = [__import__("dataclasses").replace(l) for l in DEFAULT_LEGS]
    t, sigs = compute_target(legs, dfs, False, {})
    assert t == pytest.approx(sum(sigs[l.key] * l.weight for l in legs) / max(1.0, sum(l.weight for l in legs)))
    t2, s2 = compute_target(legs, dfs, True, {"OI flush@1h": 1.0})
    assert s2["OI flush@1h"] == 1.0 and s2["SMA cross@4h"] == sigs["SMA cross@4h"]
    assert s2["Regime switch (CHOP)@6h"] == sigs["Regime switch (CHOP)@6h"]
    assert compute_target([], dfs, False, {}) == (0.0, {})


# ---------- состояние ----------
def test_snapshot_shape_and_leg_validation(tmp_path):
    p = tmp_path / "s.db"
    assert read_snapshot(p)["legs"][0]["key"] == "SMA cross@4h"      # БД нет — значения по умолчанию
    st = Store(p)
    st.set("equity", 1000.0)
    st.log_signals("b", {"SMA cross@4h": 1.0}, 0.5)
    st.add_equity(1000.0)
    st.add_event("info", "x")
    snap = read_snapshot(p)
    assert set(snap) == {"enabled", "killed", "kill_reason", "latch", "equity", "peak_equity", "position",
                         "target_exposure", "last_bar", "heartbeat", "settings", "legs", "trades", "equity_history",
                         "events"}
    assert set(snap["settings"]) == {"leverage", "max_leverage", "stop_pct", "kill_dd", "kill_daily"}
    assert snap["legs"][0]["signal"] == 1.0 and snap["equity_history"][0][1] == 1000.0
    import json; json.dumps(snap)
    assert [l["key"] for l in snap["legs"]] == [l.key for l in DEFAULT_LEGS] and len(snap["legs"]) == 3
    assert sum(l["weight"] for l in snap["legs"]) <= 1.0                # дефолты на чистой БД делят 100%, а не 150%
    with pytest.raises(ValueError):
        st.set_leg("SMA cross@4h", weight=0.9)                       # 0.9 + 2*0.33 > 1
    with pytest.raises(ValueError):
        st.set_setting("leverage", 4)
    st.set_leg("SMA cross@4h", weight=0.3)
    assert st.effective_legs()[0].weight == 0.3


def test_single_instance_lock(tmp_path):
    from tradebot.app import AlreadyRunning, acquire_lock
    f = acquire_lock(tmp_path / "l.lock")
    with pytest.raises(AlreadyRunning):
        acquire_lock(tmp_path / "l.lock")
    f.close()
    acquire_lock(tmp_path / "l.lock").close()


def test_bybit_client_signing_and_retries():
    import httpx
    from tradebot.trading.exchange import AmbiguousOrder, BybitClient
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if req.url.path == "/v5/order/create":
            raise httpx.ReadTimeout("t")
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"size": "0"}]}})

    async def go():
        c = BybitClient("https://x", "KEY", "SECRET", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        c._synced = 1e18
        c._inst = INST
        assert await c.position() is None                      # GET: 503 -> повтор
        n = len(calls)
        with pytest.raises(AmbiguousOrder):
            await c.market("buy", 0.01, False, "id1")
        assert len(calls) == n + 1                               # POST не ретраится
        h = calls[-1].headers
        assert h["x-bapi-api-key"] == "KEY" and h["x-bapi-recv-window"] == "10000" and len(h["x-bapi-sign"]) == 64
    run(go())


def test_stop_rearmed_when_missing_and_after_failed_close(env):
    st, ex, exe, _ = env
    run(exe.reconcile(0.5, BAR))
    ex.stop = None                                            # биржа потеряла SL
    run(exe.reconcile(0.5, BAR))
    assert ex.stop is not None
    ex.stop = None
    orig = ex.market

    async def boom(*a, **k):
        raise ExchangeError("отказ", 10001)
    ex.market = boom
    assert run(exe.close_all()).startswith("ошибка")
    assert ex.qty > 0 and ex.stop is not None                 # закрыть не вышло -> стоп восстановлен
    ex.market = orig


def test_duplicate_link_means_order_exists(env):
    _, ex, exe, _ = env
    orig = ex.market

    async def dup(side, qty, ro, link):
        await orig(side, qty, ro, link)
        raise ExchangeError("duplicate", 110072)
    ex.market = dup
    run(exe.reconcile(0.5, BAR))
    assert len(ex.orders) == 1 and ex.qty > 0 and exe.store.trades(1)


def test_ambiguous_waits_for_order_lookup(env):
    _, ex, exe, _ = env
    exe.retry_wait = 0
    calls = []
    orig = ex.order_by_link

    async def late(link):
        calls.append(1)
        return None if len(calls) < 3 else await orig(link)
    ex.order_by_link = late
    ex.fail_next = "after"
    run(exe.reconcile(0.5, BAR))
    assert len(ex.orders) == 1


def test_kill_on_whole_wallet_equity():
    # «выделенного капитала» нет: лимиты считаются прямо от equity кошелька (kv peak/day_start уже значения кошелька)
    assert not hasattr(risk, "bot_equity") and not hasattr(risk, "kill_reason")
    assert risk.check_kill(9700, 10000, 10000, 0.25, 0.10) is None            # -3% кошелька: не повод
    assert "дневной" in risk.check_kill(8900, 10000, 10000, 0.25, 0.10)       # -11% за сутки
    assert "просадка" in risk.check_kill(7400, 10000, 7400, 0.25, 0.10)       # -26% от пика (день уже обнулён)
    assert risk.check_kill(7600, 10000, 7600, 0.25, 0.10) is None
    assert risk.check_kill(900, None, None, 0.25, 0.10) is None               # нет базы — не срабатывает


def test_stop_risk_helpers():
    assert risk.stop_risk(0.20, 2, 1.0) == pytest.approx(0.40)
    assert risk.stop_risk(0.08, 1, 0.5) == pytest.approx(0.04)
    loss, lim, name = risk.stop_risk_breach(0.20, 2, 1.0, 0.25, 0.10)
    assert loss == pytest.approx(0.40) and lim == 0.10 and "сутки" in name      # меньший лимит — дневной
    assert risk.stop_risk_breach(0.20, 2, 1.0, 0.25, 0.50)[1] == 0.25            # меньший — просадка
    assert risk.stop_risk_breach(0.05, 2, 1.0, 0.25, 0.10)[0] == pytest.approx(0.10)    # ровно лимит -> предупреждаем
    assert risk.stop_risk_breach(0.04, 2, 1.0, 0.25, 0.10) is None
    assert risk.stop_risk_breach(0.20, 2, 0.0, 0.25, 0.10) is None                # весов нет — нечего терять


def _runner(st, exe):
    from types import SimpleNamespace as NS
    from tradebot.trading.runner import Runner
    return Runner(st, NS(), exe, exe.notify)


def test_tick_kill_independent_of_position_read_and_rearm(env):
    st, ex, exe, _ = env
    run(exe.reconcile(0.5, BAR))
    r = _runner(st, exe)
    ex.stop = None
    run(r.tick())                                             # позиция без стопа -> перевзвод
    assert ex.stop is not None
    # убыток > 10% счёта за день и сбой чтения позиции: kill всё равно срабатывает
    ex.cash -= 150
    orig = ex.position
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExchangeError("net")
        return await orig()
    ex.position = flaky
    st.set("day_start_equity", 1100.0)
    st.set("baseline_equity", 1100.0)
    st.set("peak_equity", 1100.0)
    st.set("day", __import__("datetime").datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    st.set("position", {"x": 1})
    run(r.tick())
    assert st.get("killed")


def test_kill_runs_when_disabled_but_position_open(env):
    st, ex, exe, _ = env
    run(exe.reconcile(0.5, BAR))
    st.set_setting("enabled", False)
    st.set("position", {"x": 1})
    ex.cash -= 300
    st.set("peak_equity", 1100.0)
    st.set("baseline_equity", 1100.0)
    run(_runner(st, exe).tick())
    assert st.get("killed") and ex.qty == 0


def test_safe_cycle_swallows_any_exception(env):
    from types import SimpleNamespace as NS
    st, ex, exe, sink = env

    async def bad(legs):
        raise KeyError("x")
    from tradebot.trading.runner import Runner
    r = Runner(st, NS(fetch=bad), exe, sink)
    run(r._safe_cycle("t"))
    assert sink.has("Сбой пересчёта сигналов")


def test_shutdown_data_only_claims_verified_stop(env):
    from tradebot.app import shutdown_data
    from tradebot.telegram import messages as M
    _, ex, exe, _ = env
    note = lambda: M.plain(run(shutdown_data(ex)))  # noqa: E731
    assert "нет" in note()
    run(exe.reconcile(0.5, BAR))
    assert "под биржевым стопом" in note()
    ex.stop = None
    assert "БЕЗ" in note()


def test_uta_isolated_via_account_info():
    import httpx
    from tradebot.trading.exchange import BybitClient
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.path)
        res = {"/v5/account/info": {"unifiedMarginStatus": 5, "marginMode": "ISOLATED_MARGIN"},
               "/v5/position/list": {"list": [{"leverage": "2", "tradeMode": 0, "positionIdx": 0, "size": "0"}]}}.get(req.url.path, {})
        return httpx.Response(200, json={"retCode": 0, "result": res})

    async def go():
        c = BybitClient("https://x", "K", "S", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        c._synced = 1e18
        rb = await c.setup_account(2)
        assert rb == {"leverage": 2.0, "isolated": True, "one_way": True}
        assert "/v5/account/set-margin-mode" in seen and "/v5/position/switch-isolated" not in seen
    run(go())


def _clock_client(bad_first: int, skew_s: float = 5.0):
    """BybitClient, у которого первые bad_first подписанных запросов получают 10002 (часы разошлись), далее — успех."""
    import time
    import httpx
    from tradebot.trading.exchange import BybitClient
    reqs, state = [], {"bad": bad_first}

    def handler(req: httpx.Request):
        reqs.append((req.method, req.url.path))
        if req.url.path == "/v5/market/time":
            return httpx.Response(200, json={"retCode": 0, "result": {"timeNano": str(int((time.time() + skew_s) * 1e9))}})
        if state["bad"] > 0:
            state["bad"] -= 1
            return httpx.Response(200, json={"retCode": 10002, "retMsg": "invalid request, please check your server timestamp"})
        return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"totalEquity": "155.5"}]}})
    c = BybitClient("https://x", "K", "S", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    c._synced = 1e18                  # плановая синхронизация не нужна: проверяем реакцию на 10002
    return c, reqs


def test_timestamp_error_10002_resyncs_clock_and_retries_once():
    c, reqs = _clock_client(bad_first=1)
    assert run(c.equity()) == 155.5
    assert [p for _, p in reqs] == ["/v5/account/wallet-balance", "/v5/market/time", "/v5/account/wallet-balance"]
    assert 4000 < c.offset_ms < 6000                 # смещение пересчитано по ответу биржи


def test_timestamp_error_10002_persisting_is_not_retried_forever():
    from tradebot.trading.exchange import ExchangeError
    c, reqs = _clock_client(bad_first=99)
    with pytest.raises(ExchangeError, match="10002"):
        run(c._ok("GET", "/v5/position/list", {"category": "linear"}))
    assert [p for _, p in reqs].count("/v5/position/list") == 2 and [p for _, p in reqs].count("/v5/market/time") == 1


def test_timestamp_error_10002_on_post_resyncs_and_retries_once():
    c, reqs = _clock_client(bad_first=1)
    run(c._ok("POST", "/v5/position/set-leverage", {"symbol": "BTCUSDT"}))
    assert reqs == [("POST", "/v5/position/set-leverage"), ("GET", "/v5/market/time"), ("POST", "/v5/position/set-leverage")]


def test_atomic_parquet(tmp_path):
    from tradebot.market.klines import _atomic_parquet
    p = tmp_path / "a.parquet"
    _atomic_parquet(pd.DataFrame({"a": [1, 2]}), p)
    assert list(pd.read_parquet(p)["a"]) == [1, 2] and [f.name for f in tmp_path.iterdir()] == ["a.parquet"]


# ---------- префлайт прав API-ключа ----------
def _key_env(tmp_path, query_api, fail_query=False):
    """Executor на BybitClient с MockTransport; возвращает (executor, sink, store, список запросов (метод, путь))."""
    import httpx
    from tradebot.trading.exchange import BybitClient
    reqs = []

    def handler(req: httpx.Request):
        reqs.append((req.method, req.url.path))
        if req.url.path == "/v5/user/query-api":
            if fail_query:
                return httpx.Response(200, json={"retCode": 10016, "retMsg": "server error", "result": {}})
            return httpx.Response(200, json={"retCode": 0, "result": query_api})
        res = {"/v5/account/info": {"unifiedMarginStatus": 5, "marginMode": "ISOLATED_MARGIN"},
               "/v5/position/list": {"list": [{"leverage": "2", "tradeMode": 0, "positionIdx": 0, "size": "0"}]}}.get(req.url.path, {})
        return httpx.Response(200, json={"retCode": 0, "result": res})
    c = BybitClient("https://x", "K", "S", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    c._synced = 1e18
    st = Store(tmp_path / "k.db")
    st.set_setting("leverage", 2)
    sink = Sink()
    return Executor(c, st, sink), sink, reqs


def test_key_info_parsing():
    import httpx
    from tradebot.trading.exchange import BybitClient
    cases = [({"readOnly": 1, "permissions": {"Wallet": []}, "ips": ["*"]}, {"read_only": True, "withdraw": False, "ips": ["*"]}),
             ({"readOnly": 0, "permissions": {"Wallet": ["AccountTransfer", "WITHDRAW"]}, "ips": ["1.2.3.4"]},
              {"read_only": False, "withdraw": True, "ips": ["1.2.3.4"]}),
             ({"readOnly": 0, "permissions": {"ContractTrade": ["Order"]}}, {"read_only": False, "withdraw": False, "ips": []}),
             ({"readOnly": 0, "permissions": None, "ips": []}, {"read_only": False, "withdraw": False, "ips": []})]
    for res, want in cases:
        c = BybitClient("https://x", "K", "S", httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req, res=res: httpx.Response(200, json={"retCode": 0, "result": res}))))
        c._synced = 1e18
        assert run(c.key_info()) == want


def test_read_account_only_gets_and_reads_exchange_settings_as_is():
    """read_account (его использует и `--check`) читает режимы аккаунта одними GET-запросами и ничего не переключает."""
    import httpx
    from tradebot.trading.exchange import BybitClient
    reqs = []

    def handler(req: httpx.Request):
        reqs.append((req.method, req.url.path))
        res = {"/v5/account/info": {"unifiedMarginStatus": 5, "marginMode": "REGULAR_MARGIN"},
               "/v5/position/list": {"list": [{"leverage": "5", "tradeMode": 0, "positionIdx": 0, "size": "0"}]}}.get(req.url.path, {})
        return httpx.Response(200, json={"retCode": 0, "result": res})
    c = BybitClient("https://x", "K", "S", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    c._synced = 1e18
    assert run(c.read_account()) == {"leverage": 5.0, "isolated": False, "one_way": True}      # прочитано как есть, не изменено
    assert reqs and all(m == "GET" for m, _ in reqs)


def test_readonly_key_blocks_setup_without_any_post(tmp_path):
    exe, sink, reqs = _key_env(tmp_path, {"readOnly": 1, "permissions": {"Wallet": []}, "ips": ["1.2.3.4"]})
    assert run(exe.setup()) is False and exe.trade_ok is False
    assert sink.has("только на чтение") and sink.has("Read-Write") and sink.has("перезапустите бота")
    assert reqs == [("GET", "/v5/user/query-api")]                      # ни set-leverage, ни setup_account вообще, ни POST
    assert not any(m == "POST" for m, _ in reqs)


def test_withdraw_key_blocks_setup(tmp_path):
    exe, sink, reqs = _key_env(tmp_path, {"readOnly": 0, "permissions": {"Wallet": ["AccountTransfer", "Withdraw"]}, "ips": ["1.2.3.4"]})
    assert run(exe.setup()) is False and exe.trade_ok is False
    assert any(l == "error" and "Withdraw" in t for l, t in sink.msgs)
    assert reqs == [("GET", "/v5/user/query-api")]


def test_write_key_with_ip_proceeds_to_setup_account(tmp_path):
    exe, sink, reqs = _key_env(tmp_path, {"readOnly": 0, "permissions": {"Wallet": ["AccountTransfer"]}, "ips": ["1.2.3.4"]})
    assert run(exe.setup()) is True and exe.trade_ok is True
    assert reqs[0] == ("GET", "/v5/user/query-api") and ("POST", "/v5/position/set-leverage") in reqs
    assert not sink.msgs                                                # ни предупреждений, ни ошибок


def test_key_without_ip_restriction_proceeds_silently(tmp_path):
    """Привязка к IP не требуется (VPN владельца): ни предупреждения, ни блокировки."""
    for ips in (["*"], []):
        exe, sink, reqs = _key_env(tmp_path, {"readOnly": 0, "permissions": {"Wallet": []}, "ips": ips})
        assert run(exe.setup()) is True and exe.trade_ok is True
        assert ("POST", "/v5/position/set-leverage") in reqs
        assert not sink.msgs


def test_key_info_failure_warns_and_continues(tmp_path):
    exe, sink, reqs = _key_env(tmp_path, {}, fail_query=True)
    assert run(exe.setup()) is True and exe.trade_ok is True            # как раньше: проверка не удалась — не блокируем
    assert any(l == "warn" and "Не удалось проверить права API-ключа" in t for l, t in sink.msgs)
    assert ("POST", "/v5/position/set-leverage") in reqs


def test_dryrun_key_info_is_permissive(env):
    _, ex, exe, sink = env
    ki = run(ex.key_info())
    assert ki["read_only"] is False and ki["withdraw"] is False
    assert not sink.msgs and exe.trade_ok                                # фикстура env: setup() прошёл без предупреждений
