"""Реестр стратегий. Каждая: fn(df, **params) -> Series целевой позиции (+1 лонг, -1 шорт, 0 вне рынка)
и сетка параметров для перебора."""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from tradebot.core.indicators import (adx, atr, bollinger, donchian_trail_state, ema, hold, kama, macd, parabolic_sar, rsi, sma, supertrend,
                                       timed_hold, true_range, vol_scale)


@dataclass
class Strategy:
    name: str
    desc: str
    fn: Callable[..., pd.Series]
    grid: dict[str, list]
    default: dict
    derivative: bool = False   # нужны колонки funding/funding_last/oi/lsr (веб-слой добавляет такие в DERIV_STRATEGIES)

    def combos(self):
        keys = list(self.grid)
        for vals in itertools.product(*(self.grid[k] for k in keys)):
            yield dict(zip(keys, vals))


def ema_cross(df, fast, slow):
    if fast >= slow:
        return pd.Series(0.0, index=df.index)
    c = df["close"]
    return np.sign(ema(c, fast) - ema(c, slow))


def sma_cross(df, fast, slow):
    if fast >= slow:
        return pd.Series(0.0, index=df.index)
    c = df["close"]
    return np.sign(sma(c, fast) - sma(c, slow))


def rsi_reversion(df, n, lo, hi):
    r = rsi(df["close"], n)
    long_ = hold(r < lo, r > 50)
    short = hold(r > hi, r < 50)
    return long_ - short


def rsi_trend(df, n, level):
    r = rsi(df["close"], n)
    long_ = hold(r > 50 + level, r < 50)
    short = hold(r < 50 - level, r > 50)
    return long_ - short


def macd_cross(df, fast, slow, signal):
    if fast >= slow:
        return pd.Series(0.0, index=df.index)
    line, sig = macd(df["close"], fast, slow, signal)
    return np.sign(line - sig)


def bb_reversion(df, n, k):
    c = df["close"]
    mid, up, lo = bollinger(c, n, k)
    return hold(c < lo, c > mid) - hold(c > up, c < mid)


def bb_breakout(df, n, k):
    c = df["close"]
    mid, up, lo = bollinger(c, n, k)
    return hold(c > up, c < mid) - hold(c < lo, c > mid)


def donchian(df, n_in, n_out):
    c = df["close"]
    hi_in, lo_in = df["high"].rolling(n_in).max().shift(), df["low"].rolling(n_in).min().shift()
    hi_out, lo_out = df["high"].rolling(n_out).max().shift(), df["low"].rolling(n_out).min().shift()
    return hold(c > hi_in, c < lo_out) - hold(c < lo_in, c > hi_out)


def supertrend_s(df, n, mult):
    return supertrend(df, n, mult)


def keltner_breakout(df, n, mult):
    c = df["close"]
    mid = ema(c, n)
    a = atr(df, n)
    up, lo = mid + mult * a, mid - mult * a
    return hold(c > up, c < mid) - hold(c < lo, c > mid)


def momentum(df, n):
    return np.sign(df["close"] / df["close"].shift(n) - 1).fillna(0)


def trend_pullback(df, trend, n, lo):
    """Покупаем просадку RSI только по тренду (цена выше EMA trend). Только лонг."""
    c = df["close"]
    r = rsi(c, n)
    return hold((c > ema(c, trend)) & (r < lo), r > 55)


def ema_adx(df, fast, slow, adx_min):
    """EMA-кросс, но торгуем только при сильном тренде (ADX > adx_min)."""
    if fast >= slow:
        return pd.Series(0.0, index=df.index)
    c = df["close"]
    d = np.sign(ema(c, fast) - ema(c, slow))
    return d.where(adx(df, 14) > adx_min, 0.0)


E4 = 1e-4  # 0.01% — типичный «нейтральный» фандинг за 8ч


def funding_contrarian(df, hi, lo):
    """Против толпы: фандинг сильно положительный (все в лонгах) -> шорт; сильно отрицательный -> лонг."""
    f = df["funding_last"] * 1.0
    short = hold(f > hi * E4, f < hi * E4 / 2)
    long_ = hold(f < lo * E4, f > lo * E4 / 2)
    return long_ - short


