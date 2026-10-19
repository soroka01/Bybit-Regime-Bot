"""FastAPI-приложение дашборда (только localhost, same-origin, CORS выключен).

Тяжёлые эндпоинты: пул потоков + семафор на 1 одновременный расчёт, «последний запрос побеждает»
(устаревшие в очереди получают 429), закрытое клиентом соединение не считается, таймаут 60 с,
небольшой LRU-кэш (готовые байты ответа) по телу запроса и версии файлов данных.
Сетевые задания (fetch/update) идут в отдельной полосе со своим семафором и пулом — с расчётами не пересекаются.
Сеть используется только в POST /api/data/fetch|update (явная команда пользователя).
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import itertools
import json
import logging
import math
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from tradebot.paths import WEB_DIST
from tradebot.web import service, stats
from tradebot.core.legspec import draft_payload, leg_label, parse_leg_text, validate_leg
from tradebot.web.schemas import (BacktestReq, CachedReq, FetchReq, LbItemReq, OptimizeReq, PortfolioReq, ProposalReq,
                                  SweepReq)
from tradebot.web.serialize import clean

log = logging.getLogger("tradebot.web")
HEAVY_TIMEOUT = 60.0
QUEUE_WAIT = 20.0
FETCH_TIMEOUT = 600.0

app = FastAPI(title="tradebot dashboard", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])


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


class SameOriginGuard:
    """Чистый ASGI-middleware (без BaseHTTPMiddleware): запрещает кросс-сайтовые POST (защита от CSRF/DNS-rebinding
    при открытой странице в браузере). Origin обязан совпадать с Host для любых методов, кроме GET/HEAD/OPTIONS."""
    SAFE = ("GET", "HEAD", "OPTIONS")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in self.SAFE:
            h = dict(scope["headers"])
            origin = h.get(b"origin")
            if origin:
                host = h.get(b"host")
                if origin.decode("latin-1").split("://", 1)[-1] != (host.decode("latin-1") if host is not None else None):
                    await JSONResponse({"detail": "cross-origin запрещён"}, status_code=403)(scope, receive, send)
                    return
        await self.app(scope, receive, send)


app.add_middleware(SameOriginGuard)
app.add_middleware(GZipMiddleware, minimum_size=2000)     # внешний слой: сжимает и ответы guard/статику


_ids = itertools.count(1)
_cache: OrderedDict[str, object] = OrderedDict()
CACHE_SIZE = 64
WATCHDOG_FACTOR = 2.0       # задание дольше WATCHDOG_FACTOR * timeout считается зависшим: семафор заменяется свежим
QUEUE_SLICE = 1.0           # ожидающий запрос просыпается раз в секунду: проверить «вытеснен ли» и «заменён ли семафор»
NET_QUEUE_WAIT = 3.0        # загрузка данных: второй fetch/update не ждёт десятки секунд — сразу «занято»


class Lane:
    """Полоса тяжёлых заданий: свой семафор (1 одновременное задание), свой пул потоков и свои правила очереди.
    Расчёты и сетевые задания живут в РАЗНЫХ полосах и друг друга не блокируют.
    Размер пула = 1 активное задание + abandon_limit «брошенных» watchdog-ом потоков (поток убить нельзя; он доработает
    сам, а полоса за это время уже принимает новое задание)."""

    def __init__(self, name: str, queue_wait: float, supersede: bool, abandon_limit: int):
        self.name, self.queue_wait, self.supersede, self.abandon_limit = name, queue_wait, supersede, abandon_limit
        self.pool = ThreadPoolExecutor(max_workers=1 + abandon_limit, thread_name_prefix=f"web-{name}")
        self.sem: asyncio.Semaphore | None = None     # создаётся лениво, уже внутри цикла событий
        self.latest: dict[str, int] = {}              # последний id запроса по виду: «последний побеждает»
        self.abandoned = 0                            # сколько брошенных потоков ещё работает

    def semaphore(self) -> asyncio.Semaphore:
        if self.sem is None:
            self.sem = asyncio.Semaphore(1)
        return self.sem


_calc = Lane("calc", QUEUE_WAIT, supersede=True, abandon_limit=2)    # backtest/sweep/portfolio/optimize/leaderboard
_net = Lane("net", NET_QUEUE_WAIT, supersede=False, abandon_limit=1)  # fetch/update (разные символы/тф не вытесняют друг друга)
NET_KINDS = {"fetch", "update"}


def _lane_for(kind: str) -> Lane:
    return _net if kind in NET_KINDS else _calc


def _err(status: int, code: str, msg: str, **extra):
    return HTTPException(status, detail={"code": code, "message": msg, **extra})


async def _acquire(lane: Lane, kind: str, my: int) -> asyncio.Semaphore:
    """Ждёт свободный слот не дольше lane.queue_wait. Раз в QUEUE_SLICE проверяет, не вытеснен ли запрос более новым
    и не подменил ли watchdog семафор (иначе ожидающие остались бы на старом, который держит зависший поток)."""
    t0 = time.monotonic()
    end = t0 + lane.queue_wait
    while True:
        sem = lane.semaphore()
        try:
            await asyncio.wait_for(sem.acquire(), min(max(end - time.monotonic(), 0.0), QUEUE_SLICE))
            return sem
        except asyncio.TimeoutError:
            pass
        if lane.supersede and lane.latest.get(kind) != my:
            raise _err(429, "superseded", "запрос вытеснен более новым")
        if time.monotonic() >= end:
            waited = time.monotonic() - t0
            raise _err(429, "busy", "сервер занят другим расчётом" if lane.name == "calc" else "уже идёт загрузка данных",
                       waited_s=round(waited, 1), queue_wait_s=lane.queue_wait)


async def run_heavy(request: Request, kind: str, fn, timeout: float = HEAVY_TIMEOUT):
    """Выполняет fn() в потоке под семафором своей полосы. fn не должен менять общее состояние без блокировок.
    Таймаут отвечает клиенту 504, но слот освобождается только когда fn реально завершилась; если она не
    завершилась за WATCHDOG_FACTOR*timeout — watchdog подменяет семафор свежим (и пишет в лог)."""
    lane, my = _lane_for(kind), next(_ids)
    if lane.supersede:
        lane.latest[kind] = my
    sem = await _acquire(lane, kind, my)
    handed_off = False
    try:
        if lane.supersede and lane.latest.get(kind) != my:
            raise _err(429, "superseded", "запрос вытеснен более новым")
        if await request.is_disconnected():
            raise _err(499, "cancelled", "клиент отменил запрос")
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(lane.pool, fn)
        state = {"abandoned": False}

        def _watchdog():
            if fut.done():
                return
            if lane.abandoned >= lane.abandon_limit:
                log.error("%s/%s: задание не завершилось за %.0f с, но брошенных потоков уже %d — слот остаётся занятым",
                          lane.name, kind, timeout * WATCHDOG_FACTOR, lane.abandoned)
                return
            lane.abandoned += 1
            state["abandoned"] = True
            if lane.sem is sem:
                lane.sem = asyncio.Semaphore(1)
            log.error("%s/%s: задание не завершилось за %.0f с (таймаут %.0f с) — семафор заменён, поток продолжает работать",
                      lane.name, kind, timeout * WATCHDOG_FACTOR, timeout)
        wd = loop.call_later(timeout * WATCHDOG_FACTOR, _watchdog)

        def _done(f):
            wd.cancel()
            if state["abandoned"]:
                lane.abandoned -= 1
            sem.release()
            if not f.cancelled():
                f.exception()  # «забираем» исключение, чтобы не было предупреждений
        fut.add_done_callback(_done)
        handed_off = True
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            raise _err(504, "timeout", f"расчёт дольше {int(timeout)} с")
    finally:
        if not handed_off:
            sem.release()


def _cache_get(key: str):
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]


def _cache_put(key: str, val):
    _cache[key] = val
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


def _map_exc(e: Exception):
    if isinstance(e, service.DataMissing):
        return _err(409, "data_missing", str(e), symbol=e.symbol, tf=e.tf, missing=e.missing, estimate=e.estimate)
    if isinstance(e, TimeoutError):
        return _err(504, "timeout", str(e))
    if isinstance(e, KeyError):      # str(KeyError) = "'col'" — без контекста; пишем понятное сообщение
        return _err(422, "bad_request", f"в данных или запросе нет ключа/колонки {e.args[0] if e.args else '?'}")
    if isinstance(e, ValueError):
        return _err(422, "bad_request", str(e))
    return None


def _encode(obj) -> bytes:
    """Те же байты, что выдал бы JSONResponse (компактно, UTF-8, без NaN)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json(body: bytes, cache: str) -> Response:
    return Response(content=body, media_type="application/json", headers={"X-Cache": cache})


