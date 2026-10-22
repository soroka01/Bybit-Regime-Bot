"""Данные рынка: файлы, кэш DataFrame в памяти (LRU по mtime), статус/докачка/загрузка (сеть — только явно),
отпечатки данных для ключей кэша. Папки данных читаются как paths.MARKET / paths.RESULTS в момент вызова (тесты подменяют их)."""
from __future__ import annotations

import math
import stat
import threading
from collections import OrderedDict

import pandas as pd

from tradebot import paths
from tradebot.core.strategies import STRATEGIES
from tradebot.i18n import tr
from tradebot.market import derivs, guard, klines
from tradebot.web import cache
from tradebot.web.schemas import SYMBOLS

# стратегии, которым нужны файлы деривативов (OI, L/S): исторические четыре + те, что помечены derivative=True
DERIV_STRATEGIES = {"Funding contrarian", "Long/short ratio contrarian", "OI trend", "OI flush"}
DERIV_STRATEGIES |= {n for n, s in STRATEGIES.items() if s.derivative}


def fetch_estimate(tf: str) -> dict:
    """Сколько запросов к Bybit нужно для полной загрузки таймфрейма (свечи + деривативы) и сколько это примерно займёт."""
    days = klines.history_days(tf)
    k = klines.estimate_requests(tf, days)
    d = derivs.estimate_requests(klines.TIMEFRAMES[tf][1], days)
    total = k + d
    return {"days": days, "klines_requests": k, "derivs_requests": d, "requests": total,
            "minutes": max(1, math.ceil(total * 0.4 / 60))}


class DataMissing(Exception):
    def __init__(self, symbol: str, tf: str, missing: list[str]):
        self.symbol, self.tf, self.missing = symbol, tf, missing
        self.estimate = fetch_estimate(tf) if tf in klines.TIMEFRAMES else None
        super().__init__(tr("No local data for {symbol} {tf}: {missing}. Download it explicitly (POST /api/data/fetch).").format(
            symbol=symbol, tf=tf, missing=', '.join(missing)))


class DataEmpty(ValueError):
    """Parquet-файл прочитан, но в нём нет строк (или меньше 2) — считать нечего."""
    def __init__(self, name: str):
        super().__init__(tr("the data file is empty (or has fewer than 2 rows): {name}. Download the data again.").format(name=name))


# ---------------------------------------------------------------- данные
def check_sym_tf(symbol: str, tf: str) -> None:
    """Единая проверка символа/таймфрейма перед построением ЛЮБОГО пути файла данных (защита от «..», обратных слэшей, UNC-путей)."""
    if symbol not in SYMBOLS or tf not in klines.TIMEFRAMES:
        raise ValueError(tr("unknown symbol or timeframe"))


def _files(symbol: str, tf: str) -> list:
    check_sym_tf(symbol, tf)
    gran = derivs.granularity_for(klines.TIMEFRAMES[tf][1])
    return [klines._path(symbol, tf), paths.MARKET / f"{symbol}_funding.parquet",
            paths.MARKET / f"{symbol}_oi_{gran}.parquet", paths.MARKET / f"{symbol}_lsr_{gran}.parquet"]


def available(symbol: str, tf: str) -> bool:
    return all(p.exists() for p in _files(symbol, tf))


def data_version() -> str:
    """Дёшево: mtime ВХОДНЫХ файлов — parquet рынка и результатов walk-forward (wf_*) — для ключей кэша/ETag.
    Только обычные файлы: mtime каталогов (например RESULTS/cache при каждом открытии sqlite-WAL) меняется и
    без смены данных, что ломало бы попадания в кэш."""
    parts = []
    for d, pat in ((paths.MARKET, "*.parquet"), (paths.RESULTS, "wf_*")):
        try:
            for p in sorted(d.glob(pat)):
                try:
                    st = p.stat()
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    parts.append(f"{p.name}:{st.st_mtime_ns}")
        except OSError:
            pass
    return "|".join(parts)


DF_CACHE_SIZE = 12      # кадров в памяти (1m/5m-история — сотни тысяч строк)
_df_cache: OrderedDict[tuple[str, str], tuple[tuple, pd.DataFrame]] = OrderedDict()
_df_lock = threading.Lock()