def lsr_contrarian(df, n, k):
    """Против толпы по соотношению лонг/шорт аккаунтов (z-score)."""
    r = df["lsr"]
    z = (r - r.rolling(n).mean()) / r.rolling(n).std()
    return hold(z < -k, z > 0) - hold(z > k, z < 0)


def oi_trend(df, n):
    """Рост цены при росте open interest — лонг (деньги заходят), падение при росте OI — шорт."""
    up = df["close"] > df["close"].shift(n)
    oi_up = df["oi"] > df["oi"].shift(n)
    return (up & oi_up).astype(float) - ((~up) & oi_up).astype(float)


def oi_flush(df, n, drop, hold_bars):
    """Резкое падение OI вместе с ценой (каскад ликвидаций) — покупаем отскок на hold_bars свечей."""
    flush = (df["oi"] / df["oi"].shift(n) - 1 < -drop / 100) & (df["close"] < df["close"].shift(n))
    return flush.astype(float).rolling(hold_bars, min_periods=1).max()


STRATEGIES: dict[str, Strategy] = {s.name: s for s in [
    Strategy("EMA cross", "Лонг когда быстрая EMA выше медленной, иначе шорт",
             ema_cross, {"fast": [5, 8, 12, 20, 30, 50], "slow": [21, 34, 50, 89, 100, 200]}, {"fast": 12, "slow": 50}),
    Strategy("SMA cross", "То же на простых средних (golden/death cross)",
             sma_cross, {"fast": [10, 20, 30, 50], "slow": [50, 100, 150, 200]}, {"fast": 50, "slow": 200}),
    Strategy("RSI reversion", "Покупка перепроданности, шорт перекупленности, выход у RSI 50",
             rsi_reversion, {"n": [7, 14, 21], "lo": [20, 25, 30, 35], "hi": [65, 70, 75, 80]}, {"n": 14, "lo": 30, "hi": 70}),
    Strategy("RSI trend", "Вход по импульсу: RSI выше 50+level — лонг, ниже 50-level — шорт",
             rsi_trend, {"n": [7, 14, 21], "level": [5, 10, 15]}, {"n": 14, "level": 10}),
    Strategy("MACD cross", "Пересечение линии MACD и сигнальной",
             macd_cross, {"fast": [8, 12, 16], "slow": [21, 26, 35], "signal": [5, 9, 12]}, {"fast": 12, "slow": 26, "signal": 9}),
    Strategy("Bollinger reversion", "Возврат к средней от границ полос Боллинджера",
             bb_reversion, {"n": [14, 20, 30, 50], "k": [1.5, 2.0, 2.5, 3.0]}, {"n": 20, "k": 2.0}),
    Strategy("Bollinger breakout", "Пробой полосы Боллинджера, выход у средней",
             bb_breakout, {"n": [14, 20, 30, 50], "k": [1.5, 2.0, 2.5, 3.0]}, {"n": 20, "k": 2.0}),
    Strategy("Donchian breakout", "Пробой N-свечного максимума/минимума (turtle), выход по меньшему каналу",
             donchian, {"n_in": [10, 20, 40, 55, 100], "n_out": [5, 10, 20, 40]}, {"n_in": 20, "n_out": 10}),
    Strategy("Supertrend", "Следование за трендом по ATR-каналу",
             supertrend_s, {"n": [7, 10, 14, 21], "mult": [1.5, 2.0, 3.0, 4.0, 5.0]}, {"n": 10, "mult": 3.0}),
    Strategy("Keltner breakout", "Пробой канала Кельтнера (EMA ± ATR)",
             keltner_breakout, {"n": [10, 20, 40], "mult": [1.0, 1.5, 2.0, 3.0]}, {"n": 20, "mult": 2.0}),
    Strategy("Momentum (ROC)", "Знак доходности за N свечей",
             momentum, {"n": [6, 12, 24, 48, 96, 168, 336]}, {"n": 48}),
    Strategy("Trend pullback", "Покупка просадки RSI по восходящему тренду (EMA), только лонг",
             trend_pullback, {"trend": [50, 100, 200], "n": [2, 5, 14], "lo": [20, 30, 40]}, {"trend": 200, "n": 5, "lo": 30}),
    Strategy("EMA + ADX filter", "EMA-кросс только при сильном тренде (ADX)",
             ema_adx, {"fast": [8, 12, 20], "slow": [34, 50, 100], "adx_min": [15, 20, 25, 30]}, {"fast": 12, "slow": 50, "adx_min": 20}),
    Strategy("Funding contrarian", "Против толпы по ставке фандинга (в единицах 0.01%)",
             funding_contrarian, {"hi": [1, 2, 4, 6], "lo": [0, -0.5, -1, -2]}, {"hi": 2, "lo": -0.5}),
    Strategy("Long/short ratio contrarian", "Против толпы по соотношению лонг/шорт аккаунтов",
             lsr_contrarian, {"n": [50, 100, 200, 400], "k": [1.0, 1.5, 2.0]}, {"n": 100, "k": 1.5}),
    Strategy("OI trend", "Тренд подтверждён ростом open interest",
             oi_trend, {"n": [6, 12, 24, 48, 96]}, {"n": 24}),
    Strategy("OI flush", "Покупка отскока после каскада ликвидаций (падение OI + цены), только лонг",
             oi_flush, {"n": [3, 6, 12], "drop": [1, 2, 3, 5], "hold_bars": [3, 6, 12, 24]}, {"n": 6, "drop": 2, "hold_bars": 6}),
]}


