"""Тестовый двойник биржи: in-memory DryRunExchange (используется только тестами; в src его нет)."""
from __future__ import annotations

import asyncio
import time

from tradebot.trading.exchange import TAKER_FEE, AmbiguousOrder, ExchangeError, Instrument, Position, round_down


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
