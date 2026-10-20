"""Аудит market/: общие parquet-записи бота и веба (уникальные tmp, блокировки по пути, таймауты), отмена загрузок,
журнал пропусков и устаревших рядов, пустые ответы биржи, защита путей. Сети нет: _fetch_range/requests подменены."""
import logging
import threading
import time

import numpy as np
import pandas as pd
import pytest

from _synth import bars
from tradebot.market import derivs, guard, klines


@pytest.fixture
def mdir(tmp_path, monkeypatch):
    d = tmp_path / "market"
    d.mkdir()
    monkeypatch.setattr(klines, "DATA_DIR", d)
    klines._gap_seen.clear()
    derivs._stale_seen.clear()
    yield d


def _hold(path, seconds):
    """Занимает блокировку файла в другом потоке на `seconds`; возвращает (поток, событие «захвачено»)."""
    got = threading.Event()

    def run():
        with guard.locked(path, timeout=5):
            got.set()
            time.sleep(seconds)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert got.wait(2)
    return t


# ---------------------------------------------------------------- _atomic_parquet: уникальные временные файлы
def test_atomic_parquet_tmp_names_are_unique_and_cleaned(mdir, monkeypatch):
    seen = []
    real = pd.DataFrame.to_parquet

    def spy(self, path, *a, **k):
        seen.append(path.name)
        return real(self, path, *a, **k)
    monkeypatch.setattr(pd.DataFrame, "to_parquet", spy)
    p = klines._path("BTCUSDT", "1h")
    for _ in range(3):
        klines._atomic_parquet(bars(1, 10, 1), p)
    assert len(set(seen)) == 3 and all(n.startswith(p.name + ".") and n.endswith(".tmp") for n in seen)
    assert sorted(x.name for x in mdir.iterdir()) == [p.name]             # временных файлов не осталось
    assert len(pd.read_parquet(p)) == 10


def test_atomic_parquet_failure_removes_tmp_and_keeps_old_file(mdir):
    p = klines._path("BTCUSDT", "1h")
    klines._atomic_parquet(bars(1, 10, 1), p)

    class Boom:
        def to_parquet(self, path):
            path.write_bytes(b"half")
            raise RuntimeError("диск")
    with pytest.raises(RuntimeError):
        klines._atomic_parquet(Boom(), p)
    assert sorted(x.name for x in mdir.iterdir()) == [p.name] and len(pd.read_parquet(p)) == 10


def test_atomic_parquet_sweeps_only_stale_orphans(mdir):
    p = klines._path("BTCUSDT", "1h")
    old = mdir / (p.name + ".999.deadbeef.tmp")
    fresh = mdir / (p.name + ".998.cafebabe.tmp")
    old.write_bytes(b"x")
    fresh.write_bytes(b"y")
    t_old = time.time() - klines.TMP_MAX_AGE_S - 60
    import os
    os.utime(old, (t_old, t_old))
    klines._atomic_parquet(bars(1, 5, 2), p)
    assert not old.exists() and fresh.exists()                             # чужой свежий (идущая запись) не трогаем


def test_concurrent_atomic_writers_do_not_collide(mdir):
    p = klines._path("BTCUSDT", "1h")
    errors = []

    def writer(seed):
        try:
            for _ in range(15):
                klines._atomic_parquet(bars(1, 200 + seed, seed), p)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=writer, args=(s,)) for s in (1, 2, 3, 4)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert not errors
    assert len(pd.read_parquet(p)) in (201, 202, 203, 204)
    assert [x.name for x in mdir.iterdir()] == [p.name]


# ---------------------------------------------------------------- защита путей
@pytest.mark.parametrize("sym", ["..\\x", "\\\\host\\share", "../../etc", "BTC/USDT", "btcusdt", "BTCUSDT\n", "", "A" * 40,
                                 "BTCUSDT\x00"])
def test_path_rejects_hostile_symbol(sym):
    with pytest.raises(ValueError):
        klines._path(sym, "1h")