# ======================================================================================================
# Расширенный набор стратегий. Сигнал в момент t использует только данные <= t; NaN в прогреве => позиция 0.0.
# ======================================================================================================
POS_CAP = 1.5  # максимум |позиции| у стратегий с таргетом волатильности


def _zeros(df) -> pd.Series:
    return pd.Series(0.0, index=df.index)


def _finish(s: pd.Series, df) -> pd.Series:
    return s.reindex(df.index).astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def tsmom_voltarget(df, h, vt, vol_win):
    """Голосование знаков доходности за h, 3h, 12h свечей; при vt>0 — таргет годовой волатильности vt (кап 1.5)."""
    c = df["close"]
    score = sum(np.sign(c / c.shift(k) - 1) for k in (h, 3 * h, 12 * h)) / 3.0   # NaN, пока нет 12h свечей
    if vt and vt > 0:
        score = (score * vt / vol_scale(df, vol_win)).clip(-POS_CAP, POS_CAP)
    return _finish(score, df)


def donchian_ensemble(df, lo, hi, vt):
    """Ансамбль лонг-Дончиана с трейлинг-стопом по 6 окнам (геометрическая сетка lo..hi); vt>0 — таргет волатильности."""
    c = df["close"]
    lens = sorted({int(round(x)) for x in np.geomspace(lo, hi, 6)})
    pos = pd.Series(sum(donchian_trail_state(c, L) for L in lens) / len(lens), index=df.index)
    if vt and vt > 0:
        pos = (pos * vt / vol_scale(df, 60)).clip(0.0, POS_CAP)
    return _finish(pos, df)


def ma_ribbon(df, base, thr):
    """Голосование знака (close - SMA) по длинам base*[1,2,4,8]; вход при |score|>=thr, выход при пересечении 0."""
    c = df["close"]
    score = sum(np.sign(c - sma(c, base * m)) for m in (1, 2, 4, 8)) / 4.0
    return _finish(hold(score >= thr, score <= 0) - hold(score <= -thr, score >= 0), df)


def extreme_highs(df, n, hold_bars, stop):
    """Лонг на hold_bars свечей после закрытия на максимуме n свечей; опц. трейлинг-стоп stop*ATR(14)."""
    c = df["close"]
    ev = c >= c.rolling(n).max()
    return _finish(timed_hold(ev, hold_bars, c, atr(df, 14), stop), df)


def extreme_lows(df, n, hold_bars, stop):
    """Лонг на hold_bars свечей после закрытия на минимуме n свечей (покупка падения); опц. трейлинг-стоп."""
    c = df["close"]
    ev = c <= c.rolling(n).min()
    return _finish(timed_hold(ev, hold_bars, c, atr(df, 14), stop), df)


