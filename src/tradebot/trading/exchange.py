"""Клиент Bybit v5 (async httpx, HMAC) и DryRunExchange — in-memory фейк для тестов/сухого прогона."""
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
TAKER_FEE = 0.001  # реальная комиссия taker (0.1%): DryRunExchange списывает её с каждого исполнения
# коды «уже установлено» при настройке аккаунта — не ошибка
BENIGN_SETUP = {110025, 110026, 110043, 34036, 3400045}


class ExchangeError(Exception):
    def __init__(self, msg: str, code: int = -1):
        super().__init__(msg)
        self.code = code


class PaperModeError(ExchangeError):
    """Попытка записи на биржу в режиме PAPER."""


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


class _GetOnlyTransport(httpx.AsyncBaseTransport):
    """Транспорт, физически не пропускающий ничего кроме GET (последний рубеж для PAPER)."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None):
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise PaperModeError("PAPER: не-GET запрос заблокирован транспортом")
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


class ReadOnlyClient:
    """Клиент для режима PAPER. Пересылает ТОЛЬКО чтение; методов записи у него нет — они бросают PaperModeError.
    Внутренний клиент собран на GET-only транспорте, так что даже обход обёртки не отправит запись."""

    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", transport: httpx.AsyncBaseTransport | None = None):
        self._c = BybitClient(base_url, api_key, api_secret, httpx.AsyncClient(timeout=10.0, transport=_GetOnlyTransport(transport)))

    def __repr__(self) -> str:
        return "ReadOnlyClient()"

    async def close(self): await self._c.close()
    async def sync_time(self) -> None: await self._c.sync_time()
    async def instrument(self) -> Instrument: return await self._c.instrument()
    async def mark_price(self) -> float: return await self._c.mark_price()
    async def equity(self) -> float: return await self._c.equity()
    async def sizing_balance(self) -> float: return await self._c.sizing_balance()
    async def position(self) -> Position | None: return await self._c.position()
    async def open_orders(self) -> list[dict]: return await self._c.open_orders()
    async def order_by_link(self, link_id: str) -> dict | None: return await self._c.order_by_link(link_id)

    async def key_info(self) -> dict:
        return await self._c.key_info()          # только GET; в PAPER Executor.setup() его не вызывает

    async def closed_pnl(self, order_id: str | None, since_ms: int = 0) -> dict | None:
        return await self._c.closed_pnl(order_id, since_ms)      # только GET

    async def closed_pnl_list(self, since_ms: int = 0, limit: int = 100) -> list[dict]:
        return await self._c.closed_pnl_list(since_ms, limit)    # только GET

    async def setup_account(self, leverage: float) -> dict:
        """Ничего не переключает: только читает текущие режим маржи/позиций и плечо."""
        return await self._c.read_account()

    # --- запись: заблокирована всегда ---
    async def market(self, *a, **k): raise PaperModeError("PAPER: ордера запрещены")
    async def set_stop(self, *a, **k): raise PaperModeError("PAPER: trading-stop запрещён")
    async def cancel_order(self, *a, **k): raise PaperModeError("PAPER: отмена запрещена")
    async def cancel_all(self, *a, **k): raise PaperModeError("PAPER: отмена запрещена")
    async def set_leverage(self, *a, **k): raise PaperModeError("PAPER: смена плеча запрещена")
    async def switch_mode(self, *a, **k): raise PaperModeError("PAPER: смена режима запрещена")


class DryRunExchange:
    """In-memory биржа: исполняет по mark, как Bybit обнуляет SL при закрытии позиции. Для тестов.

    Модель счёта (упрощённая UTA, isolated, one-way):
    * cash — walletBalance: реализованный PnL и комиссии, БЕЗ нереализованного PnL; equity = cash + uPnL;
    * маржа позиции = |qty| * средняя цена входа / плечо (плечо — то, что «стоит на бирже», account["leverage"]);
    * свободно для новых ордеров = cash - маржа позиции (нереализованную прибыль тратить нельзя);
    * ордер, увеличивающий позицию, отклоняется кодом 110007, если нужная начальная маржа + комиссии (открытие и закрытие)
      больше свободных USDT; reduce-only ордера маржу не проверяют;
    * комиссия taker (fee_rate, по умолчанию 0.1%) списывается с cash при каждом исполнении.
    Ликвидацию не моделирует (стоп отрабатывает helper stop_out)."""

    def __init__(self, equity: float = 1000.0, mark: float = 100_000.0, step: float = 0.001, min_qty: float = 0.001,
                 tick: float = 0.1, fee_rate: float = TAKER_FEE):
        self.inst = Instrument(step, min_qty, tick)
        self.mark, self.cash = mark, equity
        self.fee_rate = fee_rate
        self.fees_paid = 0.0
        self.qty = 0.0  # со знаком
        self.entry = 0.0
        self.stop: float | None = None
        self.orders: list[dict] = []  # исполненные ордера
        self.links: dict[str, dict] = {}
        self.cancelled = 0
        self.fail_next: str | None = None  # "before" | "after": таймаут до/после исполнения
        self.stop_noop = False  # SL не применяется (проверка read-back)
        self.account = {"leverage": 1.0, "isolated": True, "one_way": True}
        self.leverage_override: float | None = None
        self.stale_orders = 0
        self.protective_orders = 1  # SL-ордера, их чистка трогать не должна
        self.cancelled_ids: list[str] = []
        self.sizing_error: Exception | None = None   # тест: sizing_balance() падает
        self.sizing_calls = 0
        self.rejected: list[dict] = []               # отклонённые по марже ордера (тесты)
        self.closed: list[dict] = []                 # записи «закрытого P/L» (как Bybit closed-pnl), новые в конце
        self.closed_pnl_lag = 0                      # тест: первые N запросов closed_pnl вернут None (запись запаздывает)
        self.closed_pnl_off = False                  # тест: closed_pnl всегда None / ExchangeError (если closed_pnl_error)
        self.closed_pnl_error: Exception | None = None
        self.closed_pnl_hang = False                 # тест: closed_pnl_list() зависает (проверка таймаута добора)
        self.closed_pnl_calls = 0

    async def sync_time(self) -> None: ...
    async def instrument(self) -> Instrument: return self.inst
    async def mark_price(self) -> float: return self.mark

    def _upnl(self) -> float:
        return self.qty * (self.mark - self.entry) if self.qty else 0.0

    def _lev(self) -> float:
        return max(float(self.account.get("leverage") or 1.0), 1.0)

    def margin_used(self) -> float:
        """Начальная маржа открытой позиции (isolated): по средней цене входа."""
        return abs(self.qty) * self.entry / self._lev() if self.qty else 0.0

    def free_balance(self) -> float:
        """Свободно под новые ордера: кошелёк - маржа позиции - нереализованный УБЫТОК (консервативно; нереализованную
        прибыль тратить нельзя). Как именно UTA считает доступный баланс при убыточной isolated-позиции, не проверено."""
        return self.cash - self.margin_used() - max(0.0, -self._upnl())

    async def equity(self) -> float: return self.cash + self._upnl()

    async def sizing_balance(self) -> float:
        """Как BybitClient.sizing_balance: min(walletBalance, equity)."""
        self.sizing_calls += 1
        if self.sizing_error is not None:
            raise self.sizing_error
        return min(self.cash, self.cash + self._upnl())

    async def position(self) -> Position | None:
        if abs(self.qty) < 1e-12:
            return None
        return Position("long" if self.qty > 0 else "short", abs(self.qty), self.entry, self._upnl(), self.mark, self.stop)

    async def open_orders(self) -> list[dict]:
        out = [{"orderId": f"stale{i}", "stopOrderType": ""} for i in range(self.stale_orders)]
        if self.qty and self.stop:
            out += [{"orderId": f"sl{i}", "stopOrderType": "StopLoss"} for i in range(self.protective_orders)]
        return out

    async def cancel_order(self, order_id: str) -> None:
        self.cancelled_ids.append(order_id)
        if order_id.startswith("stale"):
            self.cancelled += 1
            self.stale_orders -= 1

    def _required_margin(self, sgn: int, qty: float) -> float:
        """Сколько свободных USDT нужно под ордер: прирост начальной маржи + комиссии открытия и закрытия."""
        lev, cur = self._lev(), self.qty
        new = cur + sgn * qty
        if cur == 0 or (cur > 0) == (sgn > 0):
            entry = (abs(cur) * self.entry + qty * self.mark) / (abs(cur) + qty)
            new_im = abs(new) * entry / lev
        elif abs(new) < 1e-12:
            new_im = 0.0
        elif (new > 0) != (cur > 0):
            new_im = abs(new) * self.mark / lev
        else:
            new_im = abs(new) * self.entry / lev
        return max(0.0, new_im - self.margin_used()) + 2 * qty * self.mark * self.fee_rate

    def _fill(self, sgn: int, qty: float, order_id: str | None = None, px: float | None = None) -> None:
        """Бухгалтерия исполнения по mark (или по px): средняя цена, реализованный PnL, комиссия; закрытая часть пишется в
        self.closed как запись closed-pnl (net = результат по ценам - комиссия входа - комиссия выхода, как у Bybit)."""
        px = self.mark if px is None else px
        new = self.qty + sgn * qty
        entry0, closed_qty, gross = self.entry, 0.0, 0.0
        if self.qty == 0 or (self.qty > 0) == (sgn > 0):  # увеличение: средняя цена
            self.entry = (abs(self.qty) * self.entry + qty * px) / (abs(self.qty) + qty)
        else:
            closed_qty = min(qty, abs(self.qty))
            gross = (-sgn) * closed_qty * (px - self.entry)
            self.cash += gross  # реализованный PnL
            if abs(new) > 1e-12 and (new > 0) != (self.qty > 0):
                self.entry = px
        fee = qty * px * self.fee_rate
        self.cash -= fee
        self.fees_paid += fee
        if closed_qty:
            self._record_close(order_id or f"x{len(self.closed) + 1}", sgn, closed_qty, entry0, px, gross)
        self.qty = 0.0 if abs(new) < 1e-12 else new
        if self.qty == 0:
            self.stop, self.entry = None, 0.0

    def _record_close(self, order_id: str, sgn: int, closed_qty: float, entry: float, px: float, gross: float) -> None:
        now_ms = int(time.time() * 1000)
        of, cf = closed_qty * entry * self.fee_rate, closed_qty * px * self.fee_rate
        self.closed.append({"order_id": order_id, "side": "buy" if sgn > 0 else "sell", "closed_pnl": gross - of - cf,
                            "open_fee": of, "close_fee": cf, "avg_entry": entry, "avg_exit": px, "qty": closed_qty,
                            "closed_size": closed_qty, "leverage": self._lev(), "created_ms": now_ms, "updated_ms": now_ms,
                            "exec_type": "Trade", "order_type": "Market", "cum_entry": entry * closed_qty,
                            "cum_exit": px * closed_qty, "fills": 1})

    async def closed_pnl_list(self, since_ms: int = 0, limit: int = 100) -> list[dict]:
        """Как BybitClient.closed_pnl_list: записи с updated_ms >= since_ms, новые первыми, не больше limit."""
        self.closed_pnl_calls += 1
        if self.closed_pnl_error is not None:
            raise self.closed_pnl_error
        if self.closed_pnl_hang:
            await asyncio.sleep(3600)
        return [dict(r) for r in reversed(self.closed) if r["updated_ms"] >= since_ms][:limit]

    async def closed_pnl(self, order_id: str | None, since_ms: int = 0) -> dict | None:
        """Как BybitClient.closed_pnl: запись по id закрывающего ордера (или самая свежая с updated_ms >= since_ms)."""
        self.closed_pnl_calls += 1
        if self.closed_pnl_error is not None:
            raise self.closed_pnl_error
        if self.closed_pnl_off:
            return None
        if self.closed_pnl_lag > 0:
            self.closed_pnl_lag -= 1
            return None
        if order_id:
            return next((dict(r) for r in reversed(self.closed) if r["order_id"] == order_id), None)
        return next((dict(r) for r in reversed(self.closed) if r["updated_ms"] >= since_ms), None)

    def _exec(self, side: str, qty: float, reduce_only: bool, link_id: str) -> dict:
        sgn = 1 if side == "buy" else -1
        if reduce_only:
            if self.qty == 0 or sgn == (1 if self.qty > 0 else -1):
                raise ExchangeError("reduce-only отклонён", 110017)
            qty = min(qty, abs(self.qty))
        else:
            need, free = self._required_margin(sgn, qty), self.free_balance()
            if need > free + 1e-9:  # Bybit: «недостаточно доступного баланса для нового ордера»
                self.rejected.append({"side": side, "qty": qty, "need": need, "free": free})
                raise ExchangeError(f"ab not enough for new order (нужно {need:.2f}, свободно {free:.2f})", 110007)
        oid = f"o{len(self.orders) + 1}"
        self._fill(sgn, qty, oid)
        o = {"orderId": oid, "orderLinkId": link_id, "side": side, "qty": qty,
             "reduce_only": reduce_only, "status": "Filled", "filled": qty, "avgPrice": self.mark}
        self.orders.append(o)
        self.links[link_id] = o
        return o

    async def market(self, side: str, qty: float, reduce_only: bool, link_id: str) -> dict:
        if link_id in self.links:
            raise ExchangeError("duplicate orderLinkId", 110072)
        if len(link_id) > 36:
            raise ExchangeError("orderLinkId слишком длинный", 10001)
        if qty < self.inst.min_qty - 1e-12:
            raise ExchangeError("qty < min", 10001)
        if self.fail_next == "before":
            self.fail_next = None
            raise AmbiguousOrder("timeout")
        o = self._exec(side, qty, reduce_only, link_id)
        if self.fail_next == "after":
            self.fail_next = None
            raise AmbiguousOrder("timeout")
        return {"orderId": o["orderId"], "orderLinkId": link_id}

    async def order_by_link(self, link_id: str) -> dict | None:
        o = self.links.get(link_id)
        return None if o is None else {"orderId": o["orderId"], "status": o["status"], "filled": o["filled"],
                                       "avgPrice": o["avgPrice"]}

    async def set_stop(self, price: float) -> None:
        if self.qty and not self.stop_noop:
            self.stop = round_down(price, self.inst.tick)

    async def key_info(self) -> dict:
        return {"read_only": False, "withdraw": False, "ips": ["127.0.0.1"]}   # разрешающий ответ: фейк без ключа

    async def setup_account(self, leverage: float) -> dict:
        self.account["leverage"] = self.leverage_override if self.leverage_override is not None else leverage
        return dict(self.account)

    # хелперы для тестов
    def stop_out(self) -> None:
        """Биржа закрыла позицию по стопу: убыток реализован (с комиссией taker), SL снят."""
        if self.qty:
            px = self.stop or self.mark
            self.cash += self.qty * (px - self.entry) - abs(self.qty) * px * self.fee_rate
            self._record_close(f"stop{len(self.closed) + 1}", -1 if self.qty > 0 else 1, abs(self.qty), self.entry, px,
                               self.qty * (px - self.entry))
        self.qty, self.entry, self.stop = 0.0, 0.0, None

    def external_trade(self, signed_qty: float) -> None:
        """Ручная сделка владельца в приложении Bybit (мимо бота, без записи в orders): +buy / -sell по mark."""
        if signed_qty:
            self._fill(1 if signed_qty > 0 else -1, abs(signed_qty), f"ext{len(self.closed) + 1}")
