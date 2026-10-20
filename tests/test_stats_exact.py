"""Точные итоги биржи на сайте: web/stats.py берёт net/комиссии/фандинг из roundtrips (то же число, что в Telegram), а бот добирает
их для прошлых сделок (executor.backfill_pnl). Только tmp-БД и DryRun/MockTransport: bot.db, .env и сеть не трогаются."""
import asyncio
import sqlite3
import time
from datetime import datetime, timezone

import httpx
import numpy as np
import pytest

import _stats_fixture as fx
from _stats_fixture import equity_points, golden_db, iso, make_db, ts
from _synth import synth_market  # noqa: F401
from tradebot.trading import pnl
from tradebot.trading.exchange import BybitClient
from fakes.exchange import DryRunExchange
from tradebot.trading.executor import Executor
from tradebot.trading.runner import Runner
from tradebot.trading.state import Store
from tradebot.web import stats

BAR = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def run(c):
    return asyncio.run(c)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    stats._cache.clear()
    monkeypatch.setattr(stats, "load_market", lambda: (None, "disabled"))
    monkeypatch.setattr(stats, "_market_sig", lambda: ())
    yield
    stats._cache.clear()


# ---------------------------------------------------------------- помощники для compute_stats
_n = [0]


def fill(t, side, qty, price, oid, reason="бар"):
    _n[0] += 1
    return {"id": _n[0], "ts": t, "side": side, "qty": qty, "price": price, "reason": reason, "order_id": oid}


def trip(day, oid, side="long", qty=0.003, px_in=81550.1, px_out=82300.0, h_in=2, h_out=18):
    a, b = ("buy", "sell") if side == "long" else ("sell", "buy")
    return [fill(f"2026-10-{day}T{h_in:02d}:00:14+00:00", a, qty, px_in, f"in-{oid}"),
            fill(f"2026-10-{day}T{h_out:02d}:00:14+00:00", b, qty, px_out, oid)]


def rt(day, oid, pnl_, fees=0.49, funding=0.0, side="long", qty=0.003, approx=0, h_out=18, dt=0, **kw):
    return {"ts_open": f"2026-10-{day}T02:00:14+00:00", "ts_close": iso(ts(f"2026-10-{day}T{h_out:02d}:00:14+00:00") + dt), "side": side,
            "qty": qty, "pnl": pnl_, "fees": fees, "funding": funding, "approx": approx, "order_id": oid, **kw}


def compute(fills, rts, rk="all", market=None, now="2026-10-19T00:00:00+00:00"):
    eq = equity_points("2026-10-12T00:00:00+00:00", 7 * 24, 160.0, 0.01, every_s=3600)
    t = np.array([ts(a) for a, _ in eq], dtype="int64")
    v = np.array([b for _, b in eq], dtype="float64")
    return stats.compute_stats(rk, ts(now), fill_rows=fills, event_rows=[], eq_t=t, eq_v=v, eq_first_ts=float(t[0]), kv={},
                               market=market, roundtrip_rows=rts)


def rows_by_n(out):
    return {r["n"]: r for r in out["trades"]["rows"]}


# ---------------------------------------------------------------- сайт: приоритет точного над оценкой
def test_exact_replaces_estimate_by_order_id():
    fills = trip("13", "C1") + trip("16", "C2") + trip("18", "C3")
    out = compute(fills, [rt("13", "C1", -1.3376, fees=0.49, funding=-0.05), rt("16", "C2", 1.405), rt("18", "C3", 2.02085919, funding=0.0)])
    r = rows_by_n(out)
    assert [r[i]["exact"] for i in (1, 2, 3)] == [True, True, True]
    assert (r[1]["net"], r[2]["net"], r[3]["net"]) == (-1.3376, 1.405, 2.0209)              # ровно цифры биржи (4 знака)
    assert r[1]["fees"] == 0.49 and r[1]["funding"] == pytest.approx(0.05)                   # funding в строке: − уплачен (+ получен)
    assert r[1]["gross"] == pytest.approx(-1.3376 + 0.49 + 0.05, abs=1e-3)                   # gross = net + комиссии + уплаченный фандинг
    assert r[3]["funding_complete"] is True
    assert out["kpis"]["net_pnl"] == pytest.approx(-1.3376 + 1.405 + 2.0209, abs=1e-3)
    est = compute(fills, [])                                                                  # без roundtrips — оценка как раньше
    assert [x["exact"] for x in est["trades"]["rows"]] == [False] * 3
    assert est["trades"]["rows"][2]["net"] != r[3]["net"]