def _df_put(key, sig, df) -> None:
    """Под _df_lock: кладёт кадр и вытесняет самые давно использованные."""
    _df_cache[key] = (sig, df)
    _df_cache.move_to_end(key)
    while len(_df_cache) > DF_CACHE_SIZE:
        _df_cache.popitem(last=False)


def get_df(symbol: str, tf: str) -> pd.DataFrame:
    if symbol not in SYMBOLS or tf not in klines.TIMEFRAMES:
        raise ValueError(tr("unknown symbol or timeframe"))
    files = _files(symbol, tf)
    missing = [p.name for p in files if not p.exists()]
    if missing:
        raise DataMissing(symbol, tf, missing)
    sig = tuple(p.stat().st_mtime_ns for p in files)
    with _df_lock:
        hit = _df_cache.get((symbol, tf))
        if hit and hit[0] == sig:
            _df_cache.move_to_end((symbol, tf))
            return hit[1]
        raw = klines._read_parquet(files[0]).sort_index()   # напрямую, без klines.load (тот может пойти в сеть)
        if len(raw) < 2:
            raise DataEmpty(files[0].name)
        df = derivs.enrich(raw, symbol, refresh=False)
        _df_put((symbol, tf), sig, df)
        return df


def klines_available(symbol: str, tf: str) -> bool:
    return klines._path(symbol, tf).exists()


def get_df_lite(symbol: str, tf: str) -> pd.DataFrame:
    """Полные данные, если есть деривативы; иначе только свечи (без фандинга/OI) — для рейтинга."""
    if symbol not in SYMBOLS or tf not in klines.TIMEFRAMES:
        raise ValueError(tr("unknown symbol or timeframe"))
    if available(symbol, tf):
        return get_df(symbol, tf)
    p = klines._path(symbol, tf)
    if not p.exists():
        raise DataMissing(symbol, tf, [p.name])
    key = (symbol, tf + ":lite")
    sig = (p.stat().st_mtime_ns,)
    with _df_lock:
        hit = _df_cache.get(key)
        if hit and hit[0] == sig:
            _df_cache.move_to_end(key)
            return hit[1]
        df = klines._read_parquet(p).sort_index()
        if len(df) < 2:
            raise DataEmpty(p.name)
        _df_put(key, sig, df)
        return df


def invalidate_data(symbol: str | None = None) -> None:
    """Сбрасывает кэш DataFrame в памяти (по символу или целиком)."""
    with _df_lock:
        for k in [k for k in _df_cache if symbol is None or k[0] == symbol]:
            _df_cache.pop(k, None)


_info_cache: dict = {}


