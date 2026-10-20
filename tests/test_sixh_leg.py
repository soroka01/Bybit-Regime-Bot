"""Третья нога (Regime switch (CHOP)@6h): живые данные 6h, окна, пропуск при сбое, эффективные веса, размер позиции."""
import asyncio
import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

from tradebot.core.portfolio import DEFAULT_LEGS
from tradebot.trading.exchange import Instrument
from fakes.exchange import DryRunExchange
from tradebot.trading.executor import Executor, target_qty
from tradebot.trading.market_feed import (FETCH_DAYS, TF_DELTA, WARMUP_BARS, FeedData, FeedError, MarketFeed, closed_only,
                                          compute_target, expected_last_bar, validate_window, window_for)
from tradebot.trading.runner import MANUAL_RECALC, Runner
from tradebot.trading.state import Store, read_snapshot, resolve_weights

INST = Instrument(0.001, 0.001, 0.1)
BAR = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
K6 = "Regime switch (CHOP)@6h"
NOW6 = pd.Timestamp("2026-03-01 06:00:10", tz="UTC")     # только что закрылась 6h свеча 00:00 (и 4h 00:00, и 1h 05:00)


def run(c):
    return asyncio.run(c)


class Sink:
    def __init__(self):
        self.msgs = []

    def __call__(self, level, text):
        self.msgs.append((level, text))

    def has(self, s):
        return any(s in t for _, t in self.msgs)


def bars_end(tf, last_start, n):
    end = pd.Timestamp(last_start)
    idx = pd.date_range(end=end if end.tzinfo else end.tz_localize("UTC"), periods=n, freq=tf)
    rng = np.random.default_rng(3)
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1.0,
                         "oi": 1000 + np.cumsum(rng.normal(0, 5, n))}, index=idx)


def raw(**over):
    """То, что вернул бы _load_sync: в конце лежит ещё не закрытая свеча (должна отфильтроваться)."""
    out = {"1h": bars_end("1h", "2026-03-01 06:00", 1100), "4h": bars_end("4h", "2026-03-01 04:00", 600),
           "6h": bars_end("6h", "2026-03-01 06:00", 650)}
    out.update(over)
    return out


def feed(data, calls=None):
    f = MarketFeed()

    async def _now():
        return NOW6

    def load(specs):
        if calls is not None:
            calls.append(dict(specs))
        return {tf: data[tf] for tf in specs}
    async def _oi_ready(close_ts):                      # без сети: _wait_oi иначе опрашивает реальную биржу и пишет в storage/market
        return True
    f.now, f._load_sync, f._wait_oi = _now, load, _oi_ready
    return f


# ---------- сетка и окна 6h ----------
def test_6h_bar_grid_and_closed_only():
    e = expected_last_bar
    assert e(pd.Timestamp("2026-03-01 06:00:10", tz="UTC"), "6h") == pd.Timestamp("2026-03-01 00:00", tz="UTC")
    assert e(pd.Timestamp("2026-03-01 05:59:59", tz="UTC"), "6h") == pd.Timestamp("2026-02-28 18:00", tz="UTC")
    assert e(pd.Timestamp("2026-03-01 23:00", tz="UTC"), "6h") == pd.Timestamp("2026-03-01 12:00", tz="UTC")
    d = bars_end("6h", "2026-03-01 06:00", 20)
    assert closed_only(d, "6h", NOW6).index[-1] == pd.Timestamp("2026-03-01 00:00", tz="UTC")
    assert closed_only(d, "6h", pd.Timestamp("2026-03-01 11:59:59", tz="UTC")).index[-1] == pd.Timestamp("2026-03-01 00:00", tz="UTC")
    assert closed_only(d, "6h", pd.Timestamp("2026-03-01 12:00:00", tz="UTC")).index[-1] == pd.Timestamp("2026-03-01 06:00", tz="UTC")


