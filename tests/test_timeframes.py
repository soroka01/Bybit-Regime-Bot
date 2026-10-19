"""Таймфреймы: единый список, пределы истории, недельная сетка, гранулярность деривативов (без сети)."""
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tradebot.market import derivs, klines
from tradebot.web import service
from tradebot.web.api import app

client = TestClient(app)
UTC = "UTC"


def test_timeframes_contents():
    tf = klines.TIMEFRAMES
    assert list(tf) == ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"]
    assert tf["1m"] == ("1", 1) and tf["3m"] == ("3", 3) and tf["6h"] == ("360", 360)
    assert tf["12h"] == ("720", 720) and tf["1w"] == ("W", 10080)
    assert "1M" not in tf and "M" not in tf                      # месячные свечи переменной длины не поддерживаются
    mins = [m for _, m in tf.values()]
    assert mins == sorted(mins)


def test_history_caps():
    assert klines.HISTORY_DAYS == {"1m": 90, "3m": 270, "5m": 540}
    assert klines.effective_days("1m", 1095) == 90 and klines.effective_days("3m", 1095) == 270
    assert klines.effective_days("5m", 365) == 365 and klines.effective_days("5m", 1095) == 540
    assert klines.effective_days("1h", 1095) == 1095 and klines.effective_days("1w", 5000) == 1095
    assert klines.estimate_requests("1m", 1095) == 130            # 90 дней * 1440 / 1000


def test_load_caps_requested_history(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(klines, "DATA_DIR", tmp_path)

    def fake_fetch(symbol, tf, start_ms, end_ms, category="linear"):
        seen["span_days"] = (end_ms - start_ms) / 86_400_000
        idx = pd.date_range(end=pd.Timestamp.now(tz=UTC).floor("min") - pd.Timedelta(minutes=5), periods=50, freq="min")
        return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0, "turnover": 1.0}, index=idx.rename("ts"))

    monkeypatch.setattr(klines, "_fetch_range", fake_fetch)
    klines.load("BTCUSDT", "1m", 1095, refresh=False)
    assert 89.9 < seen["span_days"] < 90.1                       # 1095 запрошено, но не больше 90 для 1m


def test_weekly_alignment_helpers():
    now = pd.Timestamp("2026-10-06 10:30", tz=UTC)                # вторник
    assert klines.bar_floor(now, "1w") == pd.Timestamp("2026-10-05", tz=UTC)       # понедельник, а не четверг (эпоха)
    assert klines.bar_floor(now, "1d") == pd.Timestamp("2026-10-06", tz=UTC)
    assert klines.bar_floor(now, "12h") == pd.Timestamp("2026-10-06 00:00", tz=UTC)
    assert klines.bar_floor(now, "6h") == pd.Timestamp("2026-10-06 06:00", tz=UTC)
    assert klines.last_closed_start(now, "1w") == pd.Timestamp("2026-09-28", tz=UTC)
    # сетка от реальной последней свечи: понедельничная неделя 09-28 закрылась 10-05 00:00
    last = pd.Timestamp("2026-09-28", tz=UTC)
    assert klines.last_closed_start(now, "1w", last) == last
    assert klines.last_closed_start(now, "1w", pd.Timestamp("2026-09-21", tz=UTC)) == last
    # свеча, закрывающаяся ровно сейчас, считается закрытой
    assert klines.last_closed_start(pd.Timestamp("2026-10-05 00:00", tz=UTC), "1w", last) == last
    assert klines.last_closed_start(now, "6h", pd.Timestamp("2026-10-06 00:00", tz=UTC)) == pd.Timestamp("2026-10-06 00:00", tz=UTC)


@pytest.mark.parametrize("tf,last,behind,level", [
    ("1w", "2026-09-28", 0, "ok"), ("1w", "2026-09-21", 1, "ok"), ("1w", "2026-08-31", 4, "bad"),
    ("1d", "2026-10-05", 0, "ok"), ("1d", "2026-10-02", 3, "warn"),
    ("12h", "2026-10-05 12:00", 0, "ok"), ("6h", "2026-10-05 12:00", 2, "warn"),
])
def test_status_expected_bar_for_coarse_frames(tmp_path, monkeypatch, tf, last, behind, level):
    monkeypatch.setattr(klines, "DATA_DIR", tmp_path)
    monkeypatch.setattr(service, "MARKET", tmp_path)
    step = pd.Timedelta(minutes=klines.TIMEFRAMES[tf][1])
    idx = pd.date_range(end=pd.Timestamp(last, tz=UTC), periods=30, freq=step).rename("ts")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0, "turnover": 1.0}, index=idx)
    klines._atomic_parquet(df, klines._path("BTCUSDT", tf))
    now = pd.Timestamp("2026-10-06 10:30", tz=UTC)
    st = service.data_status("BTCUSDT", tf, now=now)
    assert st["behind_bars"] == behind and st["level"] == level


@pytest.mark.parametrize("minutes,gran", [(1, "5min"), (3, "5min"), (5, "5min"), (15, "15min"), (30, "30min"), (60, "1h"),
                                          (120, "1h"), (240, "4h"), (360, "4h"), (720, "4h"), (1440, "1d"), (10080, "1d")])