async def heavy_endpoint(request: Request, kind: str, req, compute, with_deadline=False):
    key = f"{kind}:{req.model_dump_json()}:{hashlib.md5(service.data_version().encode()).hexdigest()}"
    use_mem = not getattr(req, "force", False)         # force = «пересчитать»: память процесса не используем
    hit = _cache_get(key) if use_mem else None
    if hit is not None:
        return _json(hit[1], "hit")                    # кэшируем уже закодированные байты — без повторной сериализации

    def job():
        return clean(compute(req, service.Deadline(HEAVY_TIMEOUT)) if with_deadline else compute(req))

    try:
        out = await run_heavy(request, kind, job)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        mapped = _map_exc(e)
        if mapped:
            raise mapped
        log.exception("%s failed", kind)
        raise _err(500, "internal", f"{type(e).__name__}: {e}")
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
    return etagged(request, clean(service.meta()), max_age=5)


@app.post("/api/backtest")
async def api_backtest(request: Request, req: BacktestReq):
    return await heavy_endpoint(request, "backtest", req, service.run_backtest, with_deadline=True)


@app.post("/api/sweep")
async def api_sweep(request: Request, req: SweepReq):
    return await heavy_endpoint(request, "sweep", req, service.run_sweep, with_deadline=True)


@app.post("/api/portfolio")
async def api_portfolio(request: Request, req: PortfolioReq):
    return await heavy_endpoint(request, "portfolio", req, service.run_portfolio, with_deadline=True)


