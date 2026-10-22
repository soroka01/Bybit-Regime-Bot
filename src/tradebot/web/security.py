"""Защита локального дашборда (чистые ASGI-middleware, без BaseHTTPMiddleware): запрет кросс-сайтовых изменяющих запросов
(CSRF / DNS-rebinding при открытой странице в браузере), обязательный JSON в теле и защитные заголовки ответов."""
from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from tradebot.i18n import tr

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
BAD_FETCH_SITES = ("cross-site", "same-site")        # Sec-Fetch-Site: запрос инициирован другой страницей/сайтом

# CSP — ТОЛЬКО frame-ancestors: script-src/style-src сломали бы собранную SPA (esbuild-бандл, стили в разметке)
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _is_json(content_type: bytes | None) -> bool:
    if not content_type:
        return False
    return content_type.decode("latin-1").split(";", 1)[0].strip().lower() == "application/json"


def _has_body(h: dict) -> bool:
    cl = h.get(b"content-length")
    if cl is not None:
        return cl.strip() not in (b"", b"0")
    return b"transfer-encoding" in h


class SameOriginGuard:
    """Для любых методов, кроме GET/HEAD/OPTIONS:
    1) Sec-Fetch-Site (его ставит браузер, страница подделать не может) cross-site / same-site -> 403;
    2) Origin (если есть) обязан совпадать с Host -> иначе 403 (скрипты без Origin проходят);
    3) тело (если есть) только application/json -> иначе 415 (text/plain и формы — классические «простые» CSRF-запросы)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            h = dict(scope["headers"])
            site = h.get(b"sec-fetch-site")
            if site and site.decode("latin-1").strip().lower() in BAD_FETCH_SITES:
                await JSONResponse({"detail": tr("cross-site is forbidden")}, status_code=403)(scope, receive, send)
                return
            origin = h.get(b"origin")
            if origin:
                host = h.get(b"host")
                if origin.decode("latin-1").split("://", 1)[-1] != (host.decode("latin-1") if host is not None else None):
                    await JSONResponse({"detail": tr("cross-origin is forbidden")}, status_code=403)(scope, receive, send)
                    return
            if _has_body(h) and not _is_json(h.get(b"content-type")):
                await JSONResponse({"detail": tr("the request body must be application/json")}, status_code=415)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class SecurityHeaders:
    """Добавляет защитные заголовки ко всем HTTP-ответам (в том числе к ошибкам и статике). Уже выставленные не затирает."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS.items():
                    headers.setdefault(k, v)
            await send(message)
        await self.app(scope, receive, send_wrapped)