def _file_info(path) -> dict | None:
    """Число строк и первая/последняя метки времени parquet-файла. Читается только индекс, результат кэшируется по mtime+size."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    hit = _info_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    try:
        idx = pd.read_parquet(path, columns=[]).index
    except Exception:  # noqa: BLE001
        return None
    info = {"rows": int(len(idx)), "first": idx[0].isoformat() if len(idx) else None,
            "last": idx[-1].isoformat() if len(idx) else None}
    _info_cache[path] = (key, info)
    return info


def _fp_part(path) -> str:
    i = _file_info(path)
    return "none" if not i else f"{i['rows']}:{i['last']}"


def data_fingerprint(symbol: str, tf: str, strategy: str) -> str:
    """Отпечаток данных, что использует стратегия: свечи, фандинг (он идёт в бэктест) и, для дериватив-стратегий,
    ОБА ряда OI и L/S нужной гранулярности. Консервативно: какие именно колонки читает стратегия, веб-слой не знает
    (core/ не трогаем), поэтому для всех derivative-стратегий берём всё — лишняя инвалидация допустима, недостающая нет.
    Дёшево: файлы читаются один раз на изменение mtime."""
    files = _files(symbol, tf)
    full = available(symbol, tf)
    parts = [f"k={_fp_part(files[0])}", f"f={_fp_part(files[1]) if full else 'lite'}"]
    if strategy in DERIV_STRATEGIES:
        parts.append(f"oi={_fp_part(files[2])}")
        parts.append(f"lsr={_fp_part(files[3])}")
    return "|".join(parts)


def data_sig(symbol: str, tf: str) -> str:
    """Грубая сигнатура всех четырёх файлов таймфрейма — для очистки устаревших записей."""
    return "|".join(_fp_part(f) for f in _files(symbol, tf))


def purge_cache(symbol: str) -> int:
    return cache.purge_stale(symbol, {tf: data_sig(symbol, tf) for tf in klines.TIMEFRAMES})


def _last_ts(path) -> str | None:
    i = _file_info(path)
    return i["last"] if i else None


def data_status(symbol: str, tf: str, now: pd.Timestamp | None = None) -> dict:
    """Свежесть локального кэша (без сети): последняя закрытая свеча, отставание, метки деривативов."""
    if symbol not in SYMBOLS or tf not in klines.TIMEFRAMES:
        raise ValueError(tr("unknown symbol or timeframe"))
    now = (now or pd.Timestamp.now(tz="UTC")).tz_convert("UTC")
    step = pd.Timedelta(minutes=klines.TIMEFRAMES[tf][1])
    gran = derivs.granularity_for(klines.TIMEFRAMES[tf][1])
    p = klines._path(symbol, tf)
    derivs_info = {"funding": _last_ts(paths.MARKET / f"{symbol}_funding.parquet"),
                   "oi": _last_ts(paths.MARKET / f"{symbol}_oi_{gran}.parquet"),
                   "lsr": _last_ts(paths.MARKET / f"{symbol}_lsr_{gran}.parquet"), "granularity": gran}
    out = {"symbol": symbol, "tf": tf, "exists": p.exists(), "now": now.isoformat(), "step_min": step.total_seconds() / 60,
           "derivs": derivs_info, "last": None, "first": None, "bars": 0, "behind_bars": None, "expected_last": None,
           "level": "bad"}
    out["estimate"] = fetch_estimate(tf)
    info = _file_info(p)
    last = pd.Timestamp(info["last"]) if info and info["last"] else None
    # сетка берётся от реальной последней свечи (недельные бары Bybit стартуют в понедельник, а не по эпохе)
    expected = klines.last_closed_start(now, tf, last)     # открытие последней ЗАКРЫТОЙ свечи
    out["expected_last"] = expected.isoformat()
    if last is None:
        return out
    first, bars = info["first"], info["rows"]
    behind = max(0, int(round((expected - last) / step)))
    out.update(last=last.isoformat(), first=first, bars=int(bars), behind_bars=behind,
               level="ok" if behind <= 1 else "warn" if behind <= 3 else "bad")
    return out


def update_data(symbol: str, tf: str) -> dict:
    """ЯВНАЯ докачка новых свечей и деривативов (сеть). Запись атомарная (klines._atomic_parquet внутри load/derivs)."""
    if symbol not in SYMBOLS or tf not in klines.TIMEFRAMES:
        raise ValueError(tr("unknown symbol or timeframe"))
    before = data_status(symbol, tf)
    eff = klines.effective_days(tf, 1095)
    with guard.patient():       # веб-докачка ждёт занятый файл терпеливо (бот в это время ждёт не дольше нескольких секунд)
        df = klines.load(symbol, tf, eff, refresh=True)
        warning = None
        try:
            derivs.enrich(df, symbol, refresh=True, days=eff)
        except guard.Cancelled:
            raise
        except Exception as e:  # noqa: BLE001  свечи уже сохранены; деривативы можно докачать повторно
            warning = tr("candles updated, but the derivatives failed to load: {exc}: {err}").format(exc=type(e).__name__, err=e)
    invalidate_data(symbol)
    purged = purge_cache(symbol)
    after = data_status(symbol, tf)
    added = max(0, after["bars"] - before["bars"])
    return {"added": added, "status": after, "before_last": before["last"], "warning": warning, "purged": purged}


def fetch_data(symbol: str, tf: str) -> dict:
    """ЕДИНСТВЕННОЕ место, где веб ходит в сеть — по явной команде пользователя."""
    if symbol not in SYMBOLS or tf not in klines.TIMEFRAMES:
        raise ValueError(tr("unknown symbol or timeframe"))
    eff = klines.effective_days(tf, 1095)
    with guard.patient():
        df = klines.load(symbol, tf, eff, refresh=True)
        derivs.enrich(df, symbol, refresh=True, days=eff)
    invalidate_data(symbol)
    purge_cache(symbol)
    return {"symbol": symbol, "tf": tf, "bars": int(len(df)), "start": df.index[0].isoformat(),
            "end": df.index[-1].isoformat()}
