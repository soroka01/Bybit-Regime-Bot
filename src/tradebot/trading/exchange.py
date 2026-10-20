"""Клиент Bybit v5 (async httpx, HMAC)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Protocol
from urllib.parse import urlencode

import httpx

from tradebot.trading.config import SYMBOL
from tradebot.trading.pnl import normalize_record

RECV_WINDOW = "10000"
TAKER_FEE = 0.001  # реальная комиссия taker (0.1%): оценка комиссии в pnl.py; тестовый фейк биржи списывает её с каждого исполнения
# коды «уже установлено» при настройке аккаунта — не ошибка
BENIGN_SETUP = {110025, 110026, 110043, 34036, 3400045}


class ExchangeError(Exception):
    def __init__(self, msg: str, code: int = -1):
        super().__init__(msg)
        self.code = code


class AmbiguousOrder(ExchangeError):
    """Таймаут/сетевой сбой при POST: неизвестно, дошёл ли ордер."""


@dataclass
class Instrument:
    qty_step: float
    min_qty: float
    tick: float


@dataclass
class Position:
    side: str  # long | short
    qty: float
    entry: float
    upnl: float
    mark: float
    stop: float | None = None

    @property
    def signed(self) -> float:
        return self.qty if self.side == "long" else -self.qty

    def as_dict(self) -> dict:
        return {"side": self.side, "qty": self.qty, "entry": self.entry, "upnl": self.upnl, "mark": self.mark}


def round_down(x: float, step: float) -> float:
    """Округление вниз к шагу (через Decimal, без float-артефактов)."""
    d = Decimal(str(step))
    return float((Decimal(str(x)) / d).to_integral_value(rounding=ROUND_DOWN) * d)


def fmt(x: float, step: float) -> str:
    d = Decimal(str(step))
    return format(Decimal(str(x)).quantize(d, rounding=ROUND_DOWN), "f")


class Exchange(Protocol):
    async def sync_time(self) -> None: ...
    async def instrument(self) -> Instrument: ...
    async def mark_price(self) -> float: ...
    async def equity(self) -> float: ...
    async def sizing_balance(self) -> float: ...
    async def position(self) -> Position | None: ...
    async def open_orders(self) -> list[dict]: ...
    async def cancel_order(self, order_id: str) -> None: ...
    async def market(self, side: str, qty: float, reduce_only: bool, link_id: str) -> dict: ...
    async def order_by_link(self, link_id: str) -> dict | None: ...
    async def set_stop(self, price: float) -> None: ...
    async def setup_account(self, leverage: float) -> dict: ...
    async def key_info(self) -> dict: ...
    async def closed_pnl(self, order_id: str | None, since_ms: int = 0) -> dict | None: ...
    async def closed_pnl_list(self, since_ms: int = 0, limit: int = 100) -> list[dict]: ...


class BybitClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", client: httpx.AsyncClient | None = None):
        self.base = base_url.rstrip("/")
        self._key, self._secret = api_key, api_secret
        self.http = client or httpx.AsyncClient(timeout=10.0)
        self.offset_ms = 0
        self._synced = 0.0
        self._inst: Instrument | None = None

    def __repr__(self) -> str:
        return f"BybitClient({self.base})"

    async def close(self):
        await self.http.aclose()

    def _ts(self) -> str:
        return str(int(time.time() * 1000) + self.offset_ms)

    async def sync_time(self) -> None:
        t0 = time.time()
        js = await self._call("GET", "/v5/market/time", None, signed=False)
        rtt = time.time() - t0
        srv = int(js["result"]["timeNano"]) // 1_000_000 if js["result"].get("timeNano") else int(js["time"])
        self.offset_ms = int(srv - (t0 + rtt / 2) * 1000)
        self._synced = time.time()

    async def _call(self, method: str, path: str, params: dict | None, signed: bool = True, _resynced: bool = False) -> dict:
        if signed and not (self._key and self._secret):
            raise ExchangeError("нет API-ключей")
        if signed and time.time() - self._synced > 600:
            try:
                await self.sync_time()
            except Exception:
                pass
        attempts = 3 if method == "GET" else 1  # ретраи только для GET
        for i in range(attempts):
            try:
                url, headers, content = self.base + path, {}, None
                if method == "GET":
                    qs = urlencode(params or {})
                    if qs:
                        url += "?" + qs
                    payload = qs
                else:
                    content = json.dumps(params or {}, separators=(",", ":"))
                    headers["Content-Type"] = "application/json"
                    payload = content
                if signed:
                    ts = self._ts()
                    sign = hmac.new(self._secret.encode(), (ts + self._key + RECV_WINDOW + payload).encode(),
                                    hashlib.sha256).hexdigest()
                    headers.update({"X-BAPI-API-KEY": self._key, "X-BAPI-TIMESTAMP": ts,
                                    "X-BAPI-RECV-WINDOW": RECV_WINDOW, "X-BAPI-SIGN": sign})
                r = await self.http.request(method, url, headers=headers, content=content)
                if r.status_code >= 500 or r.status_code == 429:
                    raise httpx.HTTPError(f"HTTP {r.status_code}")
                js = r.json()
                if js.get("retCode") in (10006, 10016) and i < attempts - 1:  # лимиты/внутренняя ошибка
                    await asyncio.sleep(1.5 * (i + 1))
                    continue
                if js.get("retCode") == 10002 and signed and not _resynced:
                    # часы ПК и биржи разошлись (системное время «шагнуло»): пересинхронизироваться и повторить один раз.
                    # 10002 отклоняется шлюзом до обработки, поэтому повтор безопасен и для POST (orderLinkId идемпотентен)
                    try:
                        await self.sync_time()
                    except Exception:
                        pass
                    return await self._call(method, path, params, signed, _resynced=True)
                return js
            except (httpx.HTTPError, ValueError) as e:
                if i < attempts - 1:
                    await asyncio.sleep(1.0 * (i + 1))
                    continue
                if method == "POST":
                    raise AmbiguousOrder(f"неоднозначный ответ: {type(e).__name__}") from None
                raise ExchangeError(f"сеть: {type(e).__name__}") from None
        raise ExchangeError("недостижимо")

    async def _ok(self, method, path, params=None, signed=True, benign: set | None = None) -> dict:
        js = await self._call(method, path, params, signed)
        code = js.get("retCode")
        if code != 0 and not (benign and code in benign):
            raise ExchangeError(f"{path}: {js.get('retMsg')} ({code})", code or -1)
        return js.get("result") or {}

    # --- публичные/приватные методы ---
    async def instrument(self) -> Instrument:
        if self._inst is None:
            r = await self._ok("GET", "/v5/market/instruments-info", {"category": "linear", "symbol": SYMBOL}, signed=False)
            it = r["list"][0]
            self._inst = Instrument(float(it["lotSizeFilter"]["qtyStep"]), float(it["lotSizeFilter"]["minOrderQty"]),
                                    float(it["priceFilter"]["tickSize"]))
        return self._inst

    async def mark_price(self) -> float:
        r = await self._ok("GET", "/v5/market/tickers", {"category": "linear", "symbol": SYMBOL}, signed=False)
        return float(r["list"][0]["markPrice"])

    async def equity(self) -> float:
        """totalEquity единого аккаунта (UTA). Инвариант: БЕЗ запасного CONTRACT-аккаунта (как sizing_balance) — иначе
        сбой UTA-запроса подставил бы equity другого, почти пустого аккаунта, и kill-switch/пик считались бы от мусора.
        Нет числового totalEquity (пустой список, пустая строка, NaN) -> ExchangeError, а не 0."""
        r = await self._ok("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        acc = (r.get("list") or [None])[0] or {}
        try:
            v = float(acc["totalEquity"])
        except (KeyError, TypeError, ValueError):
            raise ExchangeError("wallet-balance: нет числового totalEquity (нужен единый аккаунт UTA)") from None
        if v != v:
            raise ExchangeError("wallet-balance: totalEquity не число")
        return v

    async def sizing_balance(self) -> float:
        """База для размера позиции: min(walletBalance, equity) по монете USDT единого аккаунта (UTA).
        walletBalance не включает нереализованный PnL; min() покрывает случай нереализованного убытка (equity меньше).
        Без запасного CONTRACT-аккаунта, как у equity(): только UTA; нет USDT в ответе или нечисловые поля -> ExchangeError."""
        r = await self._ok("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        acc = (r.get("list") or [None])[0] or {}
        for c in acc.get("coin") or []:
            if c.get("coin") == "USDT":
                try:
                    wallet, eq = float(c["walletBalance"]), float(c["equity"])
                except (KeyError, TypeError, ValueError):
                    raise ExchangeError("wallet-balance: у USDT нет числовых walletBalance/equity") from None
                v = min(wallet, eq)
                if v != v:
                    raise ExchangeError("wallet-balance: баланс USDT не число")
                return v
        raise ExchangeError("wallet-balance: в ответе нет монеты USDT (нужен единый аккаунт UTA)")

    async def position_raw(self) -> dict:
        r = await self._ok("GET", "/v5/position/list", {"category": "linear", "symbol": SYMBOL})
        return r["list"][0] if r.get("list") else {}

    async def position(self) -> Position | None:
        p = await self.position_raw()
        size = float(p.get("size") or 0)
        if size <= 0:
            return None
        sl = float(p.get("stopLoss") or 0)
        return Position("long" if p["side"] == "Buy" else "short", size, float(p.get("avgPrice") or 0),
                        float(p.get("unrealisedPnl") or 0), float(p.get("markPrice") or 0), sl or None)

    async def open_orders(self) -> list[dict]:
        r = await self._ok("GET", "/v5/order/realtime", {"category": "linear", "symbol": SYMBOL, "limit": 50})
        return r.get("list", [])

    async def cancel_order(self, order_id: str) -> None:
        await self._ok("POST", "/v5/order/cancel", {"category": "linear", "symbol": SYMBOL, "orderId": order_id},
                       benign={110001})  # 110001 = ордера уже нет

    async def market(self, side: str, qty: float, reduce_only: bool, link_id: str) -> dict:
        inst = await self.instrument()
        body = {"category": "linear", "symbol": SYMBOL, "side": "Buy" if side == "buy" else "Sell",
                "orderType": "Market", "qty": fmt(qty, inst.qty_step), "reduceOnly": reduce_only,
                "orderLinkId": link_id, "positionIdx": 0}
        r = await self._ok("POST", "/v5/order/create", body)
        return {"orderId": r.get("orderId"), "orderLinkId": link_id}

    async def order_by_link(self, link_id: str) -> dict | None:
        r = await self._ok("GET", "/v5/order/realtime", {"category": "linear", "symbol": SYMBOL, "orderLinkId": link_id})
        lst = r.get("list", [])
        if not lst:  # ордер мог уйти в историю
            r = await self._ok("GET", "/v5/order/history", {"category": "linear", "symbol": SYMBOL, "orderLinkId": link_id})
            lst = r.get("list", [])
        if not lst:
            return None
        o = lst[0]
        return {"orderId": o.get("orderId"), "status": o.get("orderStatus"), "filled": float(o.get("cumExecQty") or 0),
                "avgPrice": float(o.get("avgPrice") or 0)}

    async def set_stop(self, price: float) -> None:
        inst = await self.instrument()
        await self._ok("POST", "/v5/position/trading-stop", {
            "category": "linear", "symbol": SYMBOL, "stopLoss": fmt(price, inst.tick), "slTriggerBy": "MarkPrice",
            "tpslMode": "Full", "positionIdx": 0}, benign={34040})  # 34040 = not modified

    async def closed_pnl(self, order_id: str | None, since_ms: int = 0) -> dict | None:
        """Только чтение: запись реализованного P/L закрытой позиции (GET /v5/position/closed-pnl, последние 10). Реальный
        closedPnl уже за вычетом комиссий и фандинга. order_id — id ЗАКРЫВАЮЩЕГО ордера (поиск по orderId); None — самая
        свежая запись с updatedTime >= since_ms (закрытие биржей: стоп/ликвидация). Нет записи (она может запаздывать на 1-3 с) -> None.
        Результат — pnl.normalize_record: плоский словарь с числами."""
        r = await self._ok("GET", "/v5/position/closed-pnl", {"category": "linear", "symbol": SYMBOL, "limit": 10})
        best = None
        for raw in r.get("list") or []:
            rec = normalize_record(raw)
            if order_id:
                if rec["order_id"] == order_id:
                    return rec
            elif rec["updated_ms"] >= since_ms and (best is None or rec["updated_ms"] > best["updated_ms"]):
                best = rec
        return best

    async def closed_pnl_list(self, since_ms: int = 0, limit: int = 100) -> list[dict]:
        """Только чтение: записи closed-pnl с updatedTime >= since_ms (GET /v5/position/closed-pnl, до limit штук, новые первыми).
        Окно запроса биржи — не больше 7 суток от startTime. Результат — pnl.normalize_record для каждой записи."""
        params = {"category": "linear", "symbol": SYMBOL, "limit": int(limit)}
        if since_ms:
            params["startTime"] = int(since_ms)
        r = await self._ok("GET", "/v5/position/closed-pnl", params)
        return [normalize_record(raw) for raw in (r.get("list") or [])]

    async def key_info(self) -> dict:
        """Права API-ключа (подписанный GET /v5/user/query-api): read_only (readOnly==1), withdraw (есть 'Withdraw' в
        permissions.Wallet), ips (привязка по IP; пусто/'*' — не ограничен)."""
        r = await self._ok("GET", "/v5/user/query-api")
        wallet = (r.get("permissions") or {}).get("Wallet") or []
        return {"read_only": int(r.get("readOnly") or 0) == 1,
                "withdraw": any(str(x).lower() == "withdraw" for x in wallet),
                "ips": [str(x) for x in (r.get("ips") or [])]}

    async def setup_account(self, leverage: float) -> dict:
        """Односторонний режим, ISOLATED, плечо; возвращает прочитанное с биржи.
        UTA: маржа проверяется через /v5/account/info (tradeMode в position/list у UTA устарел); классика — tradeMode."""
        info = await self._ok("GET", "/v5/account/info")
        uta = int(info.get("unifiedMarginStatus") or 0) >= 3
        await self._ok("POST", "/v5/position/switch-mode", {"category": "linear", "symbol": SYMBOL, "mode": 0},
                       benign=BENIGN_SETUP)
        lv = str(leverage)
        if uta:
            await self._ok("POST", "/v5/account/set-margin-mode", {"setMarginMode": "ISOLATED_MARGIN"}, benign=BENIGN_SETUP)
        else:
            await self._ok("POST", "/v5/position/switch-isolated", {
                "category": "linear", "symbol": SYMBOL, "tradeMode": 1, "buyLeverage": lv, "sellLeverage": lv},
                benign=BENIGN_SETUP)
        await self._ok("POST", "/v5/position/set-leverage", {"category": "linear", "symbol": SYMBOL, "buyLeverage": lv,
                                                              "sellLeverage": lv}, benign=BENIGN_SETUP)
        return await self.read_account()

    async def read_account(self) -> dict:
        """Только чтение: плечо, isolated, one-way как есть на бирже."""
        info = await self._ok("GET", "/v5/account/info")
        uta = int(info.get("unifiedMarginStatus") or 0) >= 3
        p = await self.position_raw()
        isolated = info.get("marginMode") == "ISOLATED_MARGIN" if uta else int(p.get("tradeMode", -1)) == 1
        return {"leverage": float(p.get("leverage") or 0), "isolated": isolated,
                "one_way": int(p.get("positionIdx", -1)) == 0}
