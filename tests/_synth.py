"""Детерминированный синтетический рынок (фиксированная конечная дата) для тестов веб-бэкенда."""
import numpy as np
import pandas as pd
import pytest

from tradebot.market import klines

END = pd.Timestamp("2026-06-01 00:00", tz="UTC")
NOW = END + pd.Timedelta(hours=3)


def bars(step_h, n, seed):
    idx = pd.date_range(end=END, periods=n, freq=f"{step_h}h").rename("ts")
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.004 * step_h ** 0.5, n)))
    return pd.DataFrame({"open": c, "high": c * 1.003, "low": c * 0.997, "close": c, "volume": 1.0, "turnover": 1.0}, index=idx)


def build_market(d) -> None:
    """BTCUSDT: свечи 1h (3000), 4h (1500) и 6h (1000; нужны ноге Regime switch портфеля по умолчанию),
    OI/LSR 1h и 4h (6h использует гранулярность 4h), фандинг."""
    klines._atomic_parquet(bars(1, 3000, 11), klines._path("BTCUSDT", "1h"))
    klines._atomic_parquet(bars(4, 1500, 12), klines._path("BTCUSDT", "4h"))
    klines._atomic_parquet(bars(6, 1000, 13), klines._path("BTCUSDT", "6h"))
    rng = np.random.default_rng(5)
    for step, names in ((1, ("oi_1h", "lsr_1h")), (4, ("oi_4h", "lsr_4h"))):
        idx = pd.date_range(end=END + pd.Timedelta(hours=step), periods=3000 // step + 40, freq=f"{step}h").rename("ts")
        for name, col in zip(names, ("oi", "ratio")):
            v = 1000 * np.exp(np.cumsum(rng.normal(0, 0.02, len(idx)))) if col == "oi" else np.exp(rng.normal(0, 0.2, len(idx)))
            klines._atomic_parquet(pd.DataFrame({col: v}, index=idx), d / f"BTCUSDT_{name}.parquet")
    fidx = pd.date_range(end=END + pd.Timedelta(hours=8), periods=3400 // 8, freq="8h").rename("ts")
    klines._atomic_parquet(pd.DataFrame({"rate": rng.normal(1e-4, 5e-5, len(fidx))}, index=fidx), d / "BTCUSDT_funding.parquet")


@pytest.fixture
def synth_market(tmp_path, monkeypatch):
    """Подменяет MARKET/RESULTS на временные папки и строит синтетические данные. Возвращает (market_dir, results_dir)."""
    from tradebot.web import api as api_mod, service
    d, res = tmp_path / "market", tmp_path / "results"
    d.mkdir()
    res.mkdir()
    monkeypatch.setattr(klines, "DATA_DIR", d)
    monkeypatch.setattr(service, "MARKET", d)
    monkeypatch.setattr(service, "RESULTS", res)
    service.invalidate_data()
    service._info_cache.clear()
    api_mod._cache.clear()
    build_market(d)
    return d, res