def test_6h_window_validation_and_fetch_depth():
    last = pd.Timestamp("2026-03-01 00:00", tz="UTC")
    d = bars_end("6h", last, 600)
    assert len(validate_window(d, "6h", {"close"}, last, 480)) == 480
    with pytest.raises(FeedError):
        validate_window(d.iloc[-400:], "6h", {"close"}, last, 480)               # мало свечей
    with pytest.raises(FeedError):
        validate_window(d, "6h", {"close"}, last + pd.Timedelta(hours=6), 480)    # не та последняя
    with pytest.raises(FeedError):
        validate_window(d.drop(d.index[-100]), "6h", {"close"}, last, 480)       # дыра
    with pytest.raises(FeedError):
        validate_window(d.assign(close=d["close"].where(np.arange(600) != 590)), "6h", {"close"}, last, 480)   # NaN
    for l in DEFAULT_LEGS:                                                         # докачка покрывает окно с запасом
        assert FETCH_DAYS[l.tf] * 86400 / TF_DELTA[l.tf].total_seconds() >= window_for(l.tf, DEFAULT_LEGS) * 1.1, l.key
        assert window_for(l.tf, DEFAULT_LEGS) >= WARMUP_BARS[l.strategy]
    assert window_for("6h", DEFAULT_LEGS) >= 100 + 28                              # EMA(slow=100) и CHOP(n=28) прогреты
    assert window_for("1h", DEFAULT_LEGS) == window_for("4h", DEFAULT_LEGS) == 300


def test_fetch_loads_only_needed_tfs_with_per_tf_windows():
    calls = []
    fd = run(feed(raw(), calls).fetch(list(DEFAULT_LEGS), tries=1, retry_delay=0))
    assert fd.stale == {} and fd.bar == pd.Timestamp("2026-03-01 05:00", tz="UTC")
    assert {tf: len(d) for tf, d in fd.dfs.items()} == {"1h": 300, "4h": 300, "6h": 480}
    assert fd.dfs["6h"].index[-1] == pd.Timestamp("2026-03-01 00:00", tz="UTC")     # незакрытая 06:00 отброшена
    assert calls == [{"1h": True, "4h": False, "6h": False}]                         # производные нужны только OI-ноге (1h)
    calls.clear()                                                                    # без 6h-ноги 6h вообще не грузится
    fd = run(feed(raw(), calls).fetch([l for l in DEFAULT_LEGS if l.tf != "6h"], tries=1, retry_delay=0))
    assert set(fd.dfs) == {"1h", "4h"} and "6h" not in calls[0]


def test_default_zero_weight_oi_leg_is_not_fetched(tmp_path):
    """OI flush по умолчанию с весом 0: бот (effective_legs) его не считает и не просит производные/OI-ожидание."""
    st = Store(tmp_path / "z.db")
    assert [(l.key, w, en) for l, w, en in st.leg_cfg()] == [("SMA cross@4h", 0.5, True), ("OI flush@1h", 0.0, True), (K6, 0.5, True)]
    legs = st.effective_legs()
    assert [l.key for l in legs] == ["SMA cross@4h", K6] and not st.weight_notes()
    calls = []
    fd = run(feed(raw(), calls).fetch(legs, tries=1, retry_delay=0))      # _wait_oi не вызывается: иначе упало бы без сети
    assert fd.oi_ok and calls == [{"1h": False, "4h": False, "6h": False}]
    st.set_leg("SMA cross@4h", weight=0.4)
    st.set_leg("OI flush@1h", weight=0.1)                                 # включение OI весом > 0 возвращает производные для 1h
    assert any(l.key == "OI flush@1h" for l in st.effective_legs())
    snap = read_snapshot(st.path)
    assert [l["weight"] for l in snap["legs"]] == [0.4, 0.1, 0.5]


def test_bad_6h_is_stale_not_zero_and_1h_failure_still_raises():
    legs = list(DEFAULT_LEGS)
    nan6 = bars_end("6h", "2026-03-01 06:00", 650)
    nan6.iloc[-40, nan6.columns.get_loc("close")] = np.nan
    bad_variants = (bars_end("6h", "2026-03-01 06:00", 650).iloc[:-2],              # закрытая 6h свеча не пришла
                    nan6, RuntimeError("net"))
    for bad in bad_variants:
        fd = run(feed(raw(**{"6h": bad})).fetch(legs, tries=2, retry_delay=0))
        assert set(fd.stale) == {"6h"} and "6h" not in fd.dfs and {"1h", "4h"} <= set(fd.dfs)
        # 6h-нога держит прежний сигнал (-1), остальные считаются заново (веса 1/3: все три ноги участвуют, сумма 1)
        eq = [dataclasses.replace(l, weight=1 / 3) for l in legs]
        t, sg = compute_target(eq, fd.dfs, False, {K6: -1.0}, stale_tfs=set(fd.stale))
        assert sg[K6] == -1.0 and t is not None
        base, _ = compute_target([l for l in eq if l.tf != "6h"], fd.dfs, False, {})
        assert t == pytest.approx(base - 1 / 3)
        # нет прежнего сигнала -> цель НЕ вычисляется (None = держать текущую), а не молча 0
        t0, sg0 = compute_target(legs, fd.dfs, False, {}, stale_tfs=set(fd.stale))
        assert t0 is None and sg0[K6] != sg0[K6]
    with pytest.raises(FeedError):                                                    # битая базовая 1h -> бар пропущен целиком
        run(feed(raw(**{"1h": bars_end("1h", "2026-03-01 06:00", 1100).iloc[:-3]})).fetch(legs, tries=2, retry_delay=0))
    with pytest.raises(FeedError):
        run(feed(raw(**{"1h": RuntimeError("net")})).fetch(legs, tries=1, retry_delay=0))