@pytest.mark.parametrize("tf", ["..\\1h", "1H", "", "1h\x00", "../1h", "7m"])
def test_path_rejects_hostile_tf(tf):
    with pytest.raises(ValueError):
        klines._path("BTCUSDT", tf)


def test_incremental_rejects_hostile_name(mdir):
    with pytest.raises(ValueError):
        derivs._incremental("..\\evil", lambda a, b: pd.DataFrame(), 10, True)
    with pytest.raises(ValueError):
        derivs.funding("..\\x", 10, True)


# ---------------------------------------------------------------- load: блокировка и пустые ответы
def _seed(mdir, n=1500):
    p = klines._path("BTCUSDT", "1h")
    klines._atomic_parquet(bars(1, n, 7), p)
    return p


def test_load_busy_file_returns_existing_without_network(mdir, monkeypatch):
    p = _seed(mdir)
    calls = []
    monkeypatch.setattr(klines, "_fetch_range", lambda *a, **k: calls.append(1) or pd.DataFrame())
    monkeypatch.setattr(guard, "LOCK_TIMEOUT_S", 0.2)
    t = _hold(p, 1.5)
    t0 = time.monotonic()
    df = klines.load("BTCUSDT", "1h", 365, refresh=True)
    assert time.monotonic() - t0 < 1.2, "бот не должен ждать долгую веб-докачку"
    assert calls == [] and len(df) == 1500
    t.join(5)


def test_load_busy_without_local_file_raises_lock_timeout(mdir, monkeypatch):
    p = klines._path("BTCUSDT", "1h")
    monkeypatch.setattr(guard, "LOCK_TIMEOUT_S", 0.1)
    t = _hold(p, 0.8)
    with pytest.raises(guard.LockTimeout):
        klines.load("BTCUSDT", "1h", 365)
    t.join(5)


def test_load_patient_waits_for_lock_then_updates(mdir, monkeypatch):
    p = _seed(mdir)
    calls = []
    monkeypatch.setattr(klines, "_fetch_range", lambda *a, **k: calls.append(1) or pd.DataFrame())
    monkeypatch.setattr(guard, "LOCK_TIMEOUT_S", 0.05)
    t = _hold(p, 0.5)
    with guard.patient(10):
        df = klines.load("BTCUSDT", "1h", 365, refresh=True)
    assert calls and len(df) == 1500                                         # дождался и выполнил обычную докачку
    t.join(5)


def test_load_empty_everywhere_is_clear_error(mdir, monkeypatch):
    monkeypatch.setattr(klines, "_fetch_range", lambda *a, **k: pd.DataFrame())
    with pytest.raises(klines.NoDataError, match="не вернул свечи"):
        klines.load("BTCUSDT", "1h", 30)
    assert not klines._path("BTCUSDT", "1h").exists()


def test_load_keeps_numeric_output(mdir, monkeypatch):
    """Блокировки и журнал пропусков числа не меняют: load отдаёт те же свечи, что лежат в файле."""
    p = _seed(mdir)
    monkeypatch.setattr(klines, "_fetch_range", lambda *a, **k: pd.DataFrame())
    ref = pd.read_parquet(p)
    out = klines.load("BTCUSDT", "1h", 1095, refresh=True)
    pd.testing.assert_frame_equal(out, ref, check_freq=False)


def test_incremental_busy_and_nodata(mdir, monkeypatch):
    p = mdir / "BTCUSDT_oi_1h.parquet"
    idx = pd.date_range("2026-05-01", periods=50, freq="h", tz="UTC").rename("ts")
    klines._atomic_parquet(pd.DataFrame({"oi": np.arange(50.0)}, index=idx), p)
    calls = []
    fetch = lambda a, b: calls.append(1) or pd.DataFrame()                    # noqa: E731
    monkeypatch.setattr(guard, "LOCK_TIMEOUT_S", 0.1)
    t = _hold(p, 0.8)
    out = derivs._incremental("BTCUSDT_oi_1h", fetch, 30, True)
    assert calls == [] and len(out) == 50                                      # писатель пропустил докачку и отдал файл
    t.join(5)
    assert len(derivs._incremental("BTCUSDT_oi_1h", fetch, 30, False)) == 50  # чтение без refresh — без сети
    with pytest.raises(klines.NoDataError):
        derivs._incremental("BTCUSDT_new_1h", fetch, 30, True)                 # раньше: ValueError «No objects to concatenate»