def squeeze_breakout(df, n, kc_mult, min_sq):
    """TTM squeeze: Боллинджер(n,2) внутри Кельтнера(EMA n, kc_mult*ATR n); при выходе из сжатия — по стороне от SMA(n)."""
    c = df["close"]
    mid, bb_up, bb_lo = bollinger(c, n, 2.0)
    kc_mid, a = ema(c, n), atr(df, n)
    sq = ((bb_up < kc_mid + kc_mult * a) & (bb_lo > kc_mid - kc_mult * a)).astype(bool)
    run = sq.astype(int).groupby((~sq).cumsum()).cumsum()           # длина текущей серии сжатия
    release = (~sq) & sq.shift(1, fill_value=False) & (run.shift(1, fill_value=0) >= min_sq)
    return _finish(hold(release & (c > mid), c < mid) - hold(release & (c < mid), c > mid), df)


def kama_trend(df, n, fast, slow, er_min):
    """Тренд по адаптивной средней Кауфмана: знак (close - KAMA), только если коэффициент эффективности ER >= er_min."""
    c = df["close"]
    k, er = kama(c, n, fast, slow)
    pos = np.sign(c - k)
    if er_min and er_min > 0:
        pos = pos.where(er >= er_min, 0.0)
    return _finish(pos, df)


def zscore_reversion(df, n, z_in, z_out, trend):
    """Возврат к средней по z-score (close vs SMA n) только по тренду: лонг выше SMA(trend), шорт ниже."""
    c = df["close"]
    sd = c.rolling(n).std(ddof=0).replace(0, np.nan)
    z = (c - sma(c, n)) / sd
    tr = sma(c, trend)
    return _finish(hold((z < -z_in) & (c > tr), z > -z_out) - hold((z > z_in) & (c < tr), z < z_out), df)


def connors_rsi2(df, lo, trend, exit_n):
    """Connors: RSI(2) < lo при close > SMA(trend) — покупка, выход когда close > SMA(exit_n). Только лонг."""
    c = df["close"]
    return _finish(hold((rsi(c, 2) < lo) & (c > sma(c, trend)), c > sma(c, exit_n)), df)


def funding_zscore(df, n, z_hi):
    """Против толпы по z-score ставки фандинга (среднее/std по n свечей): z>z_hi — шорт до z<0, z<-z_hi — лонг до z>0."""
    f = df["funding_last"].astype(float)
    sd = f.rolling(n).std().replace(0, np.nan)
    z = (f - f.rolling(n).mean()) / sd
    return _finish(hold(z < -z_hi, z > 0) - hold(z > z_hi, z < 0), df)


def trend_funding(df, n, f_max):
    """Тренд по EMA(n) с фильтром «не толпа»: лонг при фандинге <= f_max (0.01%), шорт при фандинге >= -f_max."""
    c = df["close"]
    f = df["funding_last"].astype(float)
    e = ema(c, n)
    lim = f_max * E4
    long_ = ((c > e) & (f <= lim)).astype(float)
    short = ((c < e) & (f >= -lim)).astype(float)
    return _finish(long_ - short, df)


def ichimoku(df, tenkan, kijun, senkou_b):
    """Облако Ишимоку без look-ahead: линии Senkou, посчитанные по данным до t-26, смещены вперёд на 26 свечей
    (стандартное «рисование вперёд»); Chikou не используется. Лонг: close выше облака и tenkan>kijun; шорт зеркально."""
    h, l, c = df["high"], df["low"], df["close"]

    def mid(n):
        return (h.rolling(n).max() + l.rolling(n).min()) / 2

    tk, kj = mid(tenkan), mid(kijun)
    sa = ((tk + kj) / 2).shift(26)          # shift(+26): значение на t = расчёт по данным до t-26
    sb = mid(senkou_b).shift(26)
    top, bot = np.maximum(sa, sb), np.minimum(sa, sb)
    long_ = ((c > top) & (tk > kj)).astype(float)
    short = ((c < bot) & (tk < kj)).astype(float)
    return _finish(long_ - short, df)


def parabolic_sar_s(df, af_start, af_step, af_max):
    """Параболический SAR Уайлдера: +1 пока цена выше SAR, -1 пока ниже (всегда в рынке)."""
    return _finish(parabolic_sar(df, af_start, af_step, af_max)[1], df)