def test_fetch_retries_until_6h_bar_appears():
    seq = [bars_end("6h", "2026-03-01 06:00", 650).iloc[:-2], bars_end("6h", "2026-03-01 06:00", 650)]
    f = feed(raw())
    n = {"i": 0}

    def load(specs):
        r = raw()
        r["6h"] = seq[min(n["i"], 1)]
        n["i"] += 1
        return {tf: r[tf] for tf in specs}
    f._load_sync = load
    fd = run(f.fetch(list(DEFAULT_LEGS), tries=3, retry_delay=0))
    assert n["i"] == 2 and fd.stale == {} and "6h" in fd.dfs


# ---------- раннер ----------
def make_runner(tmp_path, name, fd_fn):
    st = Store(tmp_path / f"{name}.db", paper=True)
    sink = Sink()
    exe = Executor(DryRunExchange(equity=1000, mark=100_000), st, sink, "testnet", paper=True)
    seen = []

    async def fetch(legs):
        seen.append(legs)
        return fd_fn(legs)

    async def now():
        return pd.Timestamp("2026-03-01 06:00:20", tz="UTC")
    return st, Runner(st, NS(fetch=fetch, now=now), exe, sink), sink, seen


WEIGHTS_3 = {"SMA cross@4h": {"weight": 0.3, "enabled": True}, "OI flush@1h": {"weight": 0.2, "enabled": True},
             K6: {"weight": 0.5, "enabled": True}}


def test_runner_keeps_prev_6h_signal_and_alerts_when_6h_stale(tmp_path):
    stale = {"on": False}

    def fd(legs):
        d = {"1h": bars_end("1h", "2026-03-01 05:00", 300), "4h": bars_end("4h", "2026-03-01 00:00", 300)}
        if not stale["on"]:
            d["6h"] = bars_end("6h", "2026-03-01 00:00", 480)
        return FeedData(d, d["1h"].index[-1], True, {"6h": "6h: пропуски в свечах"} if stale["on"] else {})
    st, r, sink, _ = make_runner(tmp_path, "a", fd)
    st.set("s_legs", WEIGHTS_3)
    run(r.cycle())
    assert not sink.has("некорректны") and K6 in st.latest_signals()
    st.log_signals("2026-03-01T04:00:00+00:00", {K6: -1.0}, 0.0)               # известный прежний сигнал 6h-ноги
    stale["on"] = True
    sink.msgs.clear()
    run(r.cycle())
    assert sink.has("Данные 6h недоступны") and sink.has("держат прежний сигнал")
    assert st.latest_signals()[K6][0] == -1.0                                  # прежний сигнал 6h, не 0
    assert st.get("target_exposure") is not None
    # нет прежнего сигнала (чистая БД) -> цель не меняется, ордеров нет, тревога
    st2, r2, sink2, _ = make_runner(tmp_path, "b", fd)
    st2.set("s_legs", WEIGHTS_3)
    run(r2.cycle())
    assert st2.get("target_exposure") is None and sink2.has("цель не меняю")


