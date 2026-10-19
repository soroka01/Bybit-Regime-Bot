"""Рыночные данные для сигналов: ТОЛЬКО mainnet (api.bybit.com), как в бэктестах. Только закрытые свечи по серверному времени."""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
import time
from dataclasses import dataclass, field

import httpx
import pandas as pd

from tradebot.core.portfolio import WARMUP_BARS, Leg, exposure
from tradebot.core.strategies import STRATEGIES
from tradebot.market import derivs, klines
from tradebot.trading.config import MAINNET_URL, SYMBOL

log = logging.getLogger("feed")
TF_DELTA = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "6h": pd.Timedelta(hours=6)}  # 6h: старты 00/06/12/18 UTC
BASE = "1h"                       # базовая сетка решений (раннер просыпается на закрытии каждой 1h свечи)
WINDOW = 300                      # окно по умолчанию (свечей на таймфрейм), если стратегии нет в WARMUP_BARS
# Глубина докачки (дней) на таймфрейм: должна покрывать окно (см. window_for): 1h 1080, 4h 540, 6h 600 свечей.
FETCH_DAYS = {"1h": 45, "4h": 90, "6h": 150}
OHLCV = {"open", "high", "low", "close", "volume"}
OI_TIMEOUT = 180.0
OI_POLL = 10.0


class FeedError(Exception):
    pass


@dataclass
class FeedData:
    dfs: dict[str, pd.DataFrame]
    bar: pd.Timestamp  # начало последней закрытой 1h свечи
    oi_ok: bool
    stale: dict[str, str] = field(default_factory=dict)  # таймфрейм (не 1h) -> причина: окно не прошло проверку, его ноги держат прежний сигнал


# ---------- чистые функции ----------
def expected_last_bar(now: pd.Timestamp, tf: str) -> pd.Timestamp:
    step = TF_DELTA[tf]
    return now.floor(step) - step


def closed_only(df: pd.DataFrame, tf: str, now: pd.Timestamp) -> pd.DataFrame:
    return df[df.index + TF_DELTA[tf] <= now]


def window_for(tf: str, legs: list[Leg]) -> int:
    """Сколько последних свечей таймфрейма tf берём в расчёт: максимум прогрева стратегий ног на этом таймфрейме."""
    return max([l.warmup or WARMUP_BARS.get(l.strategy, WINDOW) for l in legs if l.tf == tf] + [WINDOW])


def cap_total_weight(legs: list[Leg]) -> list[Leg]:
    """Страховка: суммарный вес ног не больше 1 (иначе |экспозиция| > 1). Нормировка пропорциональная."""
    tot = sum(max(l.weight, 0.0) for l in legs)
    if tot <= 1.0 + 1e-9:
        return legs
    return [dataclasses.replace(l, weight=l.weight / tot) for l in legs]


def leg_columns(leg: Leg) -> set[str]:
    """Какие производные колонки читает стратегия ноги (по исходнику функции)."""
    src = inspect.getsource(STRATEGIES[leg.strategy].fn)
    cols = {"open", "high", "low", "close", "volume"}
    for name, col in (("oi", "oi"), ("funding_last", "funding_last"), ("funding", "funding_last"), ("lsr", "lsr")):
        if f'"{name}"' in src:
            cols.add(col)
    return cols


def uses_oi(leg: Leg) -> bool:
    return "oi" in leg_columns(leg)


def validate_window(df: pd.DataFrame, tf: str, cols: set[str], expected_last: pd.Timestamp, n: int = WINDOW) -> pd.DataFrame:
    """Окно из n последних свечей: последняя = ожидаемая, без дыр и NaN в используемых колонках."""
    w = df.iloc[-n:]
    if len(w) < n:
        raise FeedError(f"{tf}: мало свечей ({len(w)} < {n})")
    if w.index[-1] != expected_last:
        raise FeedError(f"{tf}: последняя закрытая свеча {w.index[-1]}, ожидалась {expected_last}")
    if not (w.index.to_series().diff().dropna() == TF_DELTA[tf]).all():
        raise FeedError(f"{tf}: пропуски в свечах")
    bad = [c for c in cols if c in w and w[c].isna().any()]
    missing = [c for c in cols if c not in w]
    if bad or missing:
        raise FeedError(f"{tf}: NaN/нет колонок {bad + missing}")
    return w


def compute_target(legs: list[Leg], dfs: dict[str, pd.DataFrame], skip_oi: bool, prev: dict[str, float | None],
                   stale_tfs: frozenset[str] | set[str] = frozenset()
                   ) -> tuple[float | None, dict[str, float | None]]:
    """Целевая экспозиция на последней закрытой 1h свече через portfolio.exposure.
    skip_oi: у OI-ног сигнал не обновляется (берётся предыдущий). stale_tfs: ноги на этих таймфреймах (данные не прошли
    проверку) тоже держат предыдущий сигнал, а если его нет — NaN -> (None, ...), т.е. цель не меняется (НИКОГДА не 0).
    NaN -> (None, ...): держим текущую цель. Суммарный вес ограничен 1 (cap_total_weight)."""
    if not legs:
        return 0.0, {}
    legs = cap_total_weight(legs)
    live = [l for l in legs if l.tf not in stale_tfs]
    row = exposure(live, dfs)[1].iloc[-1] if live else None
    out: dict[str, float | None] = {}
    for l in legs:
        if l.tf in stale_tfs:
            v = prev.get(l.key)
            v = float("nan") if v is None else float(v)
        else:
            v = float(row[l.key])
            if skip_oi and uses_oi(l):
                v = prev.get(l.key)
                v = 0.0 if v is None else float(v)  # нет предыдущего -> 0, об этом уже предупреждено
        out[l.key] = v
    if any(v != v for v in out.values()):
        return None, out
    return float(sum(out[l.key] * l.weight for l in legs)), out