def regime_chop(df, n, chop_lo, chop_hi, fast, slow):
    """Переключатель режимов по Choppiness Index: CHOP<lo — тренд (EMA fast/slow), CHOP>hi — возврат (Боллинджер 20,2), иначе 0."""
    if fast >= slow:
        return _zeros(df)
    c = df["close"]
    rng = (df["high"].rolling(n).max() - df["low"].rolling(n).min()).replace(0, np.nan)
    chop = 100 * np.log10(true_range(df).rolling(n).sum() / rng) / np.log10(n)
    trend_pos = np.sign(ema(c, fast) - ema(c, slow))
    mid, up, lo_b = bollinger(c, 20, 2.0)
    rev_pos = hold(c < lo_b, c > mid) - hold(c > up, c < mid)
    trending, ranging = chop < chop_lo, chop > chop_hi
    pos = pd.Series(0.0, index=df.index)
    pos[trending] = trend_pos[trending]
    pos[ranging] = rev_pos[ranging]
    return _finish(pos, df)


def volume_breakout(df, n, k, exit_n):
    """Пробой экстремума close на объёме > k * средний объём (по предыдущим n свечам); выход по EMA(exit_n)."""
    c, v = df["close"], df["volume"]
    hi_, lo_ = c.rolling(n).max().shift(), c.rolling(n).min().shift()
    spike = v > k * v.rolling(n).mean().shift()
    e = ema(c, exit_n)
    return _finish(hold((c > hi_) & spike, c < e) - hold((c < lo_) & spike, c > e), df)


STRATEGIES.update({s.name: s for s in [
    Strategy("TSMOM vol-target", "Голосование моментума за h/3h/12h свечей с таргетом волатильности (Moskowitz/Ooi/Pedersen; Hurst/Ooi/Pedersen)",
             tsmom_voltarget, {"h": [6, 12, 24, 48], "vt": [0.0, 0.3, 0.5], "vol_win": [20, 60]},
             {"h": 24, "vt": 0.5, "vol_win": 60}),
    Strategy("Donchian ensemble (trail)", "Ансамбль из 6 лонг-Дончианов с трейлинг-стопом (Zarattini/Pagani/Barbon), опц. таргет волатильности",
             donchian_ensemble, {"lo": [5, 10, 20], "hi": [60, 120, 250], "vt": [0.0, 0.25, 0.4]},
             {"lo": 10, "hi": 120, "vt": 0.0}),
    Strategy("MA ribbon vote", "Голосование цены против 4 SMA (base x1/2/4/8) с гистерезисом, лонг/шорт",
             ma_ribbon, {"base": [5, 10, 20, 25], "thr": [0.5, 0.75, 1.0]}, {"base": 10, "thr": 0.5}),
    Strategy("Extreme highs", "Лонг на hold_bars свечей после закрытия на максимуме n свечей, опц. ATR-стоп, только лонг",
             extreme_highs, {"n": [10, 20, 30, 50], "hold_bars": [3, 6, 12, 24], "stop": [0.0, 3.0]},
             {"n": 20, "hold_bars": 6, "stop": 0.0}),
    Strategy("Extreme lows", "Покупка падения: лонг на hold_bars свечей после закрытия на минимуме n свечей (осторожно: большие просадки на медвежьем рынке), только лонг",
             extreme_lows, {"n": [10, 20, 30, 50], "hold_bars": [3, 6, 12, 24], "stop": [0.0, 3.0]},
             {"n": 20, "hold_bars": 6, "stop": 0.0}),
    Strategy("Squeeze breakout", "TTM squeeze: выход Боллинджера из канала Кельтнера — вход по стороне от SMA, выход при возврате к средней",
             squeeze_breakout, {"n": [20, 30], "kc_mult": [1.0, 1.5, 2.0], "min_sq": [3, 6]},
             {"n": 20, "kc_mult": 1.5, "min_sq": 3}),
    Strategy("KAMA trend", "Тренд по адаптивной средней Кауфмана с фильтром эффективности (ER)",
             kama_trend, {"n": [10, 20], "fast": [2, 3], "slow": [20, 30, 50], "er_min": [0.0, 0.2, 0.3]},
             {"n": 10, "fast": 2, "slow": 30, "er_min": 0.2}),
    Strategy("Z-score reversion", "Возврат к средней по z-score, но только по направлению тренда (SMA trend)",
             zscore_reversion, {"n": [20, 50, 100], "z_in": [1.5, 2.0, 2.5], "z_out": [0.0, 0.5], "trend": [100, 200]},
             {"n": 20, "z_in": 2.0, "z_out": 0.0, "trend": 200}),
    Strategy("Connors RSI(2)", "Покупка RSI(2) < lo выше долгой SMA, выход выше короткой SMA, только лонг",
             connors_rsi2, {"lo": [5, 10, 15, 25], "trend": [100, 200], "exit_n": [5, 10]},
             {"lo": 10, "trend": 200, "exit_n": 5}),
    Strategy("Funding z-score", "Против толпы по z-score ставки фандинга относительно её недавней истории",
             funding_zscore, {"n": [90, 270, 540], "z_hi": [1.5, 2.0, 2.5]}, {"n": 270, "z_hi": 2.0}, derivative=True),
    Strategy("Trend + funding filter", "Тренд по EMA, но не входим в сторону переполненной толпы по фандингу (в единицах 0.01%)",
             trend_funding, {"n": [50, 100, 200], "f_max": [1.0, 3.0, 6.0]}, {"n": 100, "f_max": 3.0}, derivative=True),
    Strategy("Ichimoku cloud", "Облако Ишимоку (без look-ahead): цена вне облака + tenkan/kijun в ту же сторону",
             ichimoku, {"tenkan": [7, 9, 12], "kijun": [22, 26, 30], "senkou_b": [44, 52, 60]},
             {"tenkan": 9, "kijun": 26, "senkou_b": 52}),
    Strategy("Parabolic SAR", "Параболический SAR Уайлдера: лонг выше SAR, шорт ниже",
             parabolic_sar_s, {"af_start": [0.01, 0.02], "af_step": [0.01, 0.02], "af_max": [0.1, 0.2]},
             {"af_start": 0.02, "af_step": 0.02, "af_max": 0.2}),
    Strategy("Regime switch (CHOP)", "Choppiness Index переключает режим: тренд (EMA) при низком CHOP, возврат (Боллинджер) при высоком",
             regime_chop, {"n": [14, 28], "chop_lo": [38.2, 45.0], "chop_hi": [55.0, 61.8], "fast": [12, 20], "slow": [50, 100]},
             {"n": 14, "chop_lo": 45.0, "chop_hi": 55.0, "fast": 12, "slow": 50}),
    Strategy("Volume breakout", "Пробой экстремума close на повышенном объёме, выход по EMA, лонг/шорт",
             volume_breakout, {"n": [20, 50], "k": [1.5, 2.0, 3.0], "exit_n": [10, 20]},
             {"n": 20, "k": 2.0, "exit_n": 10}),
]})


