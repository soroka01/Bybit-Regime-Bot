"""Проверка ноги, добавляемой в боте, ПЕРЕД тем как ей дадут вес: годится ли она для живого пути.

1) Сигнал — только -1/0/+1. Бот считает размер позиции как |цель| × баланс счёта × плечо в предположении |цель| <= Σвесов <= 1;
   у стратегий с дробными/«перебором» сигналами (TSMOM, ансамбль Дончиана: до ±1.5) это не так, и они же дёргали бы ордера на
   каждый шаг 1/3. Такие стратегии в живого бота не добавляются.
2) Окно. Живой бот считает сигнал по последним N свечам (market_feed.window_for), сайт — по всей истории. У рекурсивных индикаторов
   (EMA, «hold») короткое окно не прогревается, и сигнал молча расходится с бэктестом. Для трёх штатных ног окно подобрано вручную
   (WARMUP_BARS, tests/test_parity.py); для добавленных находим его здесь: по локальной истории (БЕЗ сети) считаем сигнал на всей
   истории и на окнах-кандидатах в выборке точек (случайные + вокруг смен сигнала + последние 100 свечей), берём наименьшее окно,
   где расхождений не больше 1% выборки и НЕТ ни одного среди последних 100 свечей, и ещё одно — для запаса (выборка не
   гарантирует, что редкое расхождение не прячется между точками). У EMA с медленным периодом расхождения бывают только у самых
   пересечений средних (вход смещается на свечу) — их показываем в тексте.
3) Данные. На итоговом окне работает живая проверка market_feed.validate_window (пропуски свечей, NaN в нужных колонках)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tradebot.core.portfolio import Leg, leg_signal
from tradebot.market import derivs, klines
from tradebot.trading.config import SYMBOL
from tradebot.trading.market_feed import FETCH_DAYS, OHLCV, TF_DELTA, FeedError, leg_columns, validate_window

CANDIDATES = (300, 480, 720, 960)  # свечей таймфрейма; 300 — чистые окна (SMA), 480 — как у Regime switch; ограничены докачкой бота
MAX_BAD_SHARE = 0.01             # допустимая доля расхождений в выборке (EMA(200) на коротком окне чуть «плывёт» у пересечений)
N_RANDOM = 100                   # случайных точек сравнения
N_TRANSITION = 80                # точек вокруг смен сигнала (смена и соседние свечи)
N_RECENT = 100                   # последние свечи — самые важные для живого бота
HEADROOM = 1.1                   # докачка бота (FETCH_DAYS) должна покрывать окно с запасом, как в tests/test_sixh_leg.py
MIN_HISTORY_DAYS = 365           # короче история — выборка ничего не доказывает


class CheckError(Exception):
    """Проверку нельзя выполнить (нет локальных данных, мало истории) — это не «стратегия плохая»."""


@dataclass
class LegCheck:
    ok: bool
    warmup: int = 0                  # найденное окно (свечей); 0, если не найдено
    points: int = 0                  # сколько точек сравнили
    bad: int = 0                     # расхождений в выборке на итоговом окне (при ok=False — на самом большом проверенном)
    signal: float | None = None      # сигнал на последней закрытой свече истории
    deriv: bool = False              # нужны производные данные (OI/фандинг/LSR): их сбой замораживает цель всего портфеля
    msg: str = ""
    last_bar: str | None = None      # начало последней свечи локальной истории (ISO): на ней посчитан «сигнал сейчас»


def window_cap(tf: str) -> int:
    """Наибольшее окно, которое живая докачка покрывает с запасом."""
    return int(FETCH_DAYS[tf] * 86400 / TF_DELTA[tf].total_seconds() / HEADROOM)


def load_history(tf: str, cols: set[str]) -> pd.DataFrame:
    """Свечи (и производные колонки, если нужны) из локального кэша. Без сети: нет файла — CheckError."""
    p = klines._path(SYMBOL, tf)
    if not p.exists():
        raise CheckError(f"нет локальной истории {tf}: на сайте нажмите «Подгрузить новые свечи» и повторите")
    df = pd.read_parquet(p)
    if cols - OHLCV:
        gran = derivs.granularity_for(klines.TIMEFRAMES[tf][1])
        need = [f"{SYMBOL}_funding.parquet", f"{SYMBOL}_oi_{gran}.parquet"] + ([f"{SYMBOL}_lsr_{gran}.parquet"] if "lsr" in cols else [])
        miss = [n for n in need if not (klines.DATA_DIR / n).exists()]
        if miss:
            raise CheckError(f"нет локальных деривативных данных ({', '.join(miss)}): загрузите их на сайте и повторите")
        df = derivs.enrich(df, SYMBOL, refresh=False)
    return df


def _sample(full: pd.Series, lo: int, rng: np.random.Generator) -> list[int]:
    """Индексы точек сравнения: последние свечи + окрестности смен сигнала + случайные (всё не раньше lo)."""
    n = len(full)
    recent = set(range(max(lo, n - N_RECENT), n))
    changes = np.flatnonzero(full.diff().fillna(0).to_numpy() != 0)
    changes = changes[changes >= lo]
    if len(changes) > N_TRANSITION // 3:
        changes = rng.choice(changes, N_TRANSITION // 3, replace=False)
    edge = {int(i) + d for i in changes for d in (-1, 0, 1) if lo <= int(i) + d < n}
    rnd = {int(i) for i in rng.choice(np.arange(lo, n), size=min(N_RANDOM, n - lo), replace=False)}
    return sorted(recent | edge | rnd)


def check_leg(leg: Leg, df: pd.DataFrame | None = None, seed: int = 1) -> LegCheck:
    """df — история ноги (в тестах подставляется); иначе читается локальный кэш. Нога уже проверена validate_leg."""
    cols = leg_columns(leg)
    deriv = bool(cols - OHLCV)
    d = df if df is not None else load_history(leg.tf, cols)
    cands = [n for n in CANDIDATES if n <= window_cap(leg.tf)]
    if not cands:
        return LegCheck(False, deriv=deriv, msg=f"докачка бота на {leg.tf} слишком мелкая для окон от {CANDIDATES[0]} свечей")
    need = max(3 * max(cands), int(MIN_HISTORY_DAYS * 86400 / TF_DELTA[leg.tf].total_seconds()))
    if len(d) < need:
        raise CheckError(f"истории {leg.tf} мало для проверки ({len(d)} свечей, нужно не меньше {need}): обновите данные на сайте")
    full = leg_signal(leg, d)
    stamp = d.index[-1].isoformat()
    odd = sorted({float(v) for v in full.unique() if v not in (-1.0, 0.0, 1.0)})
    if odd:
        return LegCheck(False, deriv=deriv, signal=float(full.iloc[-1]), last_bar=stamp,
                        msg=f"сигнал стратегии не только -1/0/+1 (встречается {', '.join(f'{v:g}' for v in odd[:3])}): "
                            f"живой бот рассчитан на целые позиции и такие размеры не поддерживает")
    idx = _sample(full, max(cands) + 1, np.random.default_rng(seed))
    last = float(full.iloc[-1])

    recent_from = len(d) - N_RECENT

    def mismatches(n: int) -> tuple[int, int]:
        """(расхождений в выборке, из них среди последних N_RECENT свечей)."""
        bad = [i for i in idx if leg_signal(leg, d.iloc[i - n + 1:i + 1]).iloc[-1] != full.iloc[i]]
        return len(bad), sum(1 for i in bad if i >= recent_from)

    def acceptable(m: tuple[int, int]) -> bool:
        return m[1] == 0 and m[0] <= MAX_BAD_SHARE * len(idx)

    m = (0, 0)
    for k, n in enumerate(cands):
        m = mismatches(n)
        if not acceptable(m):
            continue
        final, mf = n, m
        if k + 1 < len(cands):
            mn = mismatches(cands[k + 1])
            if acceptable(mn):
                final, mf = cands[k + 1], mn
        try:
            validate_window(d, leg.tf, cols | {"close"}, d.index[-1], final)
        except FeedError as e:
            return LegCheck(False, 0, len(idx), 0, last, deriv, f"данные не проходят живую проверку: {e}", stamp)
        tail = "0 расхождений" if mf[0] == 0 else f"{mf[0]} расхождений у пересечений, вход может сместиться на свечу"
        return LegCheck(True, final, len(idx), mf[0], last, deriv, f"окно {final} свечей воспроизводит бэктест ({tail}; точек: {len(idx)})", stamp)
    return LegCheck(False, 0, len(idx), m[0], last, deriv,
                    f"на окне {cands[-1]} свечей сигнал расходится с бэктестом в {m[0]} из {len(idx)} точек"
                    f"{f', в том числе {m[1]} среди последних свечей' if m[1] else ''}: "
                    f"стратегия зависит от более длинной истории, чем держит живой бот", stamp)
