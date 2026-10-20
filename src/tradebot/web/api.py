"""FastAPI-приложение дашборда (только localhost, same-origin, CORS выключен).

Тяжёлые эндпоинты: пул потоков + семафор на 1 одновременный расчёт, «последний запрос побеждает»
(устаревшие в очереди получают 429), закрытое клиентом соединение не считается, таймаут 60 с,
небольшой LRU-кэш (готовые байты ответа) по телу запроса и версии файлов данных.
Сетевые задания (fetch/update) идут в отдельной полосе со своим семафором и пулом — с расчётами не пересекаются.
Сеть используется только в POST /api/data/fetch|update (явная команда пользователя).
Защита: web/security.py (Sec-Fetch-Site, Origin==Host, JSON-тело, защитные заголовки) + разрешённые Host.
При остановке приложения (lifespan) полосы отменяют идущие задания и сбрасывают очередь пулов (web/lanes.py).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import os
import pickle
import sqlite3
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pandas.errors import EmptyDataError, ParserError
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from tradebot.market import guard
from tradebot.paths import WEB_DIST
from tradebot.web import backtests, common, data, lanes, meta, research, stats, stats_base, wf
from tradebot.core.legspec import draft_payload, leg_label, parse_leg_text, validate_leg
from tradebot.web.schemas import (BacktestReq, CachedReq, FetchReq, LbItemReq, OptimizeReq, PortfolioReq, ProposalReq,
                                  SweepReq, Symbol, Timeframe)
from tradebot.web.security import SameOriginGuard, SecurityHeaders
from tradebot.web.serialize import clean
from tradebot.web.lanes import _err

log = logging.getLogger("tradebot.web")


@asynccontextmanager
async def lifespan(_app):
    """Старт: закрытые остановкой полосы получают свежие пулы. Остановка: идущим заданиям — флаг отмены, очередь пулов
    сбрасывается без ожидания (иначе остановка бота ждала бы докачку данных до 10 минут)."""
    for lane in (lanes._calc, lanes._net):
        lane.reopen()
    yield
    for lane in (lanes._calc, lanes._net):
        lane.shutdown()


app = FastAPI(title="tradebot dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
if os.environ.get("TRADEBOT_ALLOW_TESTSERVER") == "1":      # Host TestClient; ставит только tests/conftest.py
    ALLOWED_HOSTS.append("testserver")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


def _finite(o):
    """NaN/Infinity -> строка: иначе в ответе 422 «input: nan» нельзя сериализовать (allow_nan=False) и вместо 422 был бы 500."""
    if isinstance(o, float) and not math.isfinite(o):
        return str(o)
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": _finite(jsonable_encoder(exc.errors()))}, status_code=422)


app.add_middleware(SameOriginGuard)
app.add_middleware(GZipMiddleware, minimum_size=2000)     # сжимает и ответы guard/статику
app.add_middleware(SecurityHeaders)                       # внешний слой: защитные заголовки на ВСЕХ ответах (и на 400 от TrustedHost)


_cache: OrderedDict[str, object] = OrderedDict()
CACHE_SIZE = 64


def _cache_get(key: str):
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]


def _cache_put(key: str, val):
    _cache[key] = val
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


# ---------------------------------------------------------------- единый маппинг исключений -> HTTP
def _map_exc(e: Exception, upstream: bool = False):
    """Исключение -> HTTPException (или None, если не знаем). upstream=True — эндпоинты, ходящие в сеть (fetch/update):
    там KeyError/TimeoutError — сбой ответа биржи (-> 502 снаружи), а не ошибка запроса клиента."""
    if isinstance(e, HTTPException):
        return e
    if isinstance(e, guard.Cancelled):
        return _err(503, "shutting_down", "сервер останавливается")
    if isinstance(e, data.DataMissing):
        return _err(409, "data_missing", str(e), symbol=e.symbol, tf=e.tf, missing=e.missing, estimate=e.estimate)
    if isinstance(e, sqlite3.Error):
        return _err(503, "db_error", "база данных временно недоступна")
    if isinstance(e, (EOFError, pickle.PickleError, EmptyDataError, ParserError)):    # битый pkl/csv результатов
        return _err(500, "bad_results", f"файл результатов повреждён ({type(e).__name__})")
    if upstream:
        return _err(422, "bad_request", str(e)) if isinstance(e, ValueError) else None
    if isinstance(e, TimeoutError):
        return _err(504, "timeout", str(e))
    if isinstance(e, KeyError):      # str(KeyError) = "'col'" — без контекста; пишем понятное сообщение
        return _err(422, "bad_request", f"в данных или запросе нет ключа/колонки {e.args[0] if e.args else '?'}")
    if isinstance(e, ValueError):
        return _err(422, "bad_request", str(e))
    return None


def _fail(e: Exception, what: str, fallback: tuple[int, str, str] | None = None, upstream: bool = False) -> HTTPException:
    """Единая обработка ошибок эндпоинтов: известное исключение -> свой статус; иначе пишем трассировку в лог и отдаём
    fallback=(статус, код, префикс сообщения) либо 500 internal. Сообщения очищаются от абсолютных путей (lanes._err)."""
    mapped = _map_exc(e, upstream)
    if mapped is not None:
        return mapped
    log.exception("%s failed", what)
    status, code, prefix = fallback or (500, "internal", "")
    return _err(status, code, f"{prefix}{type(e).__name__}: {e}")


def _encode(obj) -> bytes:
    """Те же байты, что выдал бы JSONResponse (компактно, UTF-8, без NaN)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json(body: bytes, cache: str) -> Response:
    return Response(content=body, media_type="application/json", headers={"X-Cache": cache})