def _nice_step(diff: float) -> float:
    cands = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0]
    target = diff / 5
    ok = [c for c in cands if c <= target + 1e-12]
    return ok[-1] if ok else cands[0]


def param_specs(st: Strategy) -> dict:
    """Границы слайдеров = сетка стратегии, расширенная в 1.5 раза."""
    out = {}
    for name, grid in st.grid.items():
        vals = sorted(set(grid))
        lo_g, hi_g = vals[0], vals[-1]
        span = hi_g - lo_g
        is_int = all(isinstance(v, int) for v in grid) and isinstance(st.default[name], int)
        lo = lo_g / 1.5 if lo_g > 0 else lo_g - 0.5 * span
        hi = hi_g * 1.5 if hi_g > 0 else hi_g + 0.5 * span
        if is_int:
            lo = max(1, math.floor(lo)) if lo_g > 0 else math.floor(lo)
            hi, step = math.ceil(hi), 1
        else:
            diffs = [b - a for a, b in zip(vals, vals[1:]) if b > a]
            step = _nice_step(min(diffs)) if diffs else 0.1
            lo, hi = round(lo / step) * step, round(hi / step) * step
        out[name] = {"min": round(lo, 6), "max": round(hi, 6), "step": step, "default": st.default[name],
                     "kind": "int" if is_int else "float", "grid": vals}
    return out


def clean_params(st: Strategy, raw: dict) -> dict:
    out = {}
    for name, sp in param_specs(st).items():
        v = raw.get(name, sp["default"])
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            v = sp["default"]
        v = min(max(v, sp["min"]), sp["max"])
        out[name] = int(round(v)) if sp["kind"] == "int" else float(v)
    return out