def test_refresh_false_read_does_not_wait_for_lock(mdir, monkeypatch):
    p = mdir / "BTCUSDT_oi_1h.parquet"
    idx = pd.date_range("2026-05-01", periods=20, freq="h", tz="UTC").rename("ts")
    klines._atomic_parquet(pd.DataFrame({"oi": np.arange(20.0)}, index=idx), p)
    monkeypatch.setattr(guard, "LOCK_TIMEOUT_S", 30)
    t = _hold(p, 0.8)
    t0 = time.monotonic()
    assert len(derivs._incremental("BTCUSDT_oi_1h", lambda a, b: pd.DataFrame(), 30, False)) == 20
    assert time.monotonic() - t0 < 0.5
    t.join(5)


# ---------------------------------------------------------------- отмена
def test_cancel_interrupts_fetch_before_any_request(monkeypatch):
    called = []
    monkeypatch.setattr(klines.requests, "get", lambda *a, **k: called.append(1))
    ev = threading.Event()
    ev.set()
    with guard.bind_cancel(ev):
        with pytest.raises(guard.Cancelled):
            klines._fetch_range("BTCUSDT", "1h", 0, 10_000_000)
    assert called == []


def test_cancel_interrupts_retry_sleep(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("нет сети")
    monkeypatch.setattr(klines.requests, "get", boom)
    ev = threading.Event()
    res = {}

    def run():
        with guard.bind_cancel(ev):
            try:
                klines._fetch_range("BTCUSDT", "1h", 0, 10_000_000)
            except BaseException as e:  # noqa: BLE001
                res["e"] = e
    t = threading.Thread(target=run)
    t0 = time.monotonic()
    t.start()
    time.sleep(0.3)                    # поток уже в паузе перед повтором (1.5 с)
    ev.set()
    t.join(3)
    assert not t.is_alive() and isinstance(res.get("e"), guard.Cancelled)
    assert time.monotonic() - t0 < 1.4


def test_cancel_interrupts_derivs_paginate(monkeypatch):
    monkeypatch.setattr(derivs.requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("x")))
    guard.request_shutdown()
    try:
        with pytest.raises(guard.Cancelled):
            derivs._paginate("open-interest", {}, "timestamp", 0, 10_000_000)
    finally:
        guard._shutdown.clear()


def test_cancel_flag_is_per_thread():
    ev = threading.Event()
    ev.set()
    seen = {}
    with guard.bind_cancel(ev):
        t = threading.Thread(target=lambda: seen.setdefault("c", guard.cancelled()))
        t.start()
        t.join()
        assert guard.cancelled() is True
    assert seen["c"] is False and guard.cancelled() is False


def test_lock_wait_is_cancellable(mdir):
    p = klines._path("BTCUSDT", "1h")
    holder = _hold(p, 1.5)
    ev = threading.Event()
    res = {}

    def run():
        with guard.bind_cancel(ev):
            try:
                with guard.locked(p, timeout=30):
                    res["got"] = True
            except guard.Cancelled as e:
                res["e"] = e
    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.2)
    ev.set()
    t.join(2)
    assert not t.is_alive() and "e" in res
    holder.join(5)


