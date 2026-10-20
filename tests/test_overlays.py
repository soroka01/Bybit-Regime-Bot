"""Оверлеи сигнала: дневной трендовый фильтр (htf_filter) и трейлинг-стоп по ATR (atr_stop)."""
import numpy as np
import pandas as pd
import pytest

from tradebot.core.indicators import atr, ema
from tradebot.core.overlays import apply, atr_stop, htf_filter


def hourly(days, seed=1, drift=0.0, vol=0.004):
    idx = pd.date_range("2026-01-01", periods=24 * days, freq="1h", tz="UTC")
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, vol, len(idx))))
    return pd.DataFrame({"open": c, "high": c * 1.002, "low": c * 0.998, "close": c}, index=idx)


def trend_frame(n=30):
    """30 дней роста, 30 дней падения, 30 дней роста (почасовые бары, плавно): дневная EMA(n) однозначно делит режимы."""
    seg = [np.linspace(100, 160, 24 * 30), np.linspace(160, 90, 24 * 30), np.linspace(90, 150, 24 * 30)]
    c = np.concatenate(seg)
    idx = pd.date_range("2026-01-01", periods=len(c), freq="1h", tz="UTC")
    return pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c}, index=idx)


def expected_bull(df, n):
    """Независимая реализация: режим бара = (закрытие ВЧЕРАШНЕГО дня > EMA(n) на вчерашнем закрытии); NaN — прогрев."""
    daily = df["close"].resample("1D").last()
    e = ema(daily, n)
    e.iloc[: n - 1] = np.nan
    state = (daily > e).astype(float).where(e.notna())
    prev_day = df.index.floor("D") - pd.Timedelta(days=1)
    return pd.Series(state.reindex(prev_day).to_numpy(), index=df.index)


# ---------------------------------------------------------------- htf_filter
def test_htf_bull_allows_only_longs_bear_only_shorts_warmup_zero():
    n = 10
    df = trend_frame()
    rng = np.random.default_rng(4)
    sig = pd.Series(rng.choice([-1.0, -0.5, 0.0, 0.5, 1.0], len(df)), index=df.index)
    out = htf_filter(df, sig, n)
    bull = expected_bull(df, n)
    assert (bull == 1).sum() > 500 and (bull == 0).sum() > 500 and bull.isna().sum() > 0        # все три режима представлены
    assert (out[bull == 1] >= 0).all() and (out[bull == 0] <= 0).all()
    assert (out[bull.isna()] == 0).all()
    # в быке длинные сигналы проходят без изменений, короткие обнуляются (раньше шорты в быке пропускались)
    b = bull == 1
    assert (out[b & (sig > 0)] == sig[b & (sig > 0)]).all() and (out[b & (sig < 0)] == 0).all()
    r = bull == 0
    assert (out[r & (sig < 0)] == sig[r & (sig < 0)]).all() and (out[r & (sig > 0)] == 0).all()


def test_htf_constant_short_signal_is_blocked_in_bull_and_constant_long_in_bear():
    n = 10
    df = trend_frame()
    bull = expected_bull(df, n)
    short = htf_filter(df, pd.Series(-1.0, index=df.index), n)
    long_ = htf_filter(df, pd.Series(1.0, index=df.index), n)
    assert (short[bull == 1] == 0).all() and (short[bull == 0] == -1).all()
    assert (long_[bull == 0] == 0).all() and (long_[bull == 1] == 1).all()


def test_htf_warmup_first_n_days_is_flat():
    n = 20
    df = trend_frame()
    out = htf_filter(df, pd.Series(1.0, index=df.index), n)
    assert (out[: 24 * n] == 0).all()                 # нет вчерашней EMA(n): позиции нет
    assert out.iloc[24 * (n + 5)] == 1.0              # рост, EMA прогрета -> лонг разрешён


def test_htf_has_no_lookahead_future_days_do_not_change_past_output():
    n = 10
    df = hourly(120, seed=2)
    rng = np.random.default_rng(9)
    sig = pd.Series(rng.choice([-1.0, 0.0, 1.0], len(df)), index=df.index)
    base = htf_filter(df, sig, n)
    cut = pd.Timestamp("2026-03-15", tz="UTC")                 # граница суток
    poisoned = df.copy()
    future = poisoned.index >= cut
    poisoned.loc[future, ["open", "high", "low", "close"]] *= 7.0 * (1 + np.sin(np.arange(future.sum()))[:, None] * 0.3)
    out = htf_filter(poisoned, sig, n)
    # бар суток `cut` использует закрытие cut-1 (не отравлено), поэтому совпадать должно всё до конца суток cut включительно
    same_until = cut + pd.Timedelta(days=1)
    assert (base[base.index < same_until] == out[out.index < same_until]).all()
    assert not (base[base.index >= same_until] == out[out.index >= same_until]).all()          # проверка, что отравление что-то меняет


def test_htf_signal_alignment_and_nan_signal():
    df = trend_frame()
    sig = pd.Series(1.0, index=df.index)
    sig.iloc[100] = np.nan
    out = htf_filter(df, sig, 10)
    assert out.index.equals(df.index)