class MarketFeed:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.http = client or httpx.AsyncClient(timeout=10.0)
        self.offset = pd.Timedelta(0)
        self._synced = 0.0

    async def close(self):
        await self.http.aclose()

    async def now(self) -> pd.Timestamp:
        """Серверное время Bybit (mainnet); при сбое — локальное с последним известным смещением."""
        if time.time() - self._synced > 300:
            try:
                t0 = time.time()
                r = await self.http.get(MAINNET_URL + "/v5/market/time")
                js = r.json()
                srv = pd.Timestamp(int(js["result"]["timeNano"]) // 1_000_000, unit="ms", tz="UTC")
                self.offset = srv - (pd.Timestamp(t0 + (time.time() - t0) / 2, unit="s", tz="UTC"))
                self._synced = time.time()
            except Exception as e:
                log.warning("не удалось получить серверное время: %s", type(e).__name__)
        return pd.Timestamp.now(tz="UTC") + self.offset

    @staticmethod
    def _load_sync(specs: dict[str, bool] | None = None) -> dict[str, pd.DataFrame | Exception]:
        """specs: таймфрейм -> нужны ли производные колонки (OI/фандинг/LSR). Ошибка загрузки одного таймфрейма не
        мешает остальным (возвращается как исключение). Свечи — инкрементальный кэш klines.load (1h/4h/6h одинаково);
        производные для 6h берутся с гранулярностью 4h (derivs.GRANULARITY) из уже кэшированных файлов."""
        out: dict[str, pd.DataFrame | Exception] = {}
        for tf, deriv in (specs or {"1h": True, "4h": True}).items():
            try:
                d = klines.load(SYMBOL, tf, FETCH_DAYS[tf], refresh=True)
                out[tf] = derivs.enrich(d, SYMBOL, refresh=True) if deriv else d
            except Exception as e:  # noqa: BLE001
                out[tf] = e
        return out

    async def _wait_oi(self, close_ts: pd.Timestamp) -> bool:
        """Ждём, пока OI-запись на момент закрытия бара появится (публикуется с задержкой)."""
        deadline = time.time() + OI_TIMEOUT
        while True:
            try:
                oi = await asyncio.to_thread(derivs.open_interest, SYMBOL, 1095, "1h", True)
                if len(oi) and oi.index[-1] >= close_ts:
                    return True
            except Exception as e:
                log.warning("OI: %s", type(e).__name__)
            if time.time() >= deadline:
                return False
            await asyncio.sleep(OI_POLL)

    async def fetch(self, legs: list[Leg], tries: int = 4, retry_delay: float = 20.0) -> FeedData:
        """Загружает окна нужных таймфреймов (1h всегда + таймфреймы ног), проверяет каждое; при несовпадении ожидаемой
        свечи повторяет. После последней попытки: нет/некорректно 1h -> FeedError (бар пропущен); некорректен другой
        таймфрейм -> он попадает в FeedData.stale, ноги на нём держат прежний сигнал (runner оповещает)."""
        need_oi = any(uses_oi(l) for l in legs)
        tfs = [BASE] + sorted({l.tf for l in legs} - {BASE}, key=lambda t: TF_DELTA[t])
        for tf in tfs:
            if tf not in TF_DELTA or tf not in FETCH_DAYS:
                raise FeedError(f"таймфрейм {tf} не поддерживается живым ботом")
        cols = {tf: {"close"}.union(*(leg_columns(l) for l in legs if l.tf == tf)) for tf in tfs}
        specs = {tf: bool(cols[tf] - OHLCV) for tf in tfs}
        last: str = ""
        oi_ok = True
        for i in range(tries):
            now = await self.now()
            exp = expected_last_bar(now, BASE)
            if need_oi and i == 0:
                oi_ok = await self._wait_oi(exp + TF_DELTA[BASE])
                now = await self.now()
                new_exp = expected_last_bar(now, BASE)  # после ожидания мог начаться новый бар
                if new_exp != exp:
                    exp, oi_ok = new_exp, False  # OI для нового бара не ждали
            try:
                raw = await asyncio.to_thread(self._load_sync, specs)
            except Exception as e:  # noqa: BLE001
                raw = {tf: e for tf in tfs}
            dfs: dict[str, pd.DataFrame] = {}
            errs: dict[str, str] = {}
            for tf in tfs:
                try:
                    d = raw[tf]
                    if isinstance(d, Exception):
                        raise FeedError(f"{tf}: загрузка не удалась ({type(d).__name__}: {d})")
                    d = closed_only(d, tf, now)
                    dfs[tf] = validate_window(d, tf, cols[tf], expected_last_bar(now, tf), window_for(tf, legs))
                except Exception as e:  # noqa: BLE001
                    errs[tf] = str(e)
            if errs:
                last = "; ".join(f"{e}" for e in errs.values())
                log.warning("загрузка данных (попытка %d/%d): %s", i + 1, tries, last)
            if not errs or (i == tries - 1 and BASE not in errs):
                return FeedData(dfs, dfs[BASE].index[-1], oi_ok, errs)
            if i < tries - 1:
                await asyncio.sleep(retry_delay)
        raise FeedError(last)