def test_estimate_rows_and_null_approx_never_count_as_exact():
    fills = trip("13", "C1") + trip("16", "C2") + trip("18", "C3")
    rts = [rt("13", "C1", 999.0, approx=1), rt("16", "C2", 888.0, approx=None), rt("18", "C3", None, approx=0)]
    out = compute(fills, rts)
    assert [x["exact"] for x in out["trades"]["rows"]] == [False] * 3
    assert all(abs(x["net"]) < 50 for x in out["trades"]["rows"])


def test_time_fallback_window_side_and_qty():
    fills = trip("13", "C1")
    def one(**kw):
        return compute(fills, [rt("13", "OTHER", -1.2, **kw)])["trades"]["rows"][0]
    assert one(dt=100)["exact"] is True and one(dt=-100)["exact"] is True        # в пределах 120 с
    assert one(dt=130)["exact"] is False and one(dt=-600)["exact"] is False
    assert one(side="short")["exact"] is False                                    # другая сторона
    assert one(qty=0.006)["exact"] is False and one(qty=0.00302)["exact"] is True  # объём в пределах 1 %
    assert one(dt=100, qty=None)["exact"] is True                                 # объёма в строке нет — не проверяем


def test_order_id_beats_time_and_row_is_used_once():
    fills = trip("13", "C1")
    near = rt("13", "NEAR", 5.0, dt=1)                      # ближе по времени, но другой order_id
    exact_oid = rt("13", "C1", -1.2, dt=90)                 # совпал order_id (дальше по времени)
    out = compute(fills, [near, exact_oid])
    assert out["trades"]["rows"][0]["net"] == -1.2
    two = trip("13", "C1") + trip("13", "C9", h_in=19, h_out=20)       # две сделки подряд, одна строка по времени: только ближайшая
    out = compute(two, [rt("13", "ZZ", 3.0, h_out=18, dt=5)])
    assert [x["exact"] for x in out["trades"]["rows"]] == [True, False]


def test_reconstructed_stop_trade_gets_exact_numbers_by_time():
    fills = [fill("2026-10-18T02:00:14+00:00", "buy", 0.003, 81550.1, "in1")]
    ev = [{"ts": "2026-10-18T12:00:00+00:00", "level": "error", "text": "Позиция исчезла (стоп/ликвидация) · x"}]
    eq = equity_points("2026-10-12T00:00:00+00:00", 7 * 24, 160.0, 0.01, every_s=3600)
    t = np.array([ts(a) for a, _ in eq], dtype="int64")
    v = np.array([b for _, b in eq], dtype="float64")
    out = stats.compute_stats("all", ts("2026-10-19T00:00:00+00:00"), fill_rows=fills, event_rows=ev, eq_t=t, eq_v=v, eq_first_ts=float(t[0]),
                              kv={}, market=None, roundtrip_rows=[{"ts_open": "x", "ts_close": "2026-10-18T12:00:20+00:00", "side": "long", "qty": 0.003,
                                                                   "pnl": -49.37, "fees": 0.45, "funding": 0.0, "approx": 0, "order_id": "STOP"}])
    (row,) = out["trades"]["rows"]
    assert row["reconstructed"] is True and row["exact"] is True and row["net"] == -49.37 and row["fees"] == 0.45


def test_exact_row_without_fees_or_funding_keeps_net_and_balances_gross():
    out = compute(trip("13", "C1"), [rt("13", "C1", -1.2, fees=None, funding=None)])
    r = out["trades"]["rows"][0]
    assert r["exact"] and r["net"] == -1.2 and r["gross"] - r["fees"] - r["funding"] == pytest.approx(r["net"], abs=1e-3)