async def heavy_endpoint(request: Request, kind: str, req, compute, with_deadline=False):
    key = f"{kind}:{req.model_dump_json()}:{hashlib.md5(data.data_version().encode()).hexdigest()}"
    use_mem = not getattr(req, "force", False)         # force = «пересчитать»: память процесса не используем
    hit = _cache_get(key) if use_mem else None
    if hit is not None:
        return _json(hit[1], "hit")                    # кэшируем уже закодированные байты — без повторной сериализации

    def job():
        return clean(compute(req, common.Deadline(lanes.HEAVY_TIMEOUT)) if with_deadline else compute(req))

    try:
        out = await lanes.run_heavy(request, kind, job)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise _fail(e, kind)
    body = _encode(out)
    if use_mem:       # при повторе из памяти ответ «из кэша»: для карточек/подбора поле from_cache = True
        _cache_put(key, (body, _encode({**out, "from_cache": True}) if isinstance(out, dict) and "from_cache" in out else body))
    return _json(body, "miss")


def etagged(request: Request, payload, max_age: int = 30):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    etag = '"' + hashlib.md5(body).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": f"private, max-age={max_age}"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


# ---------------------------------------------------------------- маршруты
@app.get("/api/meta")
def api_meta(request: Request):
    return etagged(request, clean(meta.meta()), max_age=5)


@app.post("/api/backtest")
async def api_backtest(request: Request, req: BacktestReq):
    return await heavy_endpoint(request, "backtest", req, backtests.run_backtest, with_deadline=True)


@app.post("/api/sweep")
async def api_sweep(request: Request, req: SweepReq):
    return await heavy_endpoint(request, "sweep", req, backtests.run_sweep, with_deadline=True)


@app.post("/api/portfolio")
async def api_portfolio(request: Request, req: PortfolioReq):
    return await heavy_endpoint(request, "portfolio", req, backtests.run_portfolio, with_deadline=True)


@app.post("/api/optimize")
async def api_optimize(request: Request, req: OptimizeReq):
    return await heavy_endpoint(request, "optimize", req, research.run_optimize, with_deadline=True)


