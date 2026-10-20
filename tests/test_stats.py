"""Статистика реальной торговли бота (tradebot.web.stats + GET /api/stats): реплей сделок, комиссии/фандинг, equity-метрики,
окна, пустые состояния, кэш. Только временные БД — storage/state/bot.db не читается и не пишется."""
import json
import math
import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

import _stats_fixture as fx
from _stats_fixture import add_trade, equity_points, golden_db, iso, make_db, ts
from _synth import synth_market  # noqa: F401
from tradebot.web import api, stats
from tradebot.web.stats import Market

client = TestClient(api.app)
REAL_LOAD_MARKET = stats.load_market      # автофикстура подменяет атрибут модуля; настоящая функция — здесь


@pytest.fixture(autouse=True)
def _stats_env(monkeypatch, tmp_path):
    """Кэш статистики чистый; реальные свечи/parquet не читаются (тесты сами подставляют рынок); БД — во tmp."""
    from tradebot.trading import state
    stats._cache.clear()
    monkeypatch.setattr(stats, "load_market", lambda: (None, "disabled"))
    monkeypatch.setattr(stats, "_market_sig", lambda: ())
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "bot.db")
    monkeypatch.setattr(stats, "_now", lambda: fx.NOW1)
    yield
    stats._cache.clear()


# ---------------------------------------------------------------- помощники
def F(t, side, qty, price, reason="бар", i=[0]):
    i[0] += 1
    return {"id": i[0], "ts": ts(t), "side": side, "qty": qty, "price": price, "reason": reason}


def rep(fills, stops=(), market=None, **kw):
    return stats.replay(fills, [ts(s) for s in stops], market=market, **kw)