# ---------------------------------------------------------------- C7: пропуски (журнал, числа те же)
def test_find_gaps_and_warn_once(caplog):
    idx = pd.DatetimeIndex(pd.date_range("2026-01-01", periods=10, freq="h", tz="UTC").delete([3, 4, 7]))
    gaps = klines.find_gaps(idx, 60)
    assert [g[2] for g in gaps] == [2, 1]
    assert klines.find_gaps(pd.date_range("2026-01-01", periods=10, freq="h", tz="UTC"), 60) == []
    klines._gap_seen.clear()
    with caplog.at_level(logging.WARNING, logger="tradebot.market"):
        klines.warn_gaps("klines X 1h", gaps)
        klines.warn_gaps("klines X 1h", gaps)                              # то же состояние — второй раз молчим
    assert len([r for r in caplog.records if "пропуски" in r.getMessage()]) == 1
    caplog.clear()
    klines.warn_gaps("klines X 1h", [])
    with caplog.at_level(logging.WARNING, logger="tradebot.market"):
        klines.warn_gaps("klines X 1h", gaps)                              # после «починки» и нового пропуска — снова пишем
    assert len(caplog.records) == 1


def test_load_logs_gap_but_returns_the_same_data(mdir, monkeypatch, caplog):
    b = bars(1, 600, 3)
    holed = b.drop(b.index[[100, 101, 300]])
    p = klines._path("BTCUSDT", "1h")
    klines._atomic_parquet(holed, p)
    monkeypatch.setattr(klines, "_fetch_range", lambda *a, **k: pd.DataFrame())
    with caplog.at_level(logging.WARNING, logger="tradebot.market"):
        out = klines.load("BTCUSDT", "1h", 1095, refresh=True)
    assert any("пропуски" in r.getMessage() and "3 баров" in r.getMessage() for r in caplog.records)
    pd.testing.assert_frame_equal(out, holed, check_freq=False)                              # лог-only: данные не «починены»


# ---------------------------------------------------------------- C7: протянутые вперёд ряды
def test_enrich_warns_on_stale_oi_and_keeps_values(mdir, monkeypatch, caplog):
    df = bars(1, 200, 5)
    idx_oi = pd.date_range(df.index[0], periods=120, freq="h", tz=df.index.tz).rename("ts")      # OI кончается намного раньше свечей
    oi = pd.Series(np.arange(120.0) + 1, index=idx_oi, name="oi")
    lsr = pd.Series(np.full(120, 1.1), index=idx_oi, name="ratio")
    fidx = pd.date_range(df.index[0], periods=40, freq="8h", tz=df.index.tz).rename("ts")
    f = pd.Series(np.full(40, 1e-4), index=fidx, name="rate")
    monkeypatch.setattr(derivs, "funding", lambda *a, **k: f)
    monkeypatch.setattr(derivs, "open_interest", lambda *a, **k: oi)
    monkeypatch.setattr(derivs, "long_short_ratio", lambda *a, **k: lsr)
    with caplog.at_level(logging.WARNING, logger="tradebot.market"):
        out = derivs.enrich(df, "BTCUSDT", refresh=False)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("oi_1h" in m and "протянуты" in m for m in msgs)
    # значения те же, что дал бы прежний reindex(ffill)
    close_t = df.index + pd.Timedelta(hours=1)
    ref = oi.reindex(close_t, method="ffill").set_axis(df.index)
    pd.testing.assert_series_equal(out["oi"], ref, check_names=False)
    assert out["oi"].iloc[-1] == 120.0


def test_enrich_fresh_data_is_silent(mdir, monkeypatch, caplog):
    df = bars(1, 100, 6)
    idx = pd.date_range(df.index[0], periods=102, freq="h", tz=df.index.tz).rename("ts")
    monkeypatch.setattr(derivs, "funding", lambda *a, **k: pd.Series(1e-4, index=pd.date_range(df.index[0] - pd.Timedelta(hours=8), periods=20, freq="8h", tz=df.index.tz), name="rate"))
    monkeypatch.setattr(derivs, "open_interest", lambda *a, **k: pd.Series(np.arange(102.0), index=idx, name="oi"))
    monkeypatch.setattr(derivs, "long_short_ratio", lambda *a, **k: pd.Series(1.0, index=idx, name="ratio"))
    with caplog.at_level(logging.WARNING, logger="tradebot.market"):
        derivs.enrich(df, "BTCUSDT", refresh=False)
    assert not [r for r in caplog.records if "протянуты" in r.getMessage()]