@app.post("/api/optimize")
async def api_optimize(request: Request, req: OptimizeReq):
    return await heavy_endpoint(request, "optimize", req, service.run_optimize, with_deadline=True)


@app.get("/api/leaderboard/plan")
def api_lb_plan(symbol: str = "BTCUSDT", tf: str = "1h"):
    try:
        return JSONResponse(service.lb_plan(symbol, tf), headers={"Cache-Control": "no-store"})
    except ValueError as e:
        raise _err(422, "bad_request", str(e))


@app.post("/api/leaderboard/cached")
def api_lb_cached(req: CachedReq):
    """Мгновенный поиск готовых карточек в дисковом кэше. Без тяжёлого семафора и без вычислений."""
    try:
        return JSONResponse(clean(service.lb_cached(req)), headers={"Cache-Control": "no-store"})
    except ValueError as e:
        raise _err(422, "bad_request", str(e))


@app.post("/api/leaderboard/item")
async def api_lb_item(request: Request, req: LbItemReq):
    return await heavy_endpoint(request, "leaderboard", req, service.run_lb_item, with_deadline=True)


def _wf(request: Request, fn, *args):
    try:
        return etagged(request, fn(*args), max_age=60)
    except FileNotFoundError as e:
        raise _err(404, "no_results", f"нет файла результатов walk-forward: {e}")
    except (ValueError, KeyError) as e:
        raise _err(422, "bad_request", str(e))


@app.get("/api/walkforward")
def api_wf(request: Request, lev: int = 1, symbol: str = "BTCUSDT"):
    return _wf(request, service.wf_table, lev, symbol)