# ---------------------------------------------------------------- сайт: агрегаты по точным числам
def test_aggregates_use_exact_numbers_consistently():
    fills = trip("13", "C1") + trip("14", "C2", side="short") + trip("16", "C3") + trip("18", "C4")
    rts = [rt("13", "C1", -1.3376, fees=0.49, funding=-0.05), rt("14", "C2", 0.7, side="short", fees=0.5, funding=0.1),
           rt("18", "C4", 2.02085919, fees=0.49, funding=0.01)]                       # C3 — оценка
    out = compute(fills, rts)
    rows = out["trades"]["rows"]
    assert [x["exact"] for x in rows] == [True, True, False, True]
    nets = [x["net"] for x in rows]
    k = out["kpis"]
    assert k["net_pnl"] == pytest.approx(sum(nets), abs=2e-4)
    assert k["profit_sum_usdt"] == pytest.approx(sum(n for n in nets if n > 0), abs=2e-4)
    assert k["loss_sum_usdt"] == pytest.approx(sum(n for n in nets if n < 0), abs=2e-4)
    assert k["profit_sum_usdt"] + k["loss_sum_usdt"] == pytest.approx(k["net_pnl"], abs=2e-4)
    assert k["fees_total"] == pytest.approx(sum(x["fees"] for x in rows), abs=2e-4)
    assert k["funding_total"] == pytest.approx(sum(x["funding"] for x in rows), abs=2e-4)
    assert k["gross_pnl"] == pytest.approx(sum(x["gross"] for x in rows), abs=2e-4)
    assert k["gross_pnl"] - k["fees_total"] - k["funding_total"] == pytest.approx(k["net_pnl"], abs=3e-4)
    assert k["expectancy_usdt"] == pytest.approx(sum(nets) / 4, abs=1e-3)
    assert k["wins"] == sum(1 for n in nets if n > 0) and k["losses"] == sum(1 for n in nets if n < 0)
    s = out["series"]
    assert sum(b["net"] for b in s["pnl_buckets"]["rows"]) == pytest.approx(sum(nets), abs=1e-3)
    assert sum(c["net"] for c in s["calendar"]) == pytest.approx(sum(nets), abs=1e-3)
    assert sum(s["histogram"]["counts"]) == 4
    assert sum(g["net"] for g in out["breakdown"]["by_side"]) == pytest.approx(sum(nets), abs=1e-3)
    assert sum(g["net"] for g in out["breakdown"]["by_reason"]) == pytest.approx(sum(nets), abs=1e-3)
    rec = out["reconciliation"]
    assert rec["bot_pnl_est"] == pytest.approx(sum(nets), abs=1e-3)                  # сверка с кошельком — тоже по точным числам
    assert k["best_trade"]["net"] == max(nets) and k["worst_trade"]["net"] == min(nets)
    # ret_pct и % счёта пересчитаны от точного net
    r = rows[0]
    assert r["ret_pct"] == pytest.approx(r["net"] / r["entry_notional"] * 100, abs=2e-3)
    assert r["ret_account_pct"] == pytest.approx(r["net"] / r["equity_at_entry"] * 100, abs=2e-3)


def test_comparison_window_uses_exact_numbers_too():
    fills = trip("13", "C1") + trip("17", "C2")
    rts = [rt("13", "C1", -9.0), rt("17", "C2", 4.0)]
    out = compute(fills, rts, rk="3d", now="2026-10-19T00:00:00+00:00")
    assert out["kpis"]["net_pnl"] == 4.0                                               # окно 3d: только C2
    full = compute(fills, rts, rk="7d")
    assert full["kpis"]["net_pnl"] == pytest.approx(-5.0)
    cmp7 = full["comparison"]
    assert cmp7["available"] is False or cmp7["kpis"]["net_pnl"]["cur"] == pytest.approx(-5.0)


def test_funding_stale_counts_only_estimated_trades():
    fills = trip("13", "C1") + trip("16", "C2")                        # оба удерживались через расчёт 08:00 UTC, рынка нет -> фандинг неизвестен
    both = compute(fills, [])
    assert next(w for w in both["warnings"] if w["code"] == "funding_stale")["trades"] == 2
    one = compute(fills, [rt("13", "C1", -1.0)])
    assert next(w for w in one["warnings"] if w["code"] == "funding_stale")["trades"] == 1
    none = compute(fills, [rt("13", "C1", -1.0), rt("16", "C2", 1.0)])
    assert "funding_stale" not in [w["code"] for w in none["warnings"]]