def mk_market(start, hours, price=lambda i: 100.0, rates=None, funding_until=None, spread=0.001):
    """Часовые свечи: open_i = price(i), close_i = price(i+1). rates: {iso-время расчёта: ставка}."""
    t0 = ts(start)
    bt = np.array([t0 + 3600 * i for i in range(hours)], dtype="int64")
    o = np.array([price(i) for i in range(hours)], dtype="float64")
    c = np.array([price(i + 1) for i in range(hours)], dtype="float64")
    h, lo = np.maximum(o, c) * (1 + spread), np.minimum(o, c) * (1 - spread)
    fund = np.zeros(hours)
    for k, r in (rates or {}).items():
        fund[int((ts(k) - t0) // 3600)] = r
    return Market(bt, o, h, lo, c, fund, ts(funding_until) if funding_until else None)


def run_compute(fills_rows, *, equity, now, rk="30d", events=(), kv=None, market=None):
    t = np.array([ts(a) for a, _ in equity], dtype="int64")
    v = np.array([b for _, b in equity], dtype="float64")
    return stats.compute_stats(rk, now, fill_rows=fills_rows, event_rows=[{"ts": a, "level": "x", "text": b} for a, b in events],
                               eq_t=t, eq_v=v, eq_first_ts=float(t[0]) if len(t) else None, kv=kv or {}, market=market)


# ---------------------------------------------------------------- реплей: сделки
def test_simple_long_and_short_round_trips():
    r = rep([F("2026-01-01T10:00:00+00:00", "buy", 1, 100), F("2026-01-01T12:00:00+00:00", "sell", 1, 110, "закрытие вручную"),
             F("2026-01-02T10:00:00+00:00", "sell", 2, 100), F("2026-01-02T11:00:00+00:00", "buy", 2, 90)], leverage=2.0)
    a, b = r["trades"]
    assert (a["side"], a["gross"], a["reason"], a["hold_s"]) == ("long", 10.0, "закрытие вручную", 7200)
    assert a["fees"] == pytest.approx(0.1 + 0.11)                           # TAKER_FEE_EST * notional обоих fills
    assert a["net"] == pytest.approx(10 - 0.21) and a["ret_pct"] == pytest.approx(9.79)
    assert a["ret_margin_pct"] == pytest.approx(19.58)                      # на маржу при плече 2
    assert (b["side"], b["gross"]) == ("short", pytest.approx(20.0))
    assert b["entry_price"] == 100 and b["exit_price"] == 90 and b["qty"] == 2
    assert r["pos"] == 0 and r["open"] is None


def test_partial_reduce_realises_pnl_on_reduced_qty():
    r = rep([F("2026-01-01T10:00:00+00:00", "buy", 1, 100), F("2026-01-01T11:00:00+00:00", "sell", 0.4, 110),
             F("2026-01-01T12:00:00+00:00", "sell", 0.6, 120, "стоп")])
    (t,) = r["trades"]
    assert t["gross"] == pytest.approx(0.4 * 10 + 0.6 * 20) and len(t["fills"]) == 3
    assert t["exit_price"] == pytest.approx((0.4 * 110 + 0.6 * 120))        # VWAP закрытий
    assert t["reason"] == "стоп"                                            # причина — закрывающего fill


def test_add_to_position_uses_average_cost():
    r = rep([F("2026-01-01T10:00:00+00:00", "buy", 1, 100), F("2026-01-01T11:00:00+00:00", "buy", 1, 110),
             F("2026-01-01T12:00:00+00:00", "sell", 2, 120)])
    (t,) = r["trades"]
    assert t["entry_price"] == pytest.approx(105) and t["gross"] == pytest.approx(30) and t["qty"] == 2
    assert t["entry_notional"] == pytest.approx(210) and t["ret_pct"] == pytest.approx((30 - 0.21 - 0.24) / 210 * 100)


def test_flip_is_split_into_close_and_open_at_same_price_and_time():
    r = rep([F("2026-01-01T10:00:00+00:00", "buy", 1, 100), F("2026-01-01T12:00:00+00:00", "sell", 3, 110),
             F("2026-01-01T14:00:00+00:00", "buy", 2, 100)])
    a, b = r["trades"]
    assert a["gross"] == pytest.approx(10) and a["exit_ts"] == b["entry_ts"] == ts("2026-01-01T12:00:00+00:00")
    assert a["fills"][-1]["qty"] == 1 and a["fills"][-1]["split"] and b["fills"][0]["qty"] == 2 and b["fills"][0]["split"]
    assert b["side"] == "short" and b["entry_price"] == 110 and b["gross"] == pytest.approx(20)
    assert a["fees"] + b["fees"] == pytest.approx((100 + 110 + 220 + 200) * 0.001)    # комиссия делится по долям fill
    assert r["pos"] == 0


def test_invalid_fills_are_dropped():
    fills, bad = stats.clean_fills([{"id": 1, "ts": "2026-01-01T10:00:00+00:00", "side": "buy", "qty": 1, "price": 0, "reason": ""},
                                    {"id": 2, "ts": "2026-01-01T10:00:00+00:00", "side": "buy", "qty": -1, "price": 5, "reason": ""},
                                    {"id": 3, "ts": "2026-01-01T10:00:00+00:00", "side": "hold", "qty": 1, "price": 5, "reason": ""},
                                    {"id": 4, "ts": "2026-01-01T10:00:00+00:00", "side": "buy", "qty": 1, "price": 5, "reason": ""}])
    assert [f["id"] for f in fills] == [4] and bad == 3


# ---------------------------------------------------------------- синтетические закрытия по стопу
STOP_TEXT = "[MAINNET] Позиция исчезла (стоп/ликвидация). Защёлка: не вхожу заново, пока целевая экспозиция не изменится."


def test_stopout_event_closes_at_stop_level_when_candle_touched():
    # лонг 100, стоп 5 % -> 95; свеча 13:00 уходит до 94 -> выход по стоп-уровню
    mk = mk_market("2026-01-01T09:00:00+00:00", 10, price=lambda i: 100.0 if i < 4 else 94.5)
    r = rep([F("2026-01-01T10:00:14+00:00", "buy", 1, 100)], ["2026-01-01T14:30:00+00:00"], mk, stop_pct=0.05)
    (t,) = r["trades"]
    assert t["reconstructed"] and t["reason"] == stats.STOP_REASON and t["exit_price"] == pytest.approx(95.0)
    assert t["fills"][-1]["synthetic"] and t["fills"][-1]["basis"] == "stop_level" and t["fills"][-1]["id"] is None
    assert t["gross"] == pytest.approx(-5.0) and t["exit_ts"] == ts("2026-01-01T14:30:00+00:00")
    assert r["pos"] == 0 and r["open"] is None


def test_stopout_short_uses_high_and_falls_back_to_candle_close_then_entry():
    # шорт 100, стоп 5 % -> 105; цены не доходят -> close свечи; без свечей -> цена входа
    mk = mk_market("2026-01-01T09:00:00+00:00", 10, price=lambda i: 101.0)
    fills = [F("2026-01-01T10:00:14+00:00", "sell", 1, 100)]
    t = rep(fills, ["2026-01-01T14:30:00+00:00"], mk, stop_pct=0.05)["trades"][0]
    assert t["fills"][-1]["basis"] == "candle_open" and t["exit_price"] == pytest.approx(101.0)
    t = rep(fills, ["2026-01-01T19:30:00+00:00"], mk, stop_pct=0.05)["trades"][0]
    assert t["fills"][-1]["basis"] == "candle_close" and t["exit_price"] == pytest.approx(101.0)   # свечи события нет, есть закрытая
    t = rep(fills, ["2026-01-01T23:00:00+00:00"], mk, stop_pct=0.05)["trades"][0]
    assert t["fills"][-1]["basis"] == "entry" and t["exit_price"] == 100            # свечи старше допуска
    t = rep(fills, ["2026-01-01T14:30:00+00:00"], None, stop_pct=0.05)["trades"][0]
    assert t["fills"][-1]["basis"] == "entry" and t["gross"] == 0
    mk2 = mk_market("2026-01-01T09:00:00+00:00", 10, price=lambda i: 106.0 if i >= 3 else 101.0)
    t = rep(fills, ["2026-01-01T14:30:00+00:00"], mk2, stop_pct=0.05)["trades"][0]
    assert t["fills"][-1]["basis"] == "stop_level" and t["exit_price"] == pytest.approx(105.0)


def test_stopout_event_without_position_is_ignored():
    r = rep([F("2026-01-01T10:00:00+00:00", "buy", 1, 100), F("2026-01-01T11:00:00+00:00", "sell", 1, 101)],
            ["2026-01-01T09:00:00+00:00", "2026-01-01T12:00:00+00:00"])
    assert len(r["trades"]) == 1 and not r["trades"][0]["reconstructed"]


def test_compute_marks_stop_events_and_warns():
    rows = [{"id": 1, "ts": "2026-01-01T10:00:00+00:00", "side": "buy", "qty": 1, "price": 100, "reason": "бар"}]
    now = ts("2026-01-02T00:00:00+00:00")
    out = run_compute(rows, equity=equity_points("2026-01-01T09:00:00+00:00", 20, 1000, every_s=3600), now=now, rk="1d",
                      events=[("2026-01-01T12:00:00+00:00", STOP_TEXT)], kv={"s_stop_pct": 0.05})
    assert out["trades"]["rows"][0]["reconstructed"] is True
    assert "reconstructed_trades" in [w["code"] for w in out["warnings"]]
    assert out["position_mismatch"] is None                                   # kv без позиции == реплей плоский


# ---------------------------------------------------------------- расхождение журнала и биржи
def test_position_mismatch_is_exposed_not_hidden():
    rows = [{"id": 1, "ts": "2026-01-01T10:00:00+00:00", "side": "buy", "qty": 1, "price": 100, "reason": "бар"},
            {"id": 2, "ts": "2026-01-01T11:00:00+00:00", "side": "sell", "qty": 1, "price": 101, "reason": "бар"}]
    kv = {"position": {"side": "long", "qty": 0.5, "entry": 100.0, "upnl": 2.0, "mark": 104.0}}      # «призрак»: журнал плоский
    out = run_compute(rows, equity=equity_points("2026-01-01T09:00:00+00:00", 20, 1000), now=ts("2026-01-02T00:00:00+00:00"), rk="1d", kv=kv)
    assert out["position_mismatch"] == {"replayed": None, "actual": {"side": "long", "qty": 0.5}}
    assert out["open_position"]["side"] == "long" and out["open_position"]["opened_ts"] is None     # показано по данным бота
    assert out["reconciliation"]["reason"] == "position_mismatch" and out["reconciliation"]["unexplained"] is None
    assert "position_mismatch" in [w["code"] for w in out["warnings"]]
    # обратный случай: журнал держит позицию, бот — нет
    rows2 = rows[:1]
    out = run_compute(rows2, equity=equity_points("2026-01-01T09:00:00+00:00", 20, 1000), now=ts("2026-01-02T00:00:00+00:00"), rk="1d", kv={})
    assert out["position_mismatch"] == {"replayed": {"side": "long", "qty": 1.0}, "actual": None}
    assert out["open_position"] is None


# ---------------------------------------------------------------- комиссии и фандинг: цель валидации (closed-pnl Bybit)
REAL = [  # первые сделки живого бота (mainnet, 2026-10-07..08) и эталон closed-pnl Bybit
    F("2026-10-07T05:27:21+00:00", "sell", 0.003, 84192.1, "старт/перезапуск, с опозданием 27 мин"),
    F("2026-10-07T06:00:14+00:00", "buy", 0.003, 84307.1),
    F("2026-10-07T12:00:15+00:00", "buy", 0.003, 83692.0),
    F("2026-10-08T06:00:14+00:00", "sell", 0.003, 82623.0),
    F("2026-10-08T18:00:12+00:00", "buy", 0.003, 80729.0),
]


def validation_market(until="2026-10-09T00:00:00+00:00"):
    # расчёты 10-07 16:00 и 10-08 00:00: лонг платит 0.003*83000*1.32e-5 + 0.003*82500*1.26e-5 = 0.0064 (как у Bybit);
    # price = open часовой свечи расчёта (индекс 24 = 10-08 00:00)
    return mk_market("2026-10-07T00:00:00+00:00", 72, price=lambda i: 82500.0 if i == 24 else 83000.0,
                     rates={"2026-10-07T16:00:00+00:00": 1.32e-5, "2026-10-08T00:00:00+00:00": 1.26e-5}, funding_until=until)


def test_fees_and_funding_reproduce_bybit_closed_pnl():
    r = rep(REAL, market=validation_market(), leverage=2.0)
    t1, t2 = r["trades"]
    # (1) шорт: gross -0.345, комиссии 0.2526+0.2529, net -0.8505, фандинга в окне нет (05:27 -> 06:00 между расчётами)
    assert t1["gross"] == pytest.approx(-0.345, abs=1e-9) and t1["fees"] == pytest.approx(0.2526 + 0.2529, abs=0.001)
    assert t1["funding"] == 0 and t1["funding_complete"] and t1["net"] == pytest.approx(-0.8505, abs=0.002)
    # (2) лонг: gross -3.207, комиссии 0.2511+0.2479, фандинг 0.0064 (16:00 и 00:00), net -3.7124
    assert t2["gross"] == pytest.approx(-3.207, abs=1e-9) and t2["fees"] == pytest.approx(0.2511 + 0.2479, abs=0.001)
    assert t2["funding"] == pytest.approx(0.0064, abs=0.0005) and t2["funding_complete"]
    assert t2["net"] == pytest.approx(-3.7124, abs=0.002)
    assert (t1["side"], t2["side"]) == ("short", "long")
    # (3) лонг 0.003 @ 80729 остался открытым
    assert r["open"] is not None and r["pos"] == pytest.approx(0.003) and r["avg"] == 80729.0


def test_funding_beyond_local_coverage_is_zero_but_flagged():
    mk = validation_market(until="2026-10-07T08:00:00+00:00")                # parquet кончился до расчёта 16:00
    t2 = rep(REAL, market=mk)["trades"][1]
    assert t2["funding"] == 0 and t2["funding_complete"] is False
    assert rep(REAL, market=None)["trades"][1]["funding_complete"] is False


def test_funding_sign_short_receives_and_uses_position_size_at_settlement():
    mk = mk_market("2026-01-01T00:00:00+00:00", 48, price=lambda i: 100.0, rates={"2026-01-01T08:00:00+00:00": 1e-3},
                   funding_until="2026-01-02T00:00:00+00:00")
    r = rep([F("2026-01-01T06:00:00+00:00", "sell", 2, 100), F("2026-01-01T07:00:00+00:00", "buy", 1, 100),
             F("2026-01-01T09:00:00+00:00", "buy", 1, 100)], market=mk)
    assert r["trades"][0]["funding"] == pytest.approx(-1 * 100 * 1e-3)          # шорт 1 BTC в момент расчёта: получает
    r = rep([F("2026-01-01T08:00:00+00:00", "buy", 1, 100), F("2026-01-01T09:00:00+00:00", "sell", 1, 100)], market=mk)
    assert r["trades"][0]["funding"] == 0                                       # вход ровно в расчёт: (entry, exit] — не платит
    r = rep([F("2026-01-01T07:59:00+00:00", "buy", 1, 100), F("2026-01-01T08:00:00+00:00", "sell", 1, 100)], market=mk)
    assert r["trades"][0]["funding"] == pytest.approx(0.1)                      # выход ровно в расчёт: платит


# ---------------------------------------------------------------- окно и атрибуция по времени выхода
def test_window_attributes_trades_by_exit_time():
    rows = [{"id": i + 1, "ts": a, "side": s, "qty": 1, "price": p, "reason": "бар"} for i, (a, s, p) in enumerate([
        ("2026-01-01T10:00:00+00:00", "buy", 100), ("2026-01-10T10:00:00+00:00", "sell", 110),    # вход до окна, выход внутри
        ("2026-01-10T12:00:00+00:00", "buy", 100), ("2026-01-12T10:00:00+00:00", "sell", 90),     # внутри
        ("2026-01-12T12:00:00+00:00", "buy", 100), ("2026-01-20T10:00:00+00:00", "sell", 105)])]  # выход после окна (окно кончилось)
    now = ts("2026-01-15T00:00:00+00:00")
    eq = equity_points("2026-01-01T00:00:00+00:00", 14 * 24, 1000, every_s=3600)
    out = run_compute(rows[:4], equity=eq, now=now, rk="7d")        # окно 01-08..01-15
    assert out["kpis"]["trades"] == 2 and [r["n"] for r in out["trades"]["rows"]] == [1, 2]
    out = run_compute(rows[:4], equity=eq, now=now, rk="1d")        # окно 01-14..01-15: сделок нет
    assert out["kpis"]["trades"] == 0 and out["kpis"]["win_rate_pct"] is None and out["kpis"]["profit_factor"] is None
    eq = equity_points("2026-01-01T00:00:00+00:00", 20 * 24, 1000, every_s=3600)
    out = run_compute(rows, equity=eq, now=ts("2026-01-21T00:00:00+00:00"), rk="3d")             # третья сделка закрыта 01-20
    assert out["kpis"]["trades"] == 1 and out["trades"]["rows"][0]["n"] == 3


# ---------------------------------------------------------------- equity-метрики
def day_series(vals, start="2026-03-01T12:00:00+00:00"):
    """По точке в сутки (в полдень UTC)."""
    t0 = ts(start)
    return (np.array([t0 + 86400 * i for i in range(len(vals))], dtype="int64"), np.array(vals, dtype="float64"))


def test_drawdown_roi_and_wallet_return():
    t, v = np.array([0, 3600, 7200, 10800], dtype="int64") + 1_700_000_000, np.array([100.0, 110.0, 99.0, 105.0])
    m = stats.equity_metrics(t, v, t[0], t[-1])
    assert m["max_drawdown_pct"] == pytest.approx(-10.0) and m["max_drawdown_peak_ts"] == t[1] and m["max_drawdown_trough_ts"] == t[2]
    # ROI счёта = Δ кошелька / кошелёк на начале окна (бот торгует на весь баланс, «выделенного капитала» нет)
    assert m["wallet_return_pct"] == pytest.approx(5.0)
    assert "roi_alloc_pct" not in m and "allocated_capital" not in str(m.keys())
    assert m["starts_before_data"] is False and m["eq_start_ts"] == t[0]


def test_eq_start_is_last_point_before_window_or_first_with_flag():
    t, v = np.array([100, 200, 300, 400], dtype="int64"), np.array([1.0, 2.0, 3.0, 4.0])
    m = stats.equity_metrics(t, v, 250, 400)
    assert (m["eq_start"], m["eq_start_ts"], m["starts_before_data"]) == (2.0, 200, False)
    m = stats.equity_metrics(t, v, 50, 400)
    assert (m["eq_start"], m["eq_start_ts"], m["starts_before_data"]) == (1.0, 100, True)
    assert stats.equity_metrics(t, v, 350, 400)["points"] == 2
    assert stats.equity_metrics(t, v, 450, 500) is None                 # в окне только одна точка


def test_daily_returns_skip_missing_days_never_zero_fill():
    t0 = ts("2026-03-01T12:00:00+00:00")
    days = [0, 1, 3, 4]                                              # дня 2 нет
    t = np.array([t0 + 86400 * d for d in days], dtype="int64")
    v = np.array([100.0, 110.0, 99.0, 98.01])
    m = stats.equity_metrics(t, v, t[0], t[-1])
    assert m["daily_points"] == 2                                    # 0->1 и 3->4; 1->3 (пропуск дня) не доходность дня
    assert m["best_day"] == {"date": "2026-03-02", "ret_pct": pytest.approx(10.0)}
    assert m["worst_day"] == {"date": "2026-03-05", "ret_pct": pytest.approx(-1.0)}


def test_daily_return_uses_last_sample_of_each_utc_day():
    t0 = ts("2026-03-01T00:00:00+00:00")
    t = np.array([t0 + 3600, t0 + 80000, t0 + 86400 + 100, t0 + 86400 + 80000], dtype="int64")      # два дня, по 2 точки
    v = np.array([100.0, 110.0, 105.0, 121.0])
    m = stats.equity_metrics(t, v, t[0], t[-1])
    assert m["daily_points"] == 1 and m["best_day"]["ret_pct"] == pytest.approx(10.0)             # 121/110 - 1


def test_sharpe_sortino_gating_by_daily_points():
    rng = np.random.default_rng(3)
    def series(ndays):
        v = [100.0]
        for r in rng.normal(0.002, 0.01, ndays - 1):
            v.append(v[-1] * (1 + r))
        return day_series(v)
    m13 = stats.equity_metrics(*series(14), 0, 10**12)         # 14 точек = 13 доходностей
    assert m13["daily_points"] == 13 and m13["sharpe"] is None and m13["sortino"] is None and m13["volatility_pct"] is not None
    m14 = stats.equity_metrics(*series(15), 0, 10**12)
    assert m14["daily_points"] == 14 and m14["sharpe"] is not None and m14["sortino"] is not None
    # только рост -> Sortino null (нет отрицательных дней), Sharpe есть
    up = day_series([100.0 * 1.01 ** i * (1 + 0.001 * (i % 3)) for i in range(16)])
    mu = stats.equity_metrics(*up, 0, 10**12)
    assert mu["sharpe"] is not None and mu["sortino"] is None


def test_calmar_gating_days_and_drawdown():
    rng = np.random.default_rng(4)
    def series(n):
        v = [100.0]
        for r in rng.normal(0.001, 0.01, n - 1):
            v.append(v[-1] * (1 + r))
        return day_series(v)
    assert stats.equity_metrics(*series(30), 0, 10**12)["calmar"] is None            # 29 доходностей
    m = stats.equity_metrics(*series(32), 0, 10**12)
    assert m["daily_points"] == 31 and m["max_drawdown_pct"] < 0 and m["calmar"] is not None
    mono = day_series([100.0 + i for i in range(40)])                                      # просадки нет
    assert stats.equity_metrics(*mono, 0, 10**12)["calmar"] is None


def test_equity_gaps_and_tail_gap():
    t0 = 1_700_000_000
    t = np.array([t0, t0 + 300, t0 + 600, t0 + 600 + 3600, t0 + 600 + 3900], dtype="int64")      # дыра 1 час
    v = np.ones(5)
    m = stats.equity_metrics(t, v, t0, t[-1])
    assert m["gaps"] == {"count": 1, "total_s": 3600}
    m = stats.equity_metrics(t, v, t0, t[-1] + 1800)                                         # + хвост: бот молчит 30 мин
    assert m["gaps"] == {"count": 2, "total_s": 3600 + 1800}


def test_downsample_is_bounded_and_keeps_extremes():
    n = 5000
    t = np.arange(n, dtype="int64") * 300 + 10**9
    v = 100 + np.sin(np.arange(n) / 50.0) * 5
    v[2222] = 150.0                                                  # одиночный выброс должен выжить в hi
    dd = v / np.maximum.accumulate(v) - 1
    d = stats.downsample(t, v, dd)
    assert len(d["equity"]["t"]) <= stats.EQ_POINTS_MAX and len(d["drawdown"]["dd_pct"]) == len(d["equity"]["t"])
    assert max(d["equity"]["hi"]) == pytest.approx(150.0) and d["equity"]["t"][-1] == t[-1] and d["equity"]["v"][-1] == pytest.approx(v[-1], abs=1e-3)
    assert min(d["drawdown"]["dd_pct"]) == pytest.approx(dd.min() * 100, abs=1e-3)
    raw = stats.downsample(t[:100], v[:100], dd[:100])
    assert raw["equity"]["v"] == raw["equity"]["lo"] == raw["equity"]["hi"] and len(raw["equity"]["t"]) == 100


# ---------------------------------------------------------------- корзины P/L по диапазону
def test_bucket_kind_per_range():
    assert {k: stats._bucket_kind(k) for k in stats.RANGE_SECONDS} == {
        "1h": "5min", "1d": "hour", "3d": "day", "7d": "day", "30d": "day", "90d": "week", "180d": "week", "365d": "week",
        "730d": "month", "1095d": "month", "all": "month"}


def test_bucket_starts_are_utc_aligned():
    wed = ts("2026-10-07T13:45:10+00:00")
    assert iso(stats.bucket_start("5min", wed)) == "2026-10-07T13:45:00+00:00"
    assert iso(stats.bucket_start("hour", wed)) == "2026-10-07T13:00:00+00:00"
    assert iso(stats.bucket_start("day", wed)) == "2026-10-07T00:00:00+00:00"
    assert iso(stats.bucket_start("week", wed)) == "2026-10-05T00:00:00+00:00"          # понедельник
    assert iso(stats.bucket_start("week", ts("2026-10-11T23:59:59+00:00"))) == "2026-10-05T00:00:00+00:00"
    assert iso(stats.bucket_start("month", ts("2026-10-31T23:00:00+00:00"))) == "2026-10-01T00:00:00+00:00"


def test_pnl_buckets_are_contiguous_with_empty_buckets():
    trs = [{"exit_ts": ts("2026-10-01T10:00:00+00:00"), "net": 2.0}, {"exit_ts": ts("2026-10-01T11:00:00+00:00"), "net": -1.0},
           {"exit_ts": ts("2026-12-05T00:00:00+00:00"), "net": 5.0}]
    rows = stats.pnl_buckets("month", trs, ts("2026-09-20T00:00:00+00:00"), ts("2026-12-20T00:00:00+00:00"))
    assert [iso(r["t"])[:7] for r in rows] == ["2026-09", "2026-10", "2026-11", "2026-12"]
    assert [(r["n"], r["net"]) for r in rows] == [(0, 0.0), (2, 1.0), (0, 0.0), (1, 5.0)] and rows[1]["wins"] == 1
    rows = stats.pnl_buckets("5min", [{"exit_ts": 1000, "net": 1}], 0, 3600)
    assert len(rows) == 13 and rows[3]["n"] == 1


def test_histogram_bins():
    h = stats.histogram([1.0, 2.0, 2.5, 9.0] * 10)
    assert sum(h["counts"]) == 40 and len(h["edges"]) == len(h["counts"]) + 1 and h["edges"][0] == 1.0 and h["edges"][-1] == 9.0
    assert stats.histogram([]) == {"edges": [], "counts": []}
    same = stats.histogram([3.0, 3.0])
    assert same["counts"] == [2] and same["edges"][0] < 3.0 < same["edges"][1]


# ---------------------------------------------------------------- KPI сделок
def test_trade_metrics_values():
    def tr(n, net, side="long", hold=3600, gross=None):
        return {"n": n, "net": net, "ret_pct": net, "gross": net + 0.2 if gross is None else gross, "fees": 0.2, "funding": 0.0, "hold_s": hold,
                "side": side, "entry_notional": 100.0, "exit_notional": 100.0, "exit_ts": n}
    trs = [tr(1, 5.0), tr(2, -2.0, "short"), tr(3, -4.0), tr(4, 3.0), tr(5, 0.0), tr(6, 1.0, hold=7200)]
    m = stats.trade_metrics(trs, [(0, 1800), (2000, 5000)], 0, 10000, None)
    assert (m["trades"], m["wins"], m["losses"], m["breakeven"]) == (6, 3, 2, 1)
    assert m["win_rate_pct"] == 50.0 and m["avg_win"] == pytest.approx(3.0) and m["avg_loss"] == pytest.approx(-3.0)
    assert m["payoff_ratio"] == pytest.approx(1.0) and m["profit_factor"] == pytest.approx(9 / 6)
    assert m["expectancy_usdt"] == pytest.approx(0.5) and m["max_win_streak"] == 1 and m["max_loss_streak"] == 2
    assert m["best_trade"]["n"] == 1 and m["worst_trade"]["n"] == 3
    assert m["hold_mean_s"] == pytest.approx((5 * 3600 + 7200) / 6) and m["hold_median_s"] == 3600
    assert m["time_in_market_pct"] == pytest.approx((1800 + 3000) / 10000 * 100)
    assert m["turnover"] == 1200 and m["fees_total"] == pytest.approx(1.2) and m["net_pnl"] == pytest.approx(3.0) and "net_pnl_pct_alloc" not in m
    assert m["fees_pct_of_gross_profit"] == pytest.approx(1.2 / (5.2 + 3.2 + 1.2 + 0.2) * 100, abs=0.01)
    assert m["buy_hold_pct"] is None
    assert stats.trade_metrics([tr(1, 1.0)], [], 0, 10, None)["profit_factor"] is None          # без убытков
    # суммы в долларах: Σ прибыльных и Σ убыточных Net (убыток — отрицательное число), вместе дают net_pnl
    assert m["profit_sum_usdt"] == pytest.approx(9.0) and m["loss_sum_usdt"] == pytest.approx(-6.0)
    assert m["profit_sum_usdt"] + m["loss_sum_usdt"] == pytest.approx(m["net_pnl"])
    empty = stats.trade_metrics([], [], 0, 10, None)
    assert empty["profit_sum_usdt"] == 0 and empty["loss_sum_usdt"] == 0


# ---------------------------------------------------------------- деньги: кошелёк на входе и результат в % счёта
def _acct_rows():
    return [{"id": 1, "ts": "2026-02-01T10:00:00+00:00", "side": "buy", "qty": 1, "price": 100.0, "reason": "бар"},
            {"id": 2, "ts": "2026-02-01T12:00:00+00:00", "side": "sell", "qty": 1, "price": 110.0, "reason": "бар"},
            {"id": 3, "ts": "2026-02-02T10:00:00+00:00", "side": "sell", "qty": 1, "price": 110.0, "reason": "бар"},
            {"id": 4, "ts": "2026-02-02T11:00:00+00:00", "side": "buy", "qty": 1, "price": 120.0, "reason": "бар"}]


def test_account_pct_uses_wallet_at_entry_not_window_start():
    eq = [("2026-02-01T00:00:00+00:00", 1000.0), ("2026-02-01T09:59:00+00:00", 1000.0), ("2026-02-01T11:00:00+00:00", 1004.0),
          ("2026-02-02T09:59:00+00:00", 1200.0), ("2026-02-02T12:00:00+00:00", 1190.0)]
    out = run_compute(_acct_rows(), equity=eq, now=ts("2026-02-02T13:00:00+00:00"), rk="7d")
    a, b = out["trades"]["rows"]
    # последняя точка <= ПЕРВОГО fill (а не на конец сделки и не на начало окна)
    assert a["equity_at_entry"] == 1000.0 and b["equity_at_entry"] == 1200.0
    assert a["ret_account_pct"] == pytest.approx(a["net"] / 1000.0 * 100, abs=1e-4)
    assert b["ret_account_pct"] == pytest.approx(b["net"] / 1200.0 * 100, abs=1e-4) and b["net"] < 0
    assert b["ret_account_pct"] != pytest.approx(b["ret_pct"], abs=0.01)             # это НЕ доходность на входной объём
    k = out["kpis"]
    assert k["profit_sum_usdt"] == pytest.approx(a["net"]) and k["loss_sum_usdt"] == pytest.approx(b["net"])
    assert k["profit_sum_usdt"] + k["loss_sum_usdt"] == pytest.approx(k["net_pnl"], abs=1e-3)


def test_account_pct_is_null_without_equity_sample_at_or_before_entry():
    # все точки equity позже входа -> кошелька на входе нет; процент не выдумывается, доллары остаются
    eq = [("2026-02-01T11:00:00+00:00", 1004.0), ("2026-02-01T23:00:00+00:00", 1005.0), ("2026-02-02T12:00:00+00:00", 1000.0)]
    out = run_compute(_acct_rows(), equity=eq, now=ts("2026-02-02T13:00:00+00:00"), rk="7d")
    a, _ = out["trades"]["rows"]
    assert a["equity_at_entry"] is None and a["ret_account_pct"] is None and a["net"] is not None
    # точка ровно в момент первого fill считается «не позже»
    eq2 = [("2026-02-01T10:00:00+00:00", 800.0)] + eq
    a2 = run_compute(_acct_rows(), equity=eq2, now=ts("2026-02-02T13:00:00+00:00"), rk="7d")["trades"]["rows"][0]
    assert a2["equity_at_entry"] == 800.0 and a2["ret_account_pct"] == pytest.approx(a2["net"] / 800.0 * 100, abs=1e-4)


def test_account_pct_ignores_stale_wallet_sample_older_than_a_day():
    eq = [("2026-01-25T00:00:00+00:00", 1000.0), ("2026-02-01T11:00:00+00:00", 1004.0), ("2026-02-02T12:00:00+00:00", 1000.0)]
    a = run_compute(_acct_rows(), equity=eq, now=ts("2026-02-02T13:00:00+00:00"), rk="30d")["trades"]["rows"][0]
    assert a["equity_at_entry"] is None and a["ret_account_pct"] is None            # единственная точка до входа — недельной давности


def test_account_pct_reads_equity_before_short_window_start(tmp_path):
    # сделка вошла за ~3 суток до «сейчас», окно 1d: точки equity до начала окна всё равно читаются из БД
    now = ts("2026-02-05T00:00:00+00:00")
    rows = [("2026-02-01T10:00:00+00:00", "buy", 1, 100.0, "бар"), ("2026-02-04T23:00:00+00:00", "sell", 1, 105.0, "бар")]
    eq = [(iso(ts("2026-02-01T00:00:00+00:00") + 3600 * i), 500.0 + i) for i in range(0, 4 * 24 + 1)]
    p = make_db(tmp_path / "a.db", trades=rows, equity=eq)
    out = stats.get_stats("1d", p, now)
    (r,) = out["trades"]["rows"]
    assert r["equity_at_entry"] == 510.0 and r["ret_account_pct"] == pytest.approx(r["net"] / 510.0 * 100, abs=1e-4)


def test_time_in_market_is_clipped_by_window():
    m = stats.trade_metrics([], [(-500, 100), (900, 5000)], 0, 1000, None)
    assert m["time_in_market_pct"] == pytest.approx((100 + 100) / 1000 * 100)


def test_buy_hold_from_candles():
    mk = mk_market("2026-01-01T00:00:00+00:00", 48, price=lambda i: 100.0 + i)
    start, end = ts("2026-01-01T05:00:00+00:00"), ts("2026-01-02T10:00:00+00:00")
    m = stats.trade_metrics([], [], start, end, mk)
    assert m["buy_hold_pct"] == pytest.approx((mk.price_at(end) / mk.price_at(start) - 1) * 100, abs=0.01)
    assert stats.trade_metrics([], [], start, ts("2026-01-05T00:00:00+00:00"), mk)["buy_hold_pct"] is None      # свечи устарели


# ---------------------------------------------------------------- сверка с кошельком
def _recon_case(market, with_price=True):
    now = ts("2026-02-02T00:00:00+00:00")
    start = now - 86400                                                         # диапазон 1d
    rows = [{"id": 1, "ts": iso(start - 5 * 3600), "side": "buy", "qty": 1, "price": 100.0, "reason": "бар"},
            {"id": 2, "ts": iso(start + 5 * 3600), "side": "sell", "qty": 1, "price": 120.0, "reason": "бар"}]
    # кошелёк: на старте окна позиция уже +10 (цена 110) за вычетом комиссии входа, к концу +10 и минус комиссия выхода
    w0 = 1000 + 10 - 0.1
    w1 = w0 + 10 - 0.12
    eq = [(iso(start - 3600), w0), (iso(start), w0), (iso(start + 5 * 3600), w1), (iso(now - 600), w1)]
    return run_compute(rows, equity=eq, now=now, rk="1d", market=market)


def test_reconciliation_marks_to_market_position_open_at_window_start():
    mk = mk_market("2026-01-30T00:00:00+00:00", 120, price=lambda i: 110.0, funding_until="2026-02-03T00:00:00+00:00")
    out = _recon_case(mk)
    r = out["reconciliation"]
    assert r["flat_at_start"] is False and r["reason"] is None
    assert r["wallet_delta"] == pytest.approx(9.88) and r["bot_pnl_est"] == pytest.approx(9.88)
    assert r["unexplained"] == pytest.approx(0, abs=1e-6)


def test_reconciliation_null_without_start_price():
    out = _recon_case(None)
    assert out["reconciliation"]["unexplained"] is None and out["reconciliation"]["reason"] == "no_start_price"


def test_reconciliation_when_flat_at_start():
    rows = [{"id": 1, "ts": "2026-02-01T10:00:00+00:00", "side": "buy", "qty": 1, "price": 100.0, "reason": "бар"},
            {"id": 2, "ts": "2026-02-01T12:00:00+00:00", "side": "sell", "qty": 1, "price": 110.0, "reason": "бар"}]
    eq = [("2026-02-01T00:00:00+00:00", 500.0), ("2026-02-01T12:00:00+00:00", 509.79), ("2026-02-01T23:00:00+00:00", 509.0)]
    out = run_compute(rows, equity=eq, now=ts("2026-02-02T00:00:00+00:00"), rk="3d")
    r = out["reconciliation"]
    assert r["flat_at_start"] is True and r["bot_pnl_est"] == pytest.approx(9.79)
    assert r["unexplained"] == pytest.approx(9.0 - 9.79)                          # Δ кошелька 9 против 9.79 оценки


# ---------------------------------------------------------------- предупреждения
def test_warnings_data_flags():
    rows = [{"id": 1, "ts": "2026-02-01T10:00:00+00:00", "side": "buy", "qty": 1, "price": 100.0, "reason": "бар"},
            {"id": 2, "ts": "2026-02-01T12:00:00+00:00", "side": "sell", "qty": 1, "price": 110.0, "reason": "бар"},
            {"id": 3, "ts": "2026-02-01T12:30:00+00:00", "side": "sell", "qty": 1, "price": 0, "reason": "бар"}]
    eq = equity_points("2026-02-01T00:00:00+00:00", 30, 500, every_s=3600)
    out = run_compute(rows, equity=eq, now=ts("2026-02-02T06:00:00+00:00"), rk="3d")
    codes = {w["code"] for w in out["warnings"]}
    assert {"few_trades", "candles_missing", "bad_fills"} <= codes and "equity_gaps" in codes       # хвост 5 ч без точек
    assert "env_mixed" not in codes and "environments" not in out["data"] and "env" not in out["config"]


def test_warning_starts_before_data_and_funding_stale():
    rows = [{"id": 1, "ts": "2026-02-01T10:00:00+00:00", "side": "buy", "qty": 1, "price": 100.0, "reason": "бар"},
            {"id": 2, "ts": "2026-02-02T12:00:00+00:00", "side": "sell", "qty": 1, "price": 110.0, "reason": "бар"}]
    mk = mk_market("2026-01-31T00:00:00+00:00", 96, funding_until="2026-02-01T00:00:00+00:00")
    eq = equity_points("2026-02-01T00:00:00+00:00", 40, 500, every_s=3600)
    out = run_compute(rows, equity=eq, now=ts("2026-02-02T16:00:00+00:00"), rk="30d", market=mk)
    codes = {w["code"] for w in out["warnings"]}
    assert "starts_before_data" in codes and "funding_stale" in codes
    assert out["data"]["funding_coverage_until"] == int(ts("2026-02-01T00:00:00+00:00"))
    assert out["window"]["effective_start"] == out["equity"]["eq_start_ts"] > out["window"]["start"]


# ---------------------------------------------------------------- сравнение с предыдущим окном
def test_comparison_only_from_7d_and_never_for_all(tmp_path):
    p = golden_db(tmp_path / "g.db")
    r7 = stats.get_stats("7d", p, fx.NOW1)["comparison"]
    assert r7["available"] and r7["prev_end"] - r7["prev_start"] == 7 * 86400
    assert set(r7["kpis"]["net_pnl"]) == {"cur", "prev", "delta"} and "wallet_return_pct" in r7["equity"] and "roi_alloc_pct" not in r7["equity"]
    d = r7["kpis"]["trades"]
    assert d["delta"] == pytest.approx(d["cur"] - d["prev"])
    assert stats.get_stats("1d", p, fx.NOW1)["comparison"] == {"available": False, "reason": "range_too_short"}
    assert stats.get_stats("3d", p, fx.NOW1)["comparison"]["reason"] == "range_too_short"
    assert stats.get_stats("all", p, fx.NOW1)["comparison"] == {"available": False, "reason": "all_range"}
    assert stats.get_stats("30d", p, fx.NOW1)["comparison"] == {"available": False, "reason": "no_previous_data"}    # 40 сут данных < 60


# ---------------------------------------------------------------- золотая БД: сквозной
def test_golden_db_end_to_end_all_ranges(tmp_path):
    p = golden_db(tmp_path / "g.db")
    for rk in stats.RANGE_SECONDS:
        out = stats.get_stats(rk, p, fx.NOW1)
        assert out["available"] and out["range"] == rk
        json.dumps(stats_clean(out), allow_nan=False)
        s = out["series"]
        assert len(s["equity"]["t"]) <= stats.EQ_POINTS_MAX
        assert len(s["equity"]["t"]) == len(s["equity"]["v"]) == len(s["drawdown"]["t"])
        assert out["trades"]["total"] == out["kpis"]["trades"] == len(out["trades"]["rows"])
        assert out["open_position"]["side"] == "long" and out["position_mismatch"] is None
    allr = stats.get_stats("all", p, fx.NOW1)
    assert allr["kpis"]["trades"] == 10 and sum(1 for r in allr["trades"]["rows"] if r["reconstructed"]) == 1
    assert allr["window"]["start"] == allr["equity"]["eq_start_ts"]
    assert sum(g["count"] for g in allr["breakdown"]["by_side"]) == 10
    assert {g["key"] for g in allr["breakdown"]["by_reason"]} == {"бар", "закрытие вручную", stats.STOP_REASON}
    assert sum(c["trades"] for c in allr["series"]["calendar"]) == 10 and sum(allr["series"]["histogram"]["counts"]) == 10


def stats_clean(o):
    from tradebot.web.serialize import clean
    return clean(o)


# ---------------------------------------------------------------- пустые состояния и API
def test_unavailable_states(tmp_path):
    no_db = stats.get_stats("30d", tmp_path / "nope.db", fx.NOW1)
    assert (no_db["available"], no_db["reason"]) == (False, "no_db") and no_db["warnings"] == [] and no_db["definitions"]
    empty = make_db(tmp_path / "e.db", equity=equity_points(fx.D0, 5, 100))
    assert stats.get_stats("30d", empty, fx.NOW1)["reason"] == "no_trades"
    one = make_db(tmp_path / "o.db", trades=[("2026-04-22T10:00:00+00:00", "buy", 1, 100.0, "бар")],
                  equity=[("2026-04-22T09:00:00+00:00", 100.0)])
    out = stats.get_stats("all", one, ts("2026-04-23T00:00:00+00:00"))
    assert (out["available"], out["reason"]) == (False, "no_equity") and out["message"]
    bad = tmp_path / "junk.db"
    bad.write_bytes(b"this is not sqlite" * 50)
    assert stats.get_stats("30d", bad, fx.NOW1)["reason"] == "db_error"


def test_api_status_codes_and_defaults(tmp_path, monkeypatch):
    from tradebot.trading import state
    r = client.get("/api/stats?range=banana")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_request"
    assert client.get("/api/stats?range=").status_code == 400
    j = client.get("/api/stats").json()                                         # БД нет -> 200 + available:false
    assert j["available"] is False and j["reason"] == "no_db" and j["range"] == "30d"
    golden_db(state.DB_PATH)
    j = client.get("/api/stats").json()
    assert j["available"] and j["range"] == "30d"
    for rk in stats.RANGE_SECONDS:
        r = client.get("/api/stats", params={"range": rk})
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", rk
        assert r.json()["range"] == rk


def test_api_never_returns_500(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(stats, "get_stats", boom)
    r = client.get("/api/stats?range=7d")
    assert r.status_code == 200 and r.json()["available"] is False and r.json()["reason"] == "error"


def test_json_has_no_nan_or_inf(tmp_path, monkeypatch):
    from tradebot.trading import state
    p = golden_db(state.DB_PATH)
    db = sqlite3.connect(str(p))
    db.execute("INSERT INTO equity(ts,v) VALUES('2026-05-20T00:00:30+00:00', NULL)")
    db.execute("INSERT INTO equity(ts,v) VALUES('2026-05-20T00:00:40+00:00', ?)", (float("inf"),))
    db.execute("INSERT INTO trades(ts,side,qty,price,reason,order_id) VALUES('2026-05-20T00:00:30+00:00','buy',1,?, 'x','o')", (float("inf"),))
    db.commit()
    db.close()
    for rk in ("7d", "all"):
        text = client.get("/api/stats", params={"range": rk}).text
        assert "NaN" not in text and "Infinity" not in text
        j = json.loads(text, parse_constant=lambda c: pytest.fail(f"недопустимая константа {c}"))
        assert j["available"]
        assert "bad_fills" in {w["code"] for w in j["warnings"]}                    # price=inf отброшен


def test_reads_db_read_only_and_does_not_modify(tmp_path):
    p = golden_db(tmp_path / "g.db")
    before = p.read_bytes()
    stats.get_stats("30d", p, fx.NOW1)
    assert p.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):                              # соединение именно read-only
        c = sqlite3.connect(stats._db_uri(p), uri=True)
        c.execute("INSERT INTO kv(k,v) VALUES('x','1')")


# ---------------------------------------------------------------- кэш
def test_cache_hits_and_invalidates_on_new_trade(tmp_path, monkeypatch):
    p = golden_db(tmp_path / "g.db")
    calls = []
    real = stats.read_db
    monkeypatch.setattr(stats, "read_db", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    a = stats.get_stats("30d", p, fx.NOW1)
    b = stats.get_stats("30d", p, fx.NOW1)
    assert a is b and len(calls) == 1                                          # второй вызов — из кэша, без чтения таблиц
    stats.get_stats("7d", p, fx.NOW1)
    assert len(calls) == 2                                                     # другой диапазон — другой ключ
    add_trade(p, "2026-05-31T23:00:00+00:00", "sell", 1.0, 120.0)             # закрывает открытый лонг -> +1 сделка
    c = stats.get_stats("30d", p, fx.NOW1)
    assert c is not a and len(calls) == 3
    assert c["kpis"]["trades"] == a["kpis"]["trades"] + 1 and c["position_mismatch"] is not None      # kv ещё держит позицию
    # новая точка equity тоже инвалидирует
    db = sqlite3.connect(str(p))
    db.execute("INSERT INTO equity(ts,v) VALUES('2026-06-01T01:00:00+00:00', 170.0)")
    db.commit()
    db.close()
    d = stats.get_stats("30d", p, fx.NOW1)
    assert d is not c and d["equity"]["eq_end"] == 170.0
    # смена «минуты» тоже обновляет ключ (кэш не живёт дольше TTL)
    e = stats.get_stats("30d", p, fx.NOW1 + 120)
    assert e is not d


def test_cache_is_bounded(tmp_path):
    p = golden_db(tmp_path / "g.db")
    for i in range(stats.CACHE_SIZE + 5):
        stats.get_stats("30d", p, fx.NOW1 + 60 * i)
    assert len(stats._cache) == stats.CACHE_SIZE


# ---------------------------------------------------------------- загрузка рынка
def test_load_market_returns_none_when_data_missing(tmp_path, monkeypatch):
    from tradebot.market import klines
    from tradebot.web import service
    d = tmp_path / "market"
    d.mkdir()
    monkeypatch.setattr(klines, "DATA_DIR", d)
    monkeypatch.setattr(service, "MARKET", d)
    service.invalidate_data()
    m, err = REAL_LOAD_MARKET()
    assert m is None and err == "DataMissing"


def test_load_market_reads_candles_and_funding_coverage(synth_market):
    from _synth import END
    m, err = REAL_LOAD_MARKET()
    assert err is None and isinstance(m, Market)
    assert m.candles_until == END.timestamp() + 3600                          # последняя свеча 00:00 закрывается в 01:00
    assert m.funding_until == (END + __import__("pandas").Timedelta(hours=8)).timestamp()
    assert m.settle and all(t % stats.SETTLE_S == 0 for t in m.settle)
    assert m.price_at(END.timestamp() + 3600) is not None