def test_late_note_is_not_sent_to_owner_any_more(tmp_path):
    """«Действую с опозданием» — служебная строка без решения для владельца: ни при перезапуске, ни при ручном пересчёте
    в Telegram не уходит (остаётся в журнале INFO)."""
    def fd(legs):
        d = {"1h": bars_end("1h", "2026-03-01 05:00", 300), "4h": bars_end("4h", "2026-03-01 00:00", 300),
             "6h": bars_end("6h", "2026-03-01 00:00", 480)}
        return FeedData({k: v for k, v in d.items() if k == "1h" or k in {l.tf for l in legs}}, d["1h"].index[-1], True)
    st, r, sink, _ = make_runner(tmp_path, "late", fd)

    async def late_now():
        return pd.Timestamp("2026-03-01 06:25:00", tz="UTC")                  # бар закрылся в 06:00 -> 25 мин назад
    r.feed.now = late_now
    run(r.cycle(MANUAL_RECALC))
    assert not sink.has("с опозданием")
    run(r.cycle("старт/перезапуск"))
    assert not sink.has("опозданием") and not any("опозданием" in t for _, t in sink.msgs)


def test_runner_never_uses_total_weight_over_one_and_warns_once(tmp_path):
    """Переопределения пользователя SMA 1.0 / OI 0.0 + новая 6h-нога с дефолтом 0.5: сумма не больше 1, предупреждение раз."""
    def fd(legs):
        d = {"1h": bars_end("1h", "2026-03-01 05:00", 300), "4h": bars_end("4h", "2026-03-01 00:00", 300),
             "6h": bars_end("6h", "2026-03-01 00:00", 480)}
        return FeedData({k: v for k, v in d.items() if k == "1h" or k in {l.tf for l in legs}}, d["1h"].index[-1], True)
    st, r, sink, seen = make_runner(tmp_path, "c", fd)
    st.set("s_legs", {"SMA cross@4h": {"weight": 1.0, "enabled": True}, "OI flush@1h": {"weight": 0.0, "enabled": True}})
    run(r.cycle())
    run(r.cycle())
    assert all(sum(l.weight for l in legs) <= 1.0 + 1e-9 for legs in seen)
    assert [l.key for l in seen[0]] == ["SMA cross@4h"]                       # новая нога получила 0 -> не участвует
    assert sum(1 for _, t in sink.msgs if "Веса стратегий уменьшены" in t) == 1      # предупреждение один раз
    assert abs(st.get("target_exposure")) <= 1.0


def test_runner_caps_even_if_legs_over_one_reach_compute_target(tmp_path):
    """Страховка второго уровня: даже если ноги с суммой > 1 дошли до compute_target, цель масштабируется (|цель| <= 1)."""
    dfs = {"1h": bars_end("1h", "2026-03-01 05:00", 300), "4h": bars_end("4h", "2026-03-01 00:00", 300),
           "6h": bars_end("6h", "2026-03-01 00:00", 480)}
    for i in range(5):
        legs = [type(l)(l.strategy, l.tf, l.params, 1.0, l.long_only) for l in DEFAULT_LEGS]        # сумма 3.0
        t, sg = compute_target(legs, dfs, False, {})
        assert abs(t) <= 1.0 + 1e-12 and t == pytest.approx(sum(sg.values()) / 3)


# ---------- эффективные веса ----------
def test_resolve_weights_cases():
    L = DEFAULT_LEGS
    K = [l.key for l in L]

    def w(ov):
        eff, notes = resolve_weights(L, ov)
        assert sum(wt for wt, en in eff.values() if en) <= 1.0 + 1e-12, (ov, eff)
        return [eff[k][0] for k in K], notes
    ws, notes = w({})                                                     # чистая БД: дефолты 0.5/0/0.5 (сумма 1) без поправок
    assert ws == [0.5, 0.0, 0.5] and not notes
    ws, notes = w({K[1]: {"weight": 0.2, "enabled": True}})               # OI явно 0.2 -> остаток 0.8: дефолты 0.5+0.5 сжаты до 0.4
    assert ws == pytest.approx([0.4, 0.2, 0.4], abs=1e-5) and notes
    ws, notes = w({K[0]: {"weight": 1.0, "enabled": True}, K[1]: {"weight": 0.0, "enabled": True}})   # настройки пользователя
    assert ws == [1.0, 0.0, 0.0] and any("Regime" in n for n in notes)
    ws, notes = w({K[0]: {"weight": 0.3, "enabled": True}, K[1]: {"weight": 0.3, "enabled": True}})   # остаток 0.4 < 0.5
    assert ws == pytest.approx([0.3, 0.3, 0.4]) and notes
    ws, notes = w({K[0]: {"weight": 0.2, "enabled": True}, K[1]: {"weight": 0.2, "enabled": True}})   # остатка хватает
    assert ws == pytest.approx([0.2, 0.2, 0.5]) and not notes
    ws, _ = w({K[0]: {"weight": 1.0, "enabled": True}, K[1]: {"weight": 0.5, "enabled": False}})      # выключенная не занимает долю
    assert ws == [1.0, 0.5, 0.0]
    ws, notes = w({K[0]: {"weight": 1.0, "enabled": True}, K[1]: {"weight": 1.0, "enabled": True}})    # явные > 1 (правка БД)
    assert ws == pytest.approx([0.5, 0.5, 0.0], abs=1e-5) and any("нормирован" in n for n in notes)
    ws, _ = w({K[0]: {"weight": 3.0, "enabled": True}, K[1]: {"weight": 1.0, "enabled": True}, K[2]: {"weight": 4.0, "enabled": True}})
    assert ws == pytest.approx([3 / 8, 1 / 8, 4 / 8], abs=1e-5)
    ws, _ = w({K[0]: {"weight": "abc"}, K[1]: {"weight": None}, K[2]: {"weight": float("inf")}})       # мусор не роняет
    assert ws == [0.0, 0.0, 0.0]
    ws, _ = w({K[0]: {"weight": -1}, K[1]: {"weight": float("nan")}})
    assert ws[:2] == [0.0, 0.0]