# ---------------------------------------------------------------- atr_stop
def old_atr_stop(df, sig, mult, n=14):
    """Прежняя реализация (ключ — само значение сигнала): эталон поведения для сигналов ±1."""
    s = sig.fillna(0).to_numpy()
    c = df["close"].to_numpy()
    a = atr(df, n).to_numpy()
    out = np.zeros(len(s))
    pos = blocked = peak = risk = 0.0
    for i in range(len(s)):
        want = s[i]
        if want != blocked:
            blocked = 0.0
        if blocked != 0.0 and want == blocked:
            want = 0.0
        if want != pos:
            pos = want
            if pos != 0 and not np.isnan(a[i]):
                peak, risk = c[i], mult * a[i]
            elif pos != 0:
                pos = 0.0
        if pos > 0:
            peak = max(peak, c[i])
            if c[i] < peak - risk:
                blocked, pos = 1.0, 0.0
        elif pos < 0:
            peak = min(peak, c[i])
            if c[i] > peak + risk:
                blocked, pos = -1.0, 0.0
        out[i] = pos
    return pd.Series(out, index=df.index)


@pytest.mark.parametrize("seed,mult", [(1, 2.0), (2, 3.0), (3, 1.5), (4, 4.0)])
def test_atr_stop_unchanged_for_unit_signals(seed, mult):
    df = hourly(40, seed=seed, vol=0.01)
    rng = np.random.default_rng(seed)
    raw = np.repeat(rng.choice([-1.0, 0.0, 1.0], 120), 8)[: len(df)]
    sig = pd.Series(raw, index=df.index)
    new = atr_stop(df, sig, mult)
    assert new.equals(old_atr_stop(df, sig, mult))
    assert (new != sig).any() and (new == sig).any()           # стоп реально срабатывает, но не всегда


def drop_frame(n_up=40, n_down=40):
    """Рост на n_up баров, затем падение: стоп срабатывает на падении. high/low дают небольшой ATR."""
    c = np.r_[np.linspace(100, 120, n_up), np.linspace(120, 80, n_down)]
    idx = pd.date_range("2026-01-01", periods=len(c), freq="1h", tz="UTC")
    return pd.DataFrame({"open": c, "high": c * 1.002, "low": c * 0.998, "close": c}, index=idx), n_up


def test_atr_stop_fractional_long_blocks_until_signal_changes():
    df, k = drop_frame()
    sig = pd.Series(0.5, index=df.index)
    out = atr_stop(df, sig, 3.0)
    stop_at = int(np.flatnonzero((out == 0).to_numpy() & (np.arange(len(df)) > 20))[0])
    assert k < stop_at < len(df) - 5
    assert (out.iloc[20:stop_at] == 0.5).all()                  # до стопа держит дробную позицию как есть
    assert (out.iloc[stop_at:] == 0).all(), "после стопа тот же дробный сигнал не должен сразу возвращаться (раньше возвращался)"


def test_atr_stop_fractional_unblocks_after_flat_and_reenters():
    df, _ = drop_frame(40, 40)
    s = np.full(len(df), 0.5)
    s[60:64] = 0.0                                              # сигнал на время пропал -> блокировка снимается
    out = atr_stop(df, pd.Series(s, index=df.index), 3.0)
    assert out.iloc[50:64].eq(0).all()                          # стоп сработал раньше и держал до пропажи сигнала
    assert out.iloc[64] == 0.5                                  # возобновлённый сигнал снова открывает позицию


def test_atr_stop_magnitude_changes_do_not_reset_trailing_peak():
    """Дробный размер меняется каждую свечу: экстремум не должен сбрасываться (раньше сбрасывался -> стоп не срабатывал)."""
    df, _ = drop_frame(30, 50)
    rng = np.random.default_rng(5)
    sig = pd.Series(rng.uniform(0.2, 0.9, len(df)), index=df.index)
    out = atr_stop(df, sig, 3.0)
    assert (out.iloc[-10:] == 0).all()                          # на затяжном падении стоп сработал и держит
    ref = atr_stop(df, pd.Series(1.0, index=df.index), 3.0)
    assert ((out == 0) == (ref == 0)).all()                     # те же моменты выхода, что у сигнала +1
    held = out != 0
    assert (out[held] == sig[held]).all()                       # внутри позиции размер сигнала сохраняется


def test_atr_stop_fractional_short_symmetric():
    df, _ = drop_frame()
    mirror = df.copy()
    mirror[["open", "close"]] = 200 - df[["open", "close"]]
    mirror["high"], mirror["low"] = 200 - df["low"], 200 - df["high"]
    out_long = atr_stop(df, pd.Series(0.5, index=df.index), 3.0)
    out_short = atr_stop(mirror, pd.Series(-0.5, index=df.index), 3.0)
    assert ((out_long == 0) == (out_short == 0)).all()
    assert (out_short.abs() == out_long).all() and (out_short <= 0).all()


def test_atr_stop_atr_warmup_nan_is_flat_not_crash():
    df, _ = drop_frame()
    d2 = df.copy()
    d2.iloc[:3, d2.columns.get_loc("high")] = np.nan
    out = atr_stop(d2, pd.Series(1.0, index=d2.index), 3.0)
    assert len(out) == len(d2)


def test_apply_combines_overlays_in_order():
    df = trend_frame()
    sig = pd.Series(1.0, index=df.index)
    assert apply(df, sig).equals(sig)
    assert apply(df, sig, htf=10).equals(htf_filter(df, sig, 10))
    assert apply(df, sig, htf=10, stop=3.0).equals(atr_stop(df, htf_filter(df, sig, 10), 3.0))