@app.get("/api/leaderboard/plan")
def api_lb_plan(symbol: Symbol = "BTCUSDT", tf: Timeframe = "1h"):
    try:
        return JSONResponse(research.lb_plan(symbol, tf), headers={"Cache-Control": "no-store"})
    except Exception as e:  # noqa: BLE001
        raise _fail(e, "lb_plan")


@app.post("/api/leaderboard/cached")
def api_lb_cached(req: CachedReq):
    """Мгновенный поиск готовых карточек в дисковом кэше. Без тяжёлого семафора и без вычислений."""
    try:
        return JSONResponse(clean(research.lb_cached(req)), headers={"Cache-Control": "no-store"})
    except Exception as e:  # noqa: BLE001
        raise _fail(e, "lb_cached")


@app.post("/api/leaderboard/item")
async def api_lb_item(request: Request, req: LbItemReq):
    return await heavy_endpoint(request, "leaderboard", req, research.run_lb_item, with_deadline=True)


def _wf(request: Request, fn, *args):
    try:
        return etagged(request, fn(*args), max_age=60)
    except FileNotFoundError as e:
        raise _err(404, "no_results", f"нет файла результатов walk-forward: {e}")
    except Exception as e:  # noqa: BLE001
        raise _fail(e, "walkforward")


@app.get("/api/walkforward")
def api_wf(request: Request, lev: int = 1, symbol: Symbol = "BTCUSDT"):
    return _wf(request, wf.wf_table, lev, symbol)


@app.get("/api/walkforward/curve")
def api_wf_curve(request: Request, tf: Timeframe, strategy: str, variant: str, lev: int = 1, symbol: Symbol = "BTCUSDT"):
    return _wf(request, wf.wf_curve, lev, tf, strategy, variant, symbol)


@app.get("/api/walkforward/windows")
def api_wf_windows(request: Request, lev: int = 1, symbol: Symbol = "BTCUSDT"):
    return _wf(request, wf.wf_windows, lev, symbol)


_SECRET_HINTS = ("token", "secret", "password", "passwd", "signature", "api_key", "apikey", "api-key", "private")


def _scrub(o):
    if isinstance(o, dict):
        return {k: _scrub(v) for k, v in o.items()
                if not any(h in str(k).lower() for h in _SECRET_HINTS)}
    if isinstance(o, list):
        return [_scrub(v) for v in o]
    return o


@app.get("/api/live")
def api_live():
    """Только чтение снимка бота. Модуль trading.state импортируется лениво: его может ещё не быть."""
    headers = {"Cache-Control": "no-store"}
    try:
        from tradebot.trading import state  # type: ignore
        snap = state.read_snapshot()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"available": False, "reason": type(e).__name__}, headers=headers)
    if not isinstance(snap, dict) or not snap:
        return JSONResponse({"available": False, "reason": "no_snapshot"}, headers=headers)
    return JSONResponse({**clean(_scrub(snap)), "available": True}, headers=headers)


BOT_ALIVE_S = 180.0


@app.post("/api/bot/proposals")
def api_bot_proposals(req: ProposalReq):
    """«В бота» на сайте: предложение стратегии (тот же текст, что «Копировать для бота») кладётся в очередь бота. Торговлю это
    НЕ меняет: бот пришлёт карточку в Telegram, вес и подтверждение — там. Одна короткая запись в bot.db."""
    headers = {"Cache-Control": "no-store"}
    try:
        drafts = parse_leg_text(req.text)
    except ValueError as e:
        raise _err(422, "bad_request", str(e))
    payloads, errors = [], []
    for d in drafts:
        try:
            payloads.append(draft_payload(dataclasses.replace(d, leg=validate_leg(d.leg))))
        except ValueError as e:
            errors.append(f"{leg_label(d.leg)}: {e}")
    if not payloads:
        raise _err(422, "bad_request", "; ".join(errors))
    from tradebot.trading import state  # ленивый импорт, как в /api/live
    try:
        ids = state.submit_proposals(payloads)
    except state.ProposalError as e:
        raise _err(429 if e.code == "too_many" else 409, e.code, str(e))
    except sqlite3.Error as e:      # база бота занята/повреждена: чистый 503 вместо необработанного 500
        raise _fail(e, "proposals")
    try:
        age = state.heartbeat_age()
    except sqlite3.Error:           # предложение уже записано; «жив ли бот» узнать не удалось — не пугаем ошибкой
        age = None
    return JSONResponse({"ok": True, "added": len(ids), "errors": errors, "bot_alive": age is not None and age < BOT_ALIVE_S}, headers=headers)