def test_definitions_and_row_shape_declare_exact():
    out = compute(trip("13", "C1"), [])
    assert "exact" in out["definitions"] and "closedPnl" in out["definitions"]["net"]
    assert out["trades"]["rows"][0]["exact"] is False and isinstance(out["trades"]["rows"][0]["exact"], bool)


# ---------------------------------------------------------------- сайт: БД (старая без таблицы, золотая)
def test_old_bot_db_without_roundtrips_table_still_works(tmp_path, monkeypatch):
    p = golden_db(tmp_path / "old.db")
    db = sqlite3.connect(str(p))
    db.execute("DROP TABLE roundtrips")
    db.commit()
    db.close()
    out = stats.get_stats("all", p, fx.NOW1)
    assert out["available"] and all(r["exact"] is False for r in out["trades"]["rows"])


def test_golden_db_marks_exact_rows_and_cache_sees_roundtrip_changes(tmp_path):
    p = golden_db(tmp_path / "g.db")
    out = stats.get_stats("all", p, fx.NOW1)
    flags = [r["exact"] for r in out["trades"]["rows"]]
    assert flags == [True, True, False, False, False, False, True, False, False, False]
    rows = rows_by_n(out)
    assert rows[1]["net"] == 1.2975 and rows[2]["net"] == -0.7 and rows[7]["net"] == -3.3 and rows[7]["reconstructed"]
    assert rows[3]["net"] < 50                                      # оценочная строка (approx=1, 999) проигнорирована
    db = sqlite3.connect(str(p))                                    # бот доопределил итог: кэш не должен отдать старое
    db.execute("UPDATE roundtrips SET pnl=-1.5 WHERE order_id='g1'")
    db.commit()
    db.close()
    assert rows_by_n(stats.get_stats("all", p, fx.NOW1))[1]["net"] == -1.5


# ---------------------------------------------------------------- бот: добор итогов с биржи
def mkbot(tmp_path, name="b.db", equity=163.44, mark=81550.1):
    st = Store(tmp_path / name)
    st.set_setting("enabled", True)
    st.set_setting("leverage", 2)
    st.set_setting("stop_pct", 0.2)
    ex = DryRunExchange(equity=equity, mark=mark)
    exe = Executor(ex, st, lambda *a: None)
    exe.poll_delay = 0
    exe.pnl_delays = (0.0, 0.0, 0.0)
    run(exe.setup())
    return st, ex, exe


def old_code_trip(exe, ex, st, t_in=81550.1, t_out=82300.0):
    """Сделка, закрытая СТАРОЙ версией бота: исполнения есть, строки roundtrips нет."""
    ex.mark = t_in
    run(exe.reconcile(1.0, BAR, "бар"))
    ex.mark = t_out
    run(exe.reconcile(0.0, BAR, "бар"))
    st._x("DELETE FROM roundtrips")


def test_backfill_creates_exact_row_for_trip_closed_by_old_code(tmp_path):
    st, ex, exe = mkbot(tmp_path)
    old_code_trip(exe, ex, st)
    assert st.roundtrip_rows() == []
    res = run(exe.backfill_pnl())
    assert res == {"inserted": 1, "updated": 0, "skipped": 0}
    (row,) = st.roundtrip_rows()
    rec = ex.closed[-1]
    assert row["pnl"] == pytest.approx(rec["closed_pnl"]) and row["approx"] == 0 and row["side"] == "long"
    assert row["order_id"] == rec["order_id"] == st.all_trades()[-1]["order_id"]
    assert row["fees"] == pytest.approx(rec["open_fee"] + rec["close_fee"]) and row["entry"] == pytest.approx(81550.1)
    assert row["exit"] == pytest.approx(82300.0) and row["eq_est"] == 1                 # эквити на входе — оценка по кривой (или NULL)
    st.add_equity(160.0, "2000-01-01T00:00:00+00:00")