def test_store_effective_legs_with_user_overrides(tmp_path):
    st = Store(tmp_path / "w.db")
    st.set("s_legs", {"SMA cross@4h": {"weight": 1.0, "enabled": True}, "OI flush@1h": {"weight": 0.0, "enabled": True}})
    assert [(l.key, l.weight) for l in st.effective_legs()] == [("SMA cross@4h", 1.0)]
    assert [w for _, w, _ in st.leg_cfg()] == [1.0, 0.0, 0.0] and st.weight_notes()
    snap = read_snapshot(st.path)
    assert [l["weight"] for l in snap["legs"]] == [1.0, 0.0, 0.0] and [l["key"] for l in snap["legs"]][2] == K6
    with pytest.raises(ValueError):                                  # поднять новую ногу, не уменьшив SMA, нельзя
        st.set_leg(K6, weight=0.5)
    st.set_leg("SMA cross@4h", weight=0.5)
    st.set_leg(K6, weight=0.5)
    assert [(l.key, l.weight) for l in st.effective_legs()] == [("SMA cross@4h", 0.5), (K6, 0.5)]
    assert not st.weight_notes()


# ---------- размер позиции на диапазоне [-1, 1] ----------
def test_sizing_and_paper_plan_full_exposure_range(tmp_path):
    for expo in (1.0, 2 / 3, 0.5, 1 / 3, 0.0, -1 / 3, -0.5, -2 / 3, -1.0):
        q = target_qty(expo, 1000, 3, 100_000, INST)
        exact = abs(expo) * 1000 * 0.97 * 3 / 100_000                   # без округления, с техническим резервом 3%
        assert exact - 0.001 - 1e-9 < abs(q) <= exact + 1e-9           # округление вниз к шагу 0.001
        assert q == 0 or (q > 0) == (expo > 0)
        assert abs(q) <= 0.0291 + 1e-12
    st = Store(tmp_path / "pr.db", paper=True)
    exe = Executor(DryRunExchange(equity=1000, mark=100_000), st, Sink(), "mainnet", paper=True)
    st.set_setting("leverage", 3)
    for expo, side, qty in ((1.0, "long", 0.029), (-1.0, "short", 0.029), (0.5, "long", 0.014), (-0.5, "short", 0.014)):
        pl = run(exe.paper_plan(expo))                                  # 1000 * 0.97 * 3 / 1e5 = 0.0291 при полной экспозиции
        assert pl["side"] == side and pl["qty"] == pytest.approx(qty)
        assert (pl["stop"] < 100_000) == (side == "long")
    assert run(exe.paper_plan(0.0))["side"] is None
    for expo in (1.0, -1.0, 2 / 3):                                     # сверка на границах диапазона не ломается (DryRun)
        st2 = Store(tmp_path / f"e{expo:.2f}.db")
        st2.set_setting("enabled", True)
        st2.set_setting("leverage", 3)
        ex2 = DryRunExchange(equity=1000, mark=100_000)
        e2 = Executor(ex2, st2, Sink(), "testnet")
        e2.poll_delay = 0
        run(e2.setup())
        assert run(e2.reconcile(expo, BAR)) == "выполнено"
        assert ex2.qty == pytest.approx(expo * 0.0291, abs=0.001) and not ex2.rejected