@app.get("/api/stats")
def api_stats(range_: str = Query(stats_base.DEFAULT_RANGE, alias="range")):
    """Статистика РЕАЛЬНОЙ торговой истории бота (bot.db, только чтение): сделки, equity-метрики, ряды для графиков.
    Для допустимого range ответ всегда 200 (при отсутствии данных — available:false + reason). Неверный range -> 400."""
    if range_ not in stats_base.RANGE_SECONDS:
        raise _err(400, "bad_request", f"неизвестный диапазон «{range_}»: допустимо {', '.join(stats_base.RANGE_SECONDS)}")
    headers = {"Cache-Control": "no-store"}
    try:
        from tradebot.trading import state  # ленивый импорт, как в /api/live
        out = stats.get_stats(range_, state.DB_PATH)
    except Exception as e:  # noqa: BLE001  статистика никогда не должна давать 500
        log.exception("stats failed")
        out = stats.unavailable(range_, "error", f"Не удалось посчитать статистику: {type(e).__name__}", time.time())
    return Response(_encode(clean(out)), media_type="application/json", headers=headers)


@app.get("/api/data/status")
def api_data_status(symbol: Symbol = "BTCUSDT", tf: Timeframe = "1h"):
    try:
        return JSONResponse(clean(data.data_status(symbol, tf)), headers={"Cache-Control": "no-store"})
    except Exception as e:  # noqa: BLE001
        raise _fail(e, "data_status")


@app.post("/api/data/update")
async def api_data_update(request: Request, req: FetchReq):
    """Явная докачка новых свечей + деривативов (сеть, атомарная запись). Сбрасывает кэши расчётов."""
    try:
        out = await lanes.run_heavy(request, "update", lambda: clean(data.update_data(req.symbol, req.tf)), lanes.FETCH_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        raise _fail(e, "update", (502, "update_failed", "не удалось обновить данные: "), upstream=True)
    _cache.clear()
    return out


@app.post("/api/data/fetch")
async def api_fetch(request: Request, req: FetchReq):
    """Явная загрузка данных с Bybit (публичный API, без ключей). Никогда не вызывается неявно."""
    try:
        out = await lanes.run_heavy(request, "fetch", lambda: data.fetch_data(req.symbol, req.tf), lanes.FETCH_TIMEOUT)
        _cache.clear()
        return out
    except Exception as e:  # noqa: BLE001
        raise _fail(e, "fetch", (502, "fetch_failed", ""), upstream=True)


# ---------------------------------------------------------------- статика (web/dist)
_FALLBACK = ("<!doctype html><meta charset=utf-8><title>tradebot</title><body style='font-family:sans-serif;padding:2rem'>"
             "<h2>Интерфейс не собран</h2><p>Выполните <code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code>.</p>")


@app.get("/{path:path}", include_in_schema=False)
def static(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    root = Path(WEB_DIST).resolve()
    try:
        f = (root / (path or "index.html")).resolve()
        inside = root in f.parents and f.is_file()
    except (ValueError, OSError):          # NUL-байт в пути, слишком длинное имя, недопустимые символы Windows
        raise HTTPException(404)
    if inside:
        headers = {"Cache-Control": "no-cache"}
        return FileResponse(f, headers=headers)
    index = root / "index.html"
    if not path and index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    if not path:
        return HTMLResponse(_FALLBACK)
    raise HTTPException(404)