@app.get("/api/walkforward/curve")
def api_wf_curve(request: Request, tf: str, strategy: str, variant: str, lev: int = 1, symbol: str = "BTCUSDT"):
    return _wf(request, service.wf_curve, lev, tf, strategy, variant, symbol)


@app.get("/api/walkforward/windows")
def api_wf_windows(request: Request, lev: int = 1, symbol: str = "BTCUSDT"):
    return _wf(request, service.wf_windows, lev, symbol)


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
    age = state.heartbeat_age()
    return JSONResponse({"ok": True, "added": len(ids), "errors": errors, "bot_alive": age is not None and age < BOT_ALIVE_S}, headers=headers)


@app.get("/api/stats")
def api_stats(range_: str = Query(stats.DEFAULT_RANGE, alias="range")):
    """Статистика РЕАЛЬНОЙ торговой истории бота (bot.db, только чтение): сделки, equity-метрики, ряды для графиков.
    Для допустимого range ответ всегда 200 (при отсутствии данных — available:false + reason). Неверный range -> 400."""
    if range_ not in stats.RANGE_SECONDS:
        raise _err(400, "bad_request", f"неизвестный диапазон «{range_}»: допустимо {', '.join(stats.RANGE_SECONDS)}")
    headers = {"Cache-Control": "no-store"}
    try:
        from tradebot.trading import state  # ленивый импорт, как в /api/live
        out = stats.get_stats(range_, state.DB_PATH)
    except Exception as e:  # noqa: BLE001  статистика никогда не должна давать 500
        log.exception("stats failed")
        out = stats.unavailable(range_, "error", f"Не удалось посчитать статистику: {type(e).__name__}", time.time())
    return Response(_encode(clean(out)), media_type="application/json", headers=headers)


@app.get("/api/data/status")
def api_data_status(symbol: str = "BTCUSDT", tf: str = "1h"):
    try:
        return JSONResponse(clean(service.data_status(symbol, tf)), headers={"Cache-Control": "no-store"})
    except ValueError as e:
        raise _err(422, "bad_request", str(e))


@app.post("/api/data/update")
async def api_data_update(request: Request, req: FetchReq):
    """Явная докачка новых свечей + деривативов (сеть, атомарная запись). Сбрасывает кэши расчётов."""
    try:
        out = await run_heavy(request, "update", lambda: clean(service.update_data(req.symbol, req.tf)), FETCH_TIMEOUT)
    except HTTPException:
        raise
    except ValueError as e:
        raise _err(422, "bad_request", str(e))
    except Exception as e:  # noqa: BLE001
        raise _err(502, "update_failed", f"не удалось обновить данные: {type(e).__name__}: {e}")
    _cache.clear()
    return out


@app.post("/api/data/fetch")
async def api_fetch(request: Request, req: FetchReq):
    """Явная загрузка данных с Bybit (публичный API, без ключей). Никогда не вызывается неявно."""
    try:
        out = await run_heavy(request, "fetch", lambda: service.fetch_data(req.symbol, req.tf), FETCH_TIMEOUT)
        _cache.clear()
        return out
    except HTTPException:
        raise
    except ValueError as e:
        raise _err(422, "bad_request", str(e))
    except Exception as e:  # noqa: BLE001
        raise _err(502, "fetch_failed", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- статика (web/dist)
_FALLBACK = ("<!doctype html><meta charset=utf-8><title>tradebot</title><body style='font-family:sans-serif;padding:2rem'>"
             "<h2>Интерфейс не собран</h2><p>Выполните <code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code>.</p>")


@app.get("/{path:path}", include_in_schema=False)
def static(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    root = Path(WEB_DIST).resolve()
    f = (root / (path or "index.html")).resolve()
    if root in f.parents and f.is_file():
        headers = {"Cache-Control": "no-cache"}
        return FileResponse(f, headers=headers)
    index = root / "index.html"
    if not path and index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    if not path:
        return HTMLResponse(_FALLBACK)
    raise HTTPException(404)