def test_derivs_granularity_mapping(minutes, gran):
    assert derivs.granularity_for(minutes) == gran
    assert {m for _, m in klines.TIMEFRAMES.values()} == set(derivs.GRANULARITY)      # покрыты все таймфреймы
    assert gran in derivs.GRAN_MINUTES


def _fake_derivs(monkeypatch, calls):
    f_idx = pd.date_range("2026-08-01", "2026-10-06", freq="8h", tz=UTC)
    f = pd.Series(np.arange(len(f_idx)) * 1e-6 + 1e-4, index=f_idx)
    monkeypatch.setattr(derivs, "funding", lambda symbol="BTCUSDT", days=1095, refresh=True: f)

    def oi(symbol="BTCUSDT", days=1095, interval="1h", refresh=True):
        calls.append(("oi", interval))
        i = pd.date_range("2026-08-01", "2026-10-06", freq="1h", tz=UTC)
        return pd.Series(np.arange(len(i), dtype=float), index=i)

    def lsr(symbol="BTCUSDT", days=1095, period="1h", refresh=True):
        calls.append(("lsr", period))
        i = pd.date_range("2026-08-01", "2026-10-06", freq="1h", tz=UTC)
        return pd.Series(1.0, index=i)

    monkeypatch.setattr(derivs, "open_interest", oi)
    monkeypatch.setattr(derivs, "long_short_ratio", lsr)
    return f


def _bars(freq, start, n):
    idx = pd.date_range(start, periods=n, freq=freq, tz=UTC).rename("ts")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=idx)


def test_enrich_uses_granularity_and_keeps_funding_path(monkeypatch):
    calls = []
    f = _fake_derivs(monkeypatch, calls)
    h1 = derivs.enrich(_bars("1h", "2026-09-01", 200), refresh=False)
    assert ("oi", "1h") in calls and ("lsr", "1h") in calls
    expect = f.groupby(f.index.floor(pd.Timedelta(hours=1))).sum().reindex(h1.index).fillna(0.0)   # прежняя формула
    assert np.allclose(h1["funding"], expect)
    assert (h1["funding"] > 0).sum() == len(f[(f.index >= h1.index[0]) & (f.index <= h1.index[-1])])
    calls.clear()
    derivs.enrich(_bars("15min", "2026-09-01", 50), refresh=False)
    assert ("oi", "15min") in calls
    calls.clear()
    derivs.enrich(_bars("6h", "2026-09-01", 40), refresh=False)
    assert ("oi", "4h") in calls


def test_enrich_weekly_funding_on_monday_grid(monkeypatch):
    f = _fake_derivs(monkeypatch, [])
    wk = derivs.enrich(_bars("7D", "2026-08-03", 8), refresh=False)       # 2026-08-03 — понедельник
    assert wk.index[0].weekday() == 0
    assert np.isclose(wk["funding"].sum(), f[(f.index >= wk.index[0]) & (f.index < wk.index[-1] + pd.Timedelta(days=7))].sum())
    first_week = f[(f.index >= wk.index[0]) & (f.index < wk.index[0] + pd.Timedelta(days=7))].sum()
    assert np.isclose(wk["funding"].iloc[0], first_week) and first_week > 0


def test_meta_derives_from_timeframes():
    m = client.get("/api/meta").json()
    assert m["timeframes"] == list(klines.TIMEFRAMES) == m["leaderboard_tfs"]
    assert m["history_days"] == {t: klines.history_days(t) for t in klines.TIMEFRAMES}
    assert m["history_days"]["1m"] == 90 and m["history_days"]["1w"] == 1095
    assert m["portfolio_tfs"] == [t for t, (_, mm) in klines.TIMEFRAMES.items() if mm >= 60]
    assert set(m["portfolio_tfs"]) == {"1h", "2h", "4h", "6h", "12h", "1d", "1w"}
    est = m["fetch_estimate"]["1m"]
    assert est["klines_requests"] == 130 and est["requests"] > 130 and est["minutes"] >= 1
    assert set(m["fetch_estimate"]) == set(klines.TIMEFRAMES)
    assert all(m["timeframe_minutes"][t] == mm for t, (_, mm) in klines.TIMEFRAMES.items())


def test_new_frames_validate_and_report_missing_data_with_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(klines, "DATA_DIR", tmp_path)         # пустой кэш: 1m точно «не скачан»
    monkeypatch.setattr(service, "MARKET", tmp_path)
    service.invalidate_data()
    r = client.post("/api/backtest", json={"strategy": "EMA cross", "tf": "1m", "symbol": "BTCUSDT", "days": 365})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "data_missing" and d["estimate"]["klines_requests"] == 130
    assert client.post("/api/backtest", json={"strategy": "EMA cross", "tf": "1M"}).status_code == 422   # месячных свечей нет
    st = client.get("/api/data/status", params={"symbol": "BTCUSDT", "tf": "1w"}).json()
    assert st["step_min"] == 10080 and "estimate" in st


def test_portfolio_rejects_short_leg_timeframe():
    leg = {"strategy": "EMA cross", "tf": "15m", "params": {}, "weight": 0.5}
    r = client.post("/api/portfolio", json={"legs": [leg], "days": 90})
    assert r.status_code == 422 and "15m" in r.json()["detail"]["message"]
    assert time.time() > 0