def test_backfill_is_idempotent_and_never_overwrites_exact(tmp_path):
    st, ex, exe = mkbot(tmp_path)
    old_code_trip(exe, ex, st)
    assert run(exe.backfill_pnl())["inserted"] == 1
    assert run(exe.backfill_pnl()) == {"inserted": 0, "updated": 0, "skipped": 0}     # план пуст: точная строка уже есть
    assert len(st.roundtrip_rows()) == 1
    st._x("UPDATE roundtrips SET pnl=123.0")                                           # «точное» значение в таблице неприкосновенно
    assert run(exe.backfill_pnl()) == {"inserted": 0, "updated": 0, "skipped": 0} and st.roundtrip_rows()[0]["pnl"] == 123.0
    # даже если план попал в хранилище напрямую (гонка с записью бота), точное не перезаписывается
    plan = [{"order_id": st.roundtrip_rows()[0]["order_id"], "ts_open": "a", "ts_close": "b", "side": "long", "qty": 1, "entry": 1, "exit": 1,
             "pnl": -5.0, "fees": 0, "funding": 0, "reason": "r"}]
    assert st.apply_backfill(plan) == {"inserted": 0, "updated": 0, "skipped": 1} and st.roundtrip_rows()[0]["pnl"] == 123.0


def test_backfill_upgrades_estimate_row_but_not_vice_versa(tmp_path):
    st, ex, exe = mkbot(tmp_path)
    ex.closed_pnl_off = True                                                           # при закрытии записи биржи не было -> оценка
    ex.mark = 81550.1
    run(exe.reconcile(1.0, BAR, "бар"))
    ex.mark = 82300.0
    run(exe.reconcile(0.0, BAR, "бар"))
    (row,) = st.roundtrip_rows()
    assert row["approx"] == 1
    res = run(exe.backfill_pnl())                                                      # closed_pnl_list не зависит от флага _off
    assert res["updated"] == 1 and res["inserted"] == 0
    (row,) = st.roundtrip_rows()
    assert row["approx"] == 0 and row["pnl"] == pytest.approx(ex.closed[-1]["closed_pnl"])


def test_backfill_partial_reductions_need_all_records(tmp_path):
    st, ex, exe = mkbot(tmp_path, equity=1000, mark=100000.0)
    run(exe.reconcile(1.0, BAR, "бар"))
    ex.mark = 101000.0
    run(exe.reconcile(0.5, BAR, "бар"))                                                # сокращение
    ex.mark = 102000.0
    run(exe.reconcile(0.0, BAR, "бар"))                                                # закрытие
    st._x("DELETE FROM roundtrips")
    assert len(ex.closed) == 2
    saved = list(ex.closed)
    ex.closed = saved[1:]                                                              # запись о сокращении пропала (старше окна)
    assert run(exe.backfill_pnl()) == {"inserted": 0, "updated": 0, "skipped": 0} and st.roundtrip_rows() == []
    ex.closed = saved
    assert run(exe.backfill_pnl())["inserted"] == 1
    (row,) = st.roundtrip_rows()
    assert row["pnl"] == pytest.approx(sum(r["closed_pnl"] for r in saved))            # итог сделки = сумма всех записей
    assert row["fees"] == pytest.approx(sum(r["open_fee"] + r["close_fee"] for r in saved))


def test_backfill_window_trips_older_than_window_stay_estimates(tmp_path):
    st, ex, exe = mkbot(tmp_path)
    old_code_trip(exe, ex, st)
    for r in ex.closed:
        r["updated_ms"] -= 9 * 86400 * 1000                                            # запись старше 7 суток: биржа её не отдаст
    assert run(exe.backfill_pnl())["inserted"] == 0 and st.roundtrip_rows() == []


def test_backfill_ignores_foreign_records_and_stop_closures(tmp_path):
    st, ex, exe = mkbot(tmp_path)
    old_code_trip(exe, ex, st)
    ex.closed.append({**ex.closed[-1], "order_id": "someone-else", "updated_ms": ex.closed[-1]["updated_ms"] + 1})      # чужой ордер
    run(exe.backfill_pnl())
    assert [r["order_id"] for r in st.roundtrip_rows()] == [st.all_trades()[-1]["order_id"]]
    # закрытие биржей (стоп): нет закрывающего fill -> запись биржи сюда не относится
    ex.mark = 81550.1
    run(exe.reconcile(1.0, BAR, "бар"))
    ex.stop_out()
    assert run(exe.backfill_pnl())["inserted"] == 0


