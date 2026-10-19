"""Портфель из нескольких «ног» (стратегия × таймфрейм × вес) на одной чистой позиции.

ВАЖНО: эта функция — единственный источник целевой позиции и для бэктеста, и для живого бота.
Сигнал старшего таймфрейма применяется к базовой сетке только с момента закрытия его свечи
(start + tf), поэтому look-ahead исключён.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tradebot.core.strategies import STRATEGIES


@dataclass
class Leg:
    strategy: str
    tf: str
    params: dict
    weight: float
    long_only: bool = False
    warmup: int = 0   # свечей таймфрейма для живого бота; 0 -> WARMUP_BARS по стратегии (у добавленных в боте ног его находит legcheck)

    @property
    def key(self) -> str:
        return f"{self.strategy}@{self.tf}"


# Веса дефолтного портфеля в сумме дают ровно 1.0 и совпадают с живым дефолтом бота (trading/state.py resolve_weights,
# market_feed.cap_total_weight ограничивают сумму 1): |экспозиция| <= 1. OI flush: edge выдохся — нога оставлена
# переключаемой, но с весом 0 (бот её не считает и не тянет под неё производные данные).
DEFAULT_LEGS = [
    Leg("SMA cross", "4h", {"fast": 10, "slow": 100}, 0.5),
    Leg("OI flush", "1h", {"n": 6, "drop": 3.0, "hold_bars": 24}, 0.0, long_only=True),
    Leg("Regime switch (CHOP)", "6h", {"n": 28, "chop_lo": 45.0, "chop_hi": 55.0, "fast": 12, "slow": 100}, 0.5, long_only=False),
]

BASE_TF = "1h"
# Сколько свечей ТАЙМФРЕЙМА ноги нужно живому боту. SMA/OI — чистые окна (300 с запасом); Regime switch содержит
# рекурсию EMA(slow=100) и состояние Боллинджера (hold): 480 6h-свечей (120 дней) дают 0 расхождений с полной историей
# (проверено по всем ~3400 свечам; на 300 — 1 расхождение), см. tests/test_parity.py.
WARMUP_BARS = {"SMA cross": 300, "OI flush": 300, "Regime switch (CHOP)": 480}


def leg_signal(leg: Leg, df: pd.DataFrame) -> pd.Series:
    s = STRATEGIES[leg.strategy].fn(df, **leg.params).fillna(0.0)
    return s.clip(lower=0) if leg.long_only else s


def align(sig: pd.Series, tf_step: pd.Timedelta, base_index: pd.DatetimeIndex, base_step: pd.Timedelta) -> pd.Series:
    """Сигнал, известный на закрытии каждой базовой свечи (start+base_step)."""
    avail = sig.copy()
    avail.index = sig.index + tf_step  # момент, когда сигнал свечи стал известен
    close_t = base_index + base_step
    return avail.reindex(close_t, method="ffill").set_axis(base_index).fillna(0.0)


def exposure(legs: list[Leg], dfs: dict[str, pd.DataFrame], base_tf: str = BASE_TF) -> tuple[pd.Series, pd.DataFrame]:
    """Возвращает (суммарная целевая экспозиция в долях капитала, сигналы по ногам) на сетке base_tf.
    Положительное — лонг, отрицательное — шорт. |exposure| <= sum(weights)."""
    base = dfs[base_tf]
    base_step = base.index.to_series().diff().median()
    parts = {}
    for leg in legs:
        d = dfs[leg.tf]
        step = d.index.to_series().diff().median()
        parts[leg.key] = align(leg_signal(leg, d), step, base.index, base_step)
    sigs = pd.DataFrame(parts)
    expo = sum(sigs[l.key] * l.weight for l in legs)
    return expo, sigs