def test_backfill_failure_paths_do_nothing_and_never_raise(tmp_path):
    st, ex, exe = mkbot(tmp_path)
    old_code_trip(exe, ex, st)
    ex.closed_pnl_error = RuntimeError("boom")
    assert run(exe.backfill_pnl()) == {"skipped": "RuntimeError"} and st.roundtrip_rows() == []
    ex.closed_pnl_error = None
    t0 = time.monotonic()
    ex.closed_pnl_hang = True
    assert run(exe.backfill_pnl(timeout=0.2)) == {"skipped": "таймаут"} and time.monotonic() - t0 < 10 and st.roundtrip_rows() == []
    ex.closed_pnl_hang = False
    exe.ex = object()                                                                  # биржа без метода списка
    assert run(exe.backfill_pnl()) == {"skipped": "нет метода"}
    exe.ex = ex

    def bad(*a, **k):
        raise ValueError("plan bug")
    orig = pnl.plan_backfill
    pnl.plan_backfill = bad
    try:
        assert run(exe.backfill_pnl()) == {"skipped": "ошибка"} and st.roundtrip_rows() == []
    finally:
        pnl.plan_backfill = orig
    assert run(exe.backfill_pnl())["inserted"] == 1                                    # после сбоев всё работает


def test_backfill_does_not_send_telegram_noise(tmp_path):
    got = []
    st, ex, exe = mkbot(tmp_path)
    exe.notify = lambda *a: got.append(a)
    old_code_trip(exe, ex, st)
    got.clear()
    run(exe.backfill_pnl())
    assert got == []


def test_runner_starts_backfill_in_background_without_blocking(tmp_path):
    st, ex, exe = mkbot(tmp_path)
    old_code_trip(exe, ex, st)
    r = Runner(st, object(), exe, lambda *a: None)
    seen = {}

    async def fake_run(self=r):
        seen["started"] = True
        await asyncio.sleep(0.2)                                                       # «старт бота» идёт своим чередом
    r._run = fake_run
    run(r.run())
    assert seen["started"] and len(st.roundtrip_rows()) == 1                           # добор отработал параллельно

    async def stuck(**k):
        await asyncio.sleep(3600)
    exe.backfill_pnl = stuck
    t0 = time.monotonic()
    run(r.run())
    assert time.monotonic() - t0 < 30                                                  # зависший добор (спит 3600 с) не держит остановку; порог с запасом на нагрузку

    async def boom(**k):
        raise RuntimeError("x")
    exe.backfill_pnl = boom
    run(r.run())                                                                       # ошибка добора не всплывает


def test_end_to_end_site_shows_backfilled_exact_net(tmp_path):
    st, ex, exe = mkbot(tmp_path)
    old_code_trip(exe, ex, st)
    now = time.time() + 30
    st.add_equity(160.0, iso(now - 3600))
    st.add_equity(165.0, iso(now - 60))
    before = stats.get_stats("1d", st.path, now)["trades"]["rows"][0]
    assert before["exact"] is False
    run(exe.backfill_pnl())
    after = stats.get_stats("1d", st.path, now)
    row = after["trades"]["rows"][0]
    assert row["exact"] is True and row["net"] == pytest.approx(ex.closed[-1]["closed_pnl"], abs=1e-4)
    assert after["kpis"]["net_pnl"] == pytest.approx(row["net"], abs=1e-4)
    # и Telegram-экран «Сделки» показывает те же доллары
    from types import SimpleNamespace as NS
    from tradebot.telegram import texts as tx
    from tradebot.telegram.handlers import Controller
    c = Controller(st, exe, NS(trigger=lambda: None))
    t = tx.strip_html(asyncio.run(c.handle(("tx", "0"))).text)
    assert f"+{ex.closed[-1]['closed_pnl']:.2f} $" in t and "≈" not in t


# ---------------------------------------------------------------- биржа: список closed-pnl и план добора (чистая логика)
def _client(handler):
    c = BybitClient("https://x", "K", "S", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    c._synced = 1e18
    return c


def test_bybit_closed_pnl_list_get_only_with_window_and_limit():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, dict(req.url.params)))
        return httpx.Response(200, json={"retCode": 0, "result": {"list": [
            {"orderId": "B", "updatedTime": "2000", "closedPnl": "2.02085919", "side": "Sell", "avgEntryPrice": "81550.1", "avgExitPrice": "82300",
             "closedSize": "0.003", "qty": "0.003", "openFee": "0.2447", "closeFee": "0.2469"},
            {"orderId": "A", "updatedTime": "1000", "closedPnl": "-1.3376", "side": "Sell"}]}})
    c = _client(handler)
    out = run(c.closed_pnl_list(5000, 100))
    assert [r["order_id"] for r in out] == ["B", "A"] and out[0]["closed_pnl"] == 2.02085919
    assert seen == [("GET", "/v5/position/closed-pnl", {"category": "linear", "symbol": "BTCUSDT", "limit": "100", "startTime": "5000"})]
    assert run(_client(lambda r: httpx.Response(200, json={"retCode": 0, "result": {}})).closed_pnl_list()) == []
    assert len(run(c.closed_pnl_list())) == 2 and all(m == "GET" for m, _, _ in seen)
    bad = _client(lambda r: httpx.Response(200, json={"retCode": 10001, "retMsg": "nope"}))
    from tradebot.trading.exchange import ExchangeError
    with pytest.raises(ExchangeError):
        run(bad.closed_pnl_list())


def test_dryrun_closed_pnl_list_filters_by_time_and_limit():
    ex = DryRunExchange(equity=5000, mark=100_000)

    async def go():
        await ex.market("buy", 0.01, False, "L1")
        await ex.market("sell", 0.004, True, "L2")
        await ex.market("sell", 0.006, True, "L3")
        return await ex.closed_pnl_list(0, 100), await ex.closed_pnl_list(0, 1), await ex.closed_pnl_list(2 ** 60, 100)
    allr, one, none = run(go())
    assert [r["order_id"] for r in allr] == ["o3", "o2"] and len(one) == 1 and none == []


def test_plan_backfill_pure_logic():
    def f(t, side, q, px, oid):
        return {"ts": f"2026-10-18T{t}:00+00:00", "side": side, "qty": q, "price": px, "reason": "бар", "order_id": oid}
    trades = [f("02:00", "buy", 0.003, 81550.1, "i1"), f("18:00", "sell", 0.003, 82300.0, "c1")]
    rec = {"order_id": "c1", "side": "sell", "closed_pnl": 2.02085919, "open_fee": 0.2447, "close_fee": 0.2469, "avg_entry": 81550.1,
           "avg_exit": 82300.0, "closed_size": 0.003, "qty": 0.003}
    (it,) = pnl.plan_backfill([rec], trades, [])
    gross = 0.003 * (82300.0 - 81550.1)
    assert it["pnl"] == 2.02085919 and it["fees"] == pytest.approx(0.4916) and it["order_id"] == "c1" and it["update"] is False
    assert it["funding"] == pytest.approx(2.02085919 - (gross - 0.4916))
    assert pnl.plan_backfill([rec], trades, [{"order_id": "c1", "approx": 0, "pnl": 1.0}]) == []                   # точная строка уже есть
    (up,) = pnl.plan_backfill([rec], trades, [{"order_id": "c1", "approx": 1, "pnl": 1.0}])
    assert up["update"] is True                                                                                    # оценочную заменяем
    assert pnl.plan_backfill([], trades, []) == [] and pnl.plan_backfill([rec], [], []) == []
    assert pnl.plan_backfill([{**rec, "order_id": "other"}], trades, []) == []                                     # чужая запись
    # переворот: закрывающий и открывающий ордера разные; запись относится к закрывающему
    flip = trades + [f("19:00", "sell", 0.003, 82300.0, "i2"), f("20:00", "buy", 0.003, 82000.0, "c2")]
    plan = pnl.plan_backfill([rec, {**rec, "order_id": "c2", "side": "buy", "closed_pnl": 0.5}], flip, [])
    assert [p["order_id"] for p in plan] == ["c1", "c2"] and plan[1]["side"] == "short"
