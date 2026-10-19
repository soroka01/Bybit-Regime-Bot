"""Исполнитель: приводит позицию на бирже к целевой экспозиции. Биржа — единственный источник истины о позиции.

Все пути, отправляющие ордера (раннер, закрытие из Telegram, паника, kill-switch), идут через один asyncio.Lock.
Размер позиции — от ВСЕГО баланса счёта (Exchange.sizing_balance), без «выделенного капитала». Его пересчитываем только
когда меняется целевая экспозиция (вход, переворот, смена веса/ноги), плечо или включена торговля; пока цель та же,
удерживаем сохранённый размер acted_qty (при цене/балансе, ушедших в сторону, доборов нет: при полной марже на счёте
биржа отклонила бы их кодом 110007). Мёртвая зона 10% сравнивает факт с acted_qty — это ловит внешние изменения
(ручное закрытие/сокращение). Известное расхождение с бэктестом: торгуем только при смене цели или такой внешней правке.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Callable

from tradebot.telegram import messages as M
from tradebot.trading import pnl, state
from tradebot.trading.config import MAX_LEVERAGE
from tradebot.trading.exchange import (AmbiguousOrder, Exchange, ExchangeError, Instrument, Position, round_down)

log = logging.getLogger("executor")
EPS = 1e-9
DEADBAND = 0.10  # допустимая ошибка фактического размера относительно удерживаемого acted_qty
# Доля баланса счёта, идущая в расчёт размера. Остаток 3% — технический резерв (НЕ пользовательский бюджет): комиссии taker
# 0.1% на открытие и на закрытие и дрейф цены между расчётом размера и исполнением, чтобы маржа не упёрлась в потолок (110007).
SIZING_EQUITY_FRACTION = 0.97
Notify = Callable[..., None]  # (level, text) или (level, text, data): data — структурное событие (см. telegram/messages.py)
PROTECTIVE = {"StopLoss", "TakeProfit", "TrailingStop", "PartialStopLoss", "PartialTakeProfit"}


# ---------- чистые функции (тестируются без биржи) ----------
def target_qty(expo: float, balance: float, leverage: float, price: float, inst: Instrument,
               fraction: float = SIZING_EQUITY_FRACTION) -> float:
    """Целевой размер в BTC со знаком: |экспозиция| * баланс счёта * fraction * плечо / цена, округление ВНИЗ к шагу.
    balance — Exchange.sizing_balance() (min(walletBalance, equity) по USDT); fraction — технический резерв
    (SIZING_EQUITY_FRACTION). Ниже minOrderQty или баланс <= 0 -> 0."""
    assert 0 < leverage <= MAX_LEVERAGE and price > 0 and 0 < fraction <= 1
    if not balance > 0:
        return 0.0
    q = round_down(abs(expo) * balance * fraction * leverage / price, inst.qty_step)
    if q < inst.min_qty - 1e-12:
        return 0.0
    return q if expo > 0 else -q


def needs_trade(acted: float | None, expo: float, cur: float, held: float) -> bool:
    """Мёртвая зона: экспозиция сменилась ИЛИ фактический размер cur отличается от удерживаемого held (acted_qty)
    более чем на 10%. held — не пересчитанный размер, а сохранённый при последнем исполнении."""
    if acted is None or abs(expo - acted) > EPS:
        return True
    if held == 0:
        return abs(cur) > 1e-12
    return abs(cur - held) > DEADBAND * abs(held)


def plan_orders(cur: float, tgt: float, inst: Instrument) -> list[tuple[str, float, bool]]:
    """[(side, qty, reduce_only)]. Переворот в one-way: сначала reduce-only на точный размер, затем новая сторона."""
    out: list[tuple[str, float, bool]] = []
    if abs(cur - tgt) < 1e-12:
        return out
    if cur != 0 and (tgt == 0 or (cur > 0) != (tgt > 0)):
        out.append(("sell" if cur > 0 else "buy", abs(cur), True))
        cur = 0.0
    d = round(tgt - cur, 9)     # без float-артефакта: 0.015 - 0.010 = 0.004999.. округлялось бы вниз на целый шаг
    q = round_down(abs(d), inst.qty_step)
    if q >= inst.min_qty - 1e-12:
        out.append(("buy" if d > 0 else "sell", q, tgt != 0 and abs(tgt) < abs(cur)))
    return out


def make_link_id(bar: datetime, seq: int, side: str, qty: float) -> str:
    """<= 36 символов: время бара + порядковый номер + короткий хэш."""
    h = hashlib.sha1(f"{bar.isoformat()}|{seq}|{side}|{qty}".encode()).hexdigest()[:8]
    return f"{bar:%y%m%d%H%M}-{seq}-{h}"[:36]


def stop_price(pos: Position, stop_pct: float, inst: Instrument) -> float:
    p = pos.entry * (1 - stop_pct) if pos.side == "long" else pos.entry * (1 + stop_pct)
    return round_down(p, inst.tick)


class Executor:
    def __init__(self, ex: Exchange, store: state.Store, notify: Notify, env_name: str, paper: bool = False):
        self.ex, self.store, self.notify, self.env = ex, store, notify, env_name
        self.paper = paper  # режим без ордеров: ни одной записи на биржу (молча, пользователю не показывается)
        self.lock = asyncio.Lock()
        self.trade_ok = False
        self.poll_delay = 0.5
        self.retry_wait = 1.0
        self.sent = 0                                   # подтверждённых исполнений за жизнь процесса (раннер сравнивает до/после)
        self.pnl_delays = (0.0, 1.0, 1.5, 2.0)          # паузы между попытками найти запись closed-pnl (она запаздывает на 1-3 с)
        # Общий бюджет ожидания записей closed-pnl на одно оповещение. ВНИМАНИЕ: оповещение идёт внутри торгового lock УЖЕ после
        # того, как стоп поставлен, но продлевает удержание lock: худший случай ≈ 6 с (записи) + 4 с (equity) + 4 с (позиция) ≈ 14 с.
        # Это допустимо: позиция к этому моменту защищена, а check_stopout/монитор просто пропускают такт, пока lock занят.
        self.pnl_budget_s = 6.0
        self._fills: list[dict] = []                    # исполнения, о которых ещё не сообщено владельцу
        self._stopouts: list[dict] = []                 # закрытия биржей (стоп/ликвидация), о которых ещё не сообщено
        self._results: list[dict] = []                  # итоги закрытий последнего _announce (для kill/panic)

    # ---------- уведомления ----------
    def _emit(self, level: str, data: dict) -> None:
        """Структурное уведомление (Notifier строит по data HTML; обычный Sink получает plain-текст)."""
        try:
            M.emit(self.notify, level, data)
        except Exception as e:  # уведомление никогда не должно ломать торговлю
            log.warning("notify: %s", e)

    # ---------- настройка аккаунта ----------
    async def setup(self) -> bool:
        if self.paper:  # только чтение: текущие режимы в журнал, ничего не меняем
            try:
                rb = await self.ex.setup_account(float(self.store.settings()["leverage"]))
                log.info("режим без ордеров: на бирже плечо %gx, isolated=%s, one-way=%s (только чтение)",
                         rb["leverage"], rb["isolated"], rb["one_way"])
            except Exception as e:
                log.warning("режим без ордеров: не удалось прочитать настройки аккаунта: %s", type(e).__name__)
            self.trade_ok = False
            return False
        lev = min(float(self.store.settings()["leverage"]), float(MAX_LEVERAGE))
        if not await self._key_ok():
            self.trade_ok = False
            return False
        try:
            rb = await self.ex.setup_account(lev)
        except Exception as e:
            self.trade_ok = False
            self.notify("error", M.w_setup_failed(e))
            return False
        ok = abs(rb["leverage"] - lev) < 1e-9 and rb["isolated"] and rb["one_way"]
        self.trade_ok = ok
        if not ok:
            self.notify("error", M.w_setup_mismatch(rb, lev))
        return ok

    async def _key_ok(self) -> bool:
        """Префлайт прав API-ключа до setup_account: ключ только на чтение или с правом вывода — торговля блокируется;
        привязка к IP не проверяется. Сбой самой проверки не блокирует (как раньше)."""
        try:
            ki = await self.ex.key_info()
        except Exception as e:
            self.notify("warn", M.w_key_check_failed(e))
            return True
        if ki.get("read_only"):
            self.notify("error", M.w_key_read_only())
            return False
        if ki.get("withdraw"):
            self.notify("error", M.w_key_withdraw())
            return False
        return True            # привязка к IP не требуется (владелец сидит за VPN с меняющимся адресом)

    async def paper_plan(self, target: float) -> dict:
        """«Что бы сделал бот» при текущих настройках: теоретическая позиция и стоп (только чтение цены). Пользователю не
        показывается; возможность оставлена для режима без ордеров."""
        s = self.store.settings()
        inst, price = await self.ex.instrument(), await self.ex.mark_price()
        bal = await self.ex.sizing_balance() if abs(target) > EPS else 0.0     # цель 0 — баланс не нужен
        q = target_qty(target, bal, float(s["leverage"]), price, inst)
        plan = {"target": target, "price": price, "side": None, "qty": 0.0, "stop": None, "note": ""}
        if q == 0:
            plan["note"] = "ниже minOrderQty — позиции не было бы" if abs(target) > EPS else "вне рынка"
        else:
            plan["side"], plan["qty"] = ("long" if q > 0 else "short"), abs(q)
            plan["stop"] = stop_price(Position(plan["side"], abs(q), price, 0, price), float(s["stop_pct"]), inst)
            plan["note"] = "стоп был бы выставлен"
        self.store.set("paper_plan", plan)
        return plan

    async def apply_leverage(self) -> bool:
        if self.paper:
            return False
        async with self.lock:
            return await self.setup()

    async def set_leverage(self, n: float) -> bool:
        """Смена плеча ОДНОЙ операцией под торговым lock: старое значение -> новое -> clear_acted -> setup().
        Инвариант: сверка (reconcile) держит тот же lock, поэтому её запись acted_* не может «перебить» сброс, сделанный сменой
        плеча (раньше сброс шёл без lock и гонялся с идущей сверкой). Не удалось применить на бирже (setup не подтвердил
        плечо/режимы) -> ОТКАТ: сохранённое плечо и acted_* возвращаются, прежнее плечо best-effort переустанавливается на
        бирже, владелец получает сообщение. Уменьшение плеча не запрещаем — при неудаче откат. True — плечо применено."""
        st = self.store
        if self.paper:  # режим без ордеров: настройка сохраняется, биржа не трогается
            st.set_setting("leverage", n)
            st.clear_acted()
            return False
        async with self.lock:
            old = st.settings()["leverage"]
            old_acted = (st.get("acted_exposure"), st.get("acted_qty"))
            st.set_setting("leverage", n)          # ValueError (вне диапазона) -> ничего не изменено
            st.clear_acted()                       # размер заново от свежего баланса при ближайшей сверке
            ok = await self.setup()
            if ok:
                return True
            st.set_setting("leverage", old)
            st.set_acted(*old_acted)
            back = await self.setup()              # best-effort: вернуть на бирже прежнее плечо
            self.notify("error", f"Плечо {float(n):g}x не применилось на бирже — оставлено прежнее {float(old):g}x"
                                 + ("." if back else ", но биржа его тоже не подтвердила: торговля заблокирована, проверьте настройки."))
            return False

    # ---------- чтение ----------
    async def refresh_cache(self) -> Position | None:
        """Обновляет кэш позиции/эквити для снимка (без блокировки: только чтение и локальные записи в kv)."""
        pos = await self.ex.position()
        self.store.set("position", pos.as_dict() if pos else None)
        self.store.set("pos_stop", pos.stop if pos else None)
        if pos is not None:
            self.store.set("pos_seen_ms", int(time.time() * 1000))
            self._remember_pos(pos)
            if not self.lock.locked():
                self._ensure_trip()
        return pos

    def _remember_pos(self, pos: Position | None) -> None:
        """Последний снимок ОТКРЫТОЙ позиции (kv pos_last): монитор сначала обновляет кэш (position=None, когда позиции уже нет),
        и только потом check_stopout — без этого снимка у стоп-аута не осталось бы ни стороны, ни размера, ни цены стопа.
        Пишется только когда позиция есть; очищается, когда закрытие объявлено и записано (или бот закрыл позицию сам)."""
        if pos is None:
            return
        prev = self.store.get("pos_last") or {}
        stop = pos.stop or (prev.get("stop") if prev.get("side") == pos.side else None)
        self.store.set("pos_last", {"side": pos.side, "qty": pos.qty, "entry": pos.entry, "stop": stop,
                                    "ts": state.now_iso()})

    # ---------- учёт сделки (круг: вход -> ... -> выход) ----------
    def _acc0(self) -> dict:
        return {"realized": 0.0, "fees": 0.0, "funding": 0.0, "approx": False, "reds": 0, "closed_qty": 0.0, "exit_value": 0.0}

    def _ensure_trip(self) -> None:
        """Позиция уже есть, а данных о входе нет (она открыта до этой версии бота): восстановить локально, без сети.
        Эквити на входе — последняя точка кривой equity до первого входного исполнения; нет её — текущее equity минус
        нереализованный результат. Всё это оценка (pos_entry_eq_est=True)."""
        st = self.store
        if st.get("pos_entry_ts") is not None:
            return
        steps, _, ot = pnl.replay(st.all_trades(), breaks=st.stop_breaks())        # с разрывами от стоп-аутов мимо ленты
        ts_open = ot["ts_open"] if ot else state.now_iso()
        eq = st.equity_before(ts_open) if ot else None
        if eq is None and st.get("equity") is not None:
            pos = st.get("position") or {}
            eq = float(st.get("equity")) - float(pos.get("upnl") or 0.0)
        acc = self._acc0()
        if ot and ot["closed_qty"] > 0:             # были частичные сокращения: их результат — оценка по ленте
            red = [x for x in steps if x["closed_qty"] > 0 and str(x["fill"]["ts"]) >= str(ot["ts_open"])]
            acc.update(realized=sum(x["net"] for x in red), approx=True, reds=len(red), closed_qty=ot["closed_qty"],
                       exit_value=ot["exit_value"])
        st.set("pos_entry_ts", ts_open)
        st.set("pos_entry_equity", eq)
        st.set("pos_entry_eq_est", True)
        st.set("pos_entry_lev", float(st.settings()["leverage"]))
        st.set("pos_trip", acc)

    def _clear_trip(self) -> None:
        for k in ("pos_entry_ts", "pos_entry_equity", "pos_entry_eq_est", "pos_entry_lev", "pos_trip"):
            self.store.set(k, None)

    def _snapshot(self) -> dict:
        self._ensure_trip()
        st = self.store
        return {"entry_equity": st.get("pos_entry_equity"), "eq_est": bool(st.get("pos_entry_eq_est")),
                "ts_open": st.get("pos_entry_ts"), "lev": st.get("pos_entry_lev") or float(st.settings()["leverage"]),
                "acc": {**self._acc0(), **(st.get("pos_trip") or {})}}

    # ---------- ордера ----------
    async def _sizing_base(self) -> float:
        """Свежий баланс счёта для расчёта размера. Любой сбой или баланс <= 0 -> ExchangeError (ордеров нет, никаких
        запасных значений из kv: устаревший баланс опаснее пропущенного бара)."""
        try:
            b = float(await self.ex.sizing_balance())
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"баланс счёта недоступен: {type(e).__name__}: {e}") from None
        if not (b > 0) or b == float("inf"):
            raise ExchangeError(f"баланс счёта для расчёта размера некорректен ({b})")
        return b

    def _held_qty(self, target: float, cur: float) -> float | None:
        """Удерживаемый размер, когда acted_qty ещё не записан (первая сверка после обновления бота). Цель 0 -> 0.
        Позиция есть и сонаправлена цели -> ПРИНИМАЕМ её размер как acted_qty и ничего не торгуем (иначе живую позицию
        пересчитало бы при рестарте). Иначе None -> размер считается заново."""
        if abs(target) <= EPS:
            return 0.0
        if cur != 0 and (cur > 0) == (target > 0):
            self.store.set("acted_qty", cur)
            log.info("размер текущей позиции %s BTC принят как удерживаемый (обновление бота)", abs(cur))
            return cur
        return None

    async def _send(self, side: str, qty: float, reduce_only: bool, bar: datetime, reason: str, before: float,
                    eq_hint: float | None = None) -> None:
        """Отправляет один рыночный ордер и подтверждает исполнение. Инвариант: возвращается либо с УЧТЁННЫМ исполнением, либо
        бросает ExchangeError — тогда остаток плана (например, открывающая нога переворота) отменяется, а acted_* не пишутся.
        Неподтверждённый ордер раньше молча считался выполненным, и сверка записывала acted_* по несуществующей позиции."""
        link = make_link_id(bar, self.store.next_seq(), side, qty)
        t0_ms = int(time.time() * 1000)
        try:
            await self.ex.market(side, qty, reduce_only, link)
        except AmbiguousOrder:
            o = None
            for _ in range(3):  # даём бирже время; потом смотрим ордер по orderLinkId
                o = await self.ex.order_by_link(link)
                if o is not None:
                    break
                await asyncio.sleep(self.retry_wait)
            if o is None:
                if await self._book_by_delta(side, link, reason, before, eq_hint, t0_ms):  # позиция изменилась — ордер дошёл
                    log.warning("ордер %s неоднозначен, но позиция изменилась", link)
                    self.notify("warn", M.w_order_ambiguous())
                    return
                try:
                    await self.ex.market(side, qty, reduce_only, link)  # тот же link: дубль биржа отклонит
                except AmbiguousOrder:
                    raise ExchangeError(f"не удалось подтвердить ордер {link}") from None
                except ExchangeError as e:
                    if e.code != 110072:  # 110072 = дубликат orderLinkId -> ордер существует
                        raise
        except ExchangeError as e:
            if e.code != 110072:
                raise
        price, filled, oid = 0.0, 0.0, link
        for _ in range(6):
            o = await self.ex.order_by_link(link)
            if o and o["status"] in ("Filled", "PartiallyFilledCanceled"):
                price, filled, oid = o["avgPrice"], o["filled"], o["orderId"] or link
                break
            await asyncio.sleep(self.poll_delay)
        if filled <= 0:
            self.notify("warn", M.w_order_unconfirmed())
            if await self._book_by_delta(side, link, reason, before, eq_hint, t0_ms):
                return                              # исполнился с опозданием: учтено по изменению позиции
            raise ExchangeError(f"ордер {link} не подтверждён и позиция не изменилась — остаток плана отменён")
        self._record_fill(side, filled, price, oid, reason, before, eq_hint, t0_ms)

    async def _book_by_delta(self, side: str, link: str, reason: str, before: float, eq_hint: float | None,
                             t0_ms: int) -> bool:
        """Ордер не подтверждён по orderLinkId — перечитать позицию. Изменилась в сторону ордера -> исполнение учитывается по
        разнице позиций (цена — mark, приближённо) и True. Не изменилась -> False. Изменилась в ДРУГУЮ сторону (внешняя правка,
        стоп-аут) -> ExchangeError: это не наш ордер, продолжать план нельзя."""
        pos = await self.ex.position()
        delta = (pos.signed if pos else 0.0) - before
        if abs(delta) <= 1e-12:
            return False
        if delta * (1 if side == "buy" else -1) < 0:
            raise ExchangeError(f"ордер {link} не подтверждён, а позиция изменилась в другую сторону ({delta:+g}) — остаток плана отменён")
        price = (pos.mark if pos and pos.mark else 0.0) or await self.ex.mark_price()
        self._record_fill(side, round(abs(delta), 9), price, link, reason, before, eq_hint, t0_ms)
        return True

    def _record_fill(self, side: str, filled: float, price: float, oid: str, reason: str, before: float,
                     eq_hint: float | None, t0_ms: int) -> None:
        self.store.add_trade(side, filled, price, reason, oid)
        self.sent += 1
        try:
            self._book(side, filled, price, oid, reason, before, eq_hint, t0_ms)
        except Exception:
            log.exception("учёт исполнения (на торговлю не влияет)")

    def _book(self, side: str, filled: float, price: float, oid: str, reason: str, before: float, eq_hint: float | None,
              t0_ms: int) -> None:
        """Локальная бухгалтерия исполнения (без сети): классификация открытие/увеличение/сокращение/закрытие, эквити на входе,
        снимок данных сделки для закрытия. Сообщение владельцу строит _announce уже после того, как позиция защищена стопом."""
        st = self.store
        sgn = 1 if side == "buy" else -1
        after = round(before + sgn * filled, 9)
        if abs(after) < 1e-9:
            after = 0.0
        base = {"order_id": oid, "reason": reason, "price": price, "ts": state.now_iso(), "since_ms": t0_ms - 5000}
        legs: list[dict] = []
        if before != 0:
            ps = "long" if before > 0 else "short"
            if after == 0 or (after > 0) != (before > 0):
                legs.append({**base, "kind": "close", "pos_side": ps, "qty": abs(before), "trip": self._snapshot()})
                self._clear_trip()
                # Инвариант: закрытие, начатое самим ботом, не должно выглядеть как «позиция исчезла». stop_armed гасим здесь же,
                # а не позже в rearm_quietly: сбой между закрытием и перевзводом иначе оставил бы stop_armed=True без позиции, и
                # после рестарта монитор объявил бы ложный стоп-аут.
                st.set("pos_last", None)
                st.set("stop_armed", False)
            elif abs(after) < abs(before):
                legs.append({**base, "kind": "reduce", "pos_side": ps, "qty": round(abs(before) - abs(after), 9),
                             "total": abs(after), "trip": self._snapshot()})
            else:
                legs.append({**base, "kind": "increase", "pos_side": ps, "qty": round(abs(after) - abs(before), 9),
                             "total": abs(after)})
        if before == 0 or (after != 0 and (after > 0) != (before > 0)):
            if after != 0:
                eq = eq_hint if eq_hint else st.get("equity")
                lev = float(st.settings()["leverage"])
                st.set("pos_entry_ts", base["ts"])
                st.set("pos_entry_equity", eq)
                st.set("pos_entry_eq_est", not eq_hint)
                st.set("pos_entry_lev", lev)
                st.set("pos_trip", self._acc0())
                legs.append({**base, "kind": "open", "pos_side": "long" if after > 0 else "short", "qty": abs(after),
                             "eq": eq, "lev": lev})
        if after != 0:
            st.set("pos_seen_ms", int(time.time() * 1000))
        self._fills.extend(legs)

    async def cleanup_orders(self) -> int:
        """Отменяет висящие ордера по orderId, кроме защитных (SL/TP/trailing). Возвращает число отменённых."""
        n = 0
        for o in await self.ex.open_orders():
            if o.get("stopOrderType") in PROTECTIVE:
                continue
            await self.ex.cancel_order(o["orderId"])
            n += 1
        return n

    async def rearm_quietly(self) -> None:
        """Best-effort: после любой операции проверить/поставить биржевой стоп; ошибки не пробрасываем."""
        if self.paper:
            return
        try:
            await self._ensure_stop(await self.ex.position())
        except Exception as e:
            log.warning("re-arm stop: %s", e)
            self.notify("error", M.w_rearm_failed(e))

    async def _ensure_stop(self, pos: Position | None, force: bool = False) -> None:
        """Ставит и проверяет read-back SL. При неудаче — закрывает позицию (без защиты не держим)."""
        if pos is None:
            self.store.set("stop_armed", False)
            return
        inst = await self.ex.instrument()
        want = stop_price(pos, float(self.store.settings()["stop_pct"]), inst)
        tol = max(inst.tick * 2, want * 0.0005)
        if not force and pos.stop is not None and abs(pos.stop - want) <= tol:
            self.store.set("stop_armed", True)
            self.store.set("pos_stop", pos.stop)
            self._remember_pos(pos)
            return
        for _ in range(3):
            try:
                await self.ex.set_stop(want)
                p2 = await self.ex.position()
            except ExchangeError as e:
                log.warning("set_stop: %s", e)
                continue
            if p2 is None:
                self.store.set("stop_armed", False)
                return
            if p2.stop is not None and abs(p2.stop - want) <= tol:
                self.store.set("stop_armed", True)
                self.store.set("pos_stop", p2.stop)
                self._remember_pos(p2)
                return
            await asyncio.sleep(self.poll_delay)
        self.notify("error", M.w_stop_failed())
        await self._flatten("нет стопа на бирже", protect=False)
        self.store.set_setting("enabled", False)

    async def _flatten(self, reason: str, bar: datetime | None = None, protect: bool = True) -> None:
        """Закрывает позицию reduce-only. Если не вышло — оставляем/ставим биржевой стоп (protect)."""
        try:
            for _ in range(2):
                pos = await self.ex.position()
                if pos is None:
                    break
                await self._send("sell" if pos.side == "long" else "buy", pos.qty, True,
                                 bar or datetime.now(timezone.utc), reason, pos.signed)
            pos = await self.ex.position()
        except ExchangeError:
            if protect:
                await self.rearm_quietly()
            raise
        if pos is not None:
            self.notify("error", M.w_not_flat(pos.side, pos.qty))
            if protect:
                await self.rearm_quietly()
        else:
            self.store.set("stop_armed", False)
        self.store.set("position", pos.as_dict() if pos else None)

    # ---------- оповещения о сделках ----------
    async def _lookup_pnl(self, order_id: str | None, since_ms: int, deadline: float, once: bool = False) -> dict | None:
        """Запись реализованного P/L с биржи (closed-pnl) с повторами: запись бывает с задержкой 1-3 с. Общий бюджет —
        до deadline (monotonic); хотя бы одна попытка делается всегда. None — записи нет (или биржа её не умеет)."""
        fn = getattr(self.ex, "closed_pnl", None)
        if fn is None:
            return None
        for d in ((0.0,) if once else self.pnl_delays):
            if d:
                if time.monotonic() + d > deadline:
                    break
                await asyncio.sleep(d)
            try:
                rec = await asyncio.wait_for(fn(order_id, since_ms), timeout=max(0.3, min(3.0, deadline - time.monotonic())))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("closed-pnl: %s", type(e).__name__)
                rec = None
            if rec:
                return rec
        return None

    async def _equity_now(self, ctx: dict) -> float | None:
        if "equity" not in ctx:
            try:
                ctx["equity"] = float(await asyncio.wait_for(self.ex.equity(), 4))
            except Exception:
                ctx["equity"] = self.store.get("equity")
        return ctx["equity"]

    async def _position_now(self, ctx: dict) -> Position | None:
        if "pos" not in ctx:
            try:
                ctx["pos"] = await asyncio.wait_for(self.ex.position(), 4)
            except Exception:
                ctx["pos"] = None
        return ctx["pos"]

    async def _announce(self, quiet: bool = False) -> list[dict]:
        """Сообщить владельцу о накопленных закрытиях биржей и исполнениях. Вызывается ПОСЛЕ того, как позиция защищена стопом,
        внутри торгового lock; ожидание записей биржи ограничено бюджетом pnl_budget_s. Не бросает исключений.
        quiet=True (kill/panic): закрытия не оглашаются, а возвращаются итогами — их вставит в своё сообщение вызывающий."""
        stops, fills = self._stopouts, self._fills
        self._stopouts, self._fills = [], []
        results: list[dict] = []
        if not stops and not fills:
            self._results = results
            return results
        deadline = time.monotonic() + self.pnl_budget_s
        ctx: dict = {}
        for s in stops:
            try:
                await self._announce_stopout(s, deadline, ctx)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("оповещение о закрытии биржей")
        for f in fills:
            try:
                r = await self._announce_fill(f, deadline, ctx, quiet)
                if r:
                    results.append(r)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("оповещение об исполнении")
        self._results = results
        return results

    def _close_numbers(self, f: dict, rec: dict | None) -> dict:
        """Реализованный P/L одного закрывающего исполнения: запись биржи (точно) либо оценка по ленте сделок (≈)."""
        if rec:
            p = pnl.record_parts(rec)
            return {"net": p["net"], "fees": p["fees"], "funding": p["funding"], "entry": rec["avg_entry"] or None,
                    "exit": rec["avg_exit"] or f["price"], "exact": True, "lev": rec.get("leverage") or None}
        steps, _, _ = pnl.replay(self.store.all_trades())
        step = pnl.step_for(steps, f["order_id"])
        if step is None:
            return {"net": None, "fees": None, "funding": None, "entry": None, "exit": f["price"], "exact": False, "lev": None}
        return {"net": step["net"], "fees": step["fee_open"] + step["fee_close"], "funding": None,
                "entry": step["avg_before"], "exit": f["price"], "exact": False, "lev": None}

    async def _announce_fill(self, f: dict, deadline: float, ctx: dict, quiet: bool) -> dict | None:
        kind, side = f["kind"], f["pos_side"]
        st = self.store
        if kind == "open":
            pos = await self._position_now(ctx)
            stop = pos.stop if pos and pos.stop else None
            if stop is None:
                try:
                    inst = await self.ex.instrument()
                    stop = stop_price(Position(side, f["qty"], f["price"], 0, f["price"]), float(st.settings()["stop_pct"]), inst)
                except Exception:
                    stop = None
            self._emit("trade", {"kind": "open", "side": side, "qty": f["qty"], "price": f["price"], "leverage": f["lev"],
                                 "equity": f["eq"], "stop": stop, "reason": f["reason"]})
            return None
        if kind == "increase":
            pos = await self._position_now(ctx)
            self._emit("trade", {"kind": "increase", "side": side, "delta": f["qty"], "price": f["price"],
                                 "total_qty": pos.qty if pos else f["total"], "avg_entry": pos.entry if pos else None,
                                 "leverage": float(st.settings()["leverage"]), "equity": await self._equity_now(ctx),
                                 "reason": f["reason"]})
            return None
        # reduce / close: P/L берём с биржи
        rec = await self._lookup_pnl(f["order_id"], f["since_ms"], deadline)
        c = self._close_numbers(f, rec)
        snap = f["trip"]
        acc = dict(snap["acc"])
        net = c["net"]
        if kind == "reduce":
            if net is not None:
                acc.update(realized=acc["realized"] + net, fees=acc["fees"] + (c["fees"] or 0.0),
                           funding=acc["funding"] + (c["funding"] or 0.0), reds=acc["reds"] + 1,
                           closed_qty=acc["closed_qty"] + f["qty"], exit_value=acc["exit_value"] + f["qty"] * c["exit"])
            acc["approx"] = acc["approx"] or not c["exact"]
            if st.get("pos_entry_ts") == snap["ts_open"]:
                st.set("pos_trip", acc)
            if not quiet:
                self._emit("trade", {"kind": "reduce", "side": side, "delta": f["qty"], "price": f["price"],
                                     "total_qty": f["total"], "pnl": net, "exact": c["exact"], "reason": f["reason"],
                                     "equity": await self._equity_now(ctx)})
            return {"pnl": net, "exact": c["exact"], "pct": (net / snap["entry_equity"]) if (net is not None and snap["entry_equity"]) else None}
        # полное закрытие: итог всей сделки
        total = (acc["realized"] + (net or 0.0)) if net is not None else None
        qty_total = acc["closed_qty"] + f["qty"]
        exit_px = (acc["exit_value"] + f["qty"] * c["exit"]) / qty_total if qty_total else c["exit"]
        entry_px = c["entry"] or (st.get("position") or {}).get("entry") or f["price"]
        exact = c["exact"] and not acc["approx"]
        eq0 = snap["entry_equity"]
        lev = c["lev"] or snap["lev"]
        margin = entry_px * qty_total / lev if lev else None
        t_open = pnl.parse_ts(snap["ts_open"])
        t_close = pnl.parse_ts(f["ts"])
        held = (t_close - t_open) if (t_open and t_close) else None
        fees = (acc["fees"] + c["fees"]) if c["fees"] is not None else None
        funding = (acc["funding"] + c["funding"]) if c["funding"] is not None else None
        pct = (total / eq0) if (total is not None and eq0 and eq0 > 0) else None
        roe = (total / margin) if (total is not None and margin) else None
        equity = await self._equity_now(ctx)
        st.add_roundtrip(ts_open=snap["ts_open"], ts_close=f["ts"], side=side, qty=qty_total, entry=entry_px, exit=exit_px,
                         pnl=total, fees=fees, funding=funding, equity_open=eq0, eq_est=int(snap["eq_est"]),
                         approx=int(not exact), kind="close", reason=f["reason"], order_id=f["order_id"])
        res = {"pnl": total, "exact": exact, "pct": pct}
        if not quiet and total is not None:
            self._emit("trade", {"kind": "close", "side": side, "qty": qty_total, "entry": entry_px, "exit": exit_px,
                                 "pnl": total, "fees": fees, "funding": funding, "exact": exact, "pct": pct,
                                 "pct_est": snap["eq_est"], "roe": roe, "held_s": held, "reason": f["reason"], "equity": equity,
                                 "reds": acc["reds"]})
        return res

    # ---------- добор точных итогов за недавние сделки ----------
    async def backfill_pnl(self, days: float = 7.0, limit: int = 100, timeout: float = 6.0) -> dict:
        """Идемпотентно добирает точные итоги (closed-pnl Bybit) для закрытых сделок последних `days` суток, у которых в roundtrips
        нет точной строки (сделки, закрытые старой версией бота). Только чтение с биржи + запись в roundtrips; Telegram не шумит
        (результат — в лог INFO). Не бросает: сбой/таймаут биржи -> {'skipped': причина}, ничего не меняется; старше окна — оценки."""
        fn = getattr(self.ex, "closed_pnl_list", None)
        if fn is None:
            return {"skipped": "нет метода"}
        try:
            since_ms = int((time.time() - days * 86400) * 1000)
            recs = await asyncio.wait_for(fn(since_ms, limit), timeout)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            log.info("добор итогов сделок: биржа не ответила за %.0f с — пропускаю", timeout)
            return {"skipped": "таймаут"}
        except Exception as e:
            log.info("добор итогов сделок пропущен: %s", type(e).__name__)
            return {"skipped": type(e).__name__}
        try:
            st = self.store
            plan = pnl.plan_backfill(recs or [], st.all_trades(), st.roundtrip_rows(), breaks=st.stop_breaks())
            res = st.apply_backfill(plan)
        except Exception:
            log.exception("добор итогов сделок")
            return {"skipped": "ошибка"}
        if res["inserted"] or res["updated"]:
            for it in plan:
                log.info("добор итога сделки %s: %+.4f $ (комиссии %.4f, фандинг %+.4f) ордер %s", it["ts_close"], it["pnl"], it["fees"],
                         it["funding"], it["order_id"])
        log.info("добор итогов сделок: записей биржи %d, добавлено %d, обновлено %d, без изменений %d", len(recs or []),
                 res["inserted"], res["updated"], res["skipped"])
        return res

    # ---------- стоп-аут ----------
    def detect_stopout(self, pos: Position | None, target: float) -> bool:
        """Позиции нет, хотя мы её держали со стопом -> закрыла биржа (стоп/ликвидация/ручное закрытие). Если цель не менялась —
        защёлка (latch). Само сообщение (с суммой P/L) уходит позже из _announce, когда найдена запись биржи."""
        st = self.store
        if pos is not None or not st.get("stop_armed", False):
            return False
        st.set("stop_armed", False)
        acted = st.get("acted_exposure")
        latched = abs(target) > EPS and acted is not None and abs(target - acted) < EPS
        if latched:
            st.set_latch(target)
        # монитор уже обнулил position/pos_stop (позиции нет): берём последний снимок открытой позиции
        last = st.get("pos_last") or st.get("position") or {}
        self._stopouts.append({"last": dict(last), "latched": latched, "side": last.get("side"), "qty": last.get("qty"), "entry": last.get("entry"),
                               "stop": last.get("stop") or st.get("pos_stop"), "since_ms": int(st.get("pos_seen_ms") or 0) - 5000,
                               "trip": self._snapshot() if st.get("pos_entry_ts") is not None or last else None,
                               "ts": state.now_iso()})
        self._clear_trip()
        return latched

    async def _announce_stopout(self, s: dict, deadline: float, ctx: dict) -> None:
        st = self.store
        known = {t["order_id"] for t in st.trades(100)}
        rec = await self._lookup_pnl(None, s["since_ms"], deadline)
        if rec and rec["order_id"] in known:
            rec = None                         # это наше собственное закрытие, не стоп-аут
        side = s["side"] or (("long" if rec["side"] == "sell" else "short") if rec else None)    # снимка нет — по закрывающему ордеру
        snap = s["trip"] or {"entry_equity": None, "eq_est": True, "ts_open": s["ts"], "lev": None, "acc": self._acc0()}
        acc = snap["acc"]
        data = {"kind": "stopout", "side": side, "qty": s["qty"], "entry": s["entry"], "exit": None, "latched": s["latched"],
                "exit_kind": pnl.exit_kind(rec, s["stop"], side or "long"), "equity": await self._equity_now(ctx),
                "pct_est": snap["eq_est"], "pnl": None, "exact": True}
        rt = {"ts_open": snap["ts_open"], "ts_close": s["ts"], "side": side, "qty": s["qty"], "entry": s["entry"],
              "exit": None, "pnl": None, "fees": None, "funding": None, "equity_open": snap["entry_equity"],
              "eq_est": int(snap["eq_est"]), "approx": 1, "kind": data["exit_kind"], "reason": "закрыто биржей", "order_id": None}
        if rec:
            p = pnl.record_parts(rec)
            total = acc["realized"] + p["net"]
            eq0 = snap["entry_equity"]
            data.update(pnl=total, exit=rec["avg_exit"], entry=rec["avg_entry"] or s["entry"], fees=acc["fees"] + p["fees"],
                        funding=acc["funding"] + p["funding"], exact=not acc["approx"],
                        pct=(total / eq0) if eq0 and eq0 > 0 else None, qty=rec["closed_size"] or s["qty"])
            rt.update(exit=rec["avg_exit"], pnl=total, fees=data["fees"], funding=data["funding"], approx=int(acc["approx"]),
                      order_id=rec["order_id"], entry=data["entry"], qty=data["qty"])
            st.add_roundtrip(**rt)
        else:
            rid = st.add_roundtrip(**rt)
            pend = st.get("pending_stopouts") or []
            pend.append({"rt_id": rid, "since_ms": s["since_ms"], "created_ms": int(time.time() * 1000),
                         "data": {k: data[k] for k in ("side", "qty", "entry", "latched", "exit_kind", "pct_est")},
                         "acc": acc, "eq0": snap["entry_equity"], "stop": s["stop"]})
            st.set("pending_stopouts", pend)
        if st.get("pos_last") == s.get("last"):         # закрытие объявлено и записано; новая позиция (если уже есть) не трогается
            st.set("pos_last", None)
        self._emit("error", data)

    async def _followup_stopout(self) -> None:
        """Записи о стоп-аутах не нашлись сразу: на следующих тиках мониторинга ищем их (без пауз) и досылаем уточнения.
        Ожидающих может быть несколько (второй стоп-аут не затирает первый): идём от старых к новым; запись, появившаяся уже
        после начала следующей позиции, старому стоп-ауту не принадлежит. Известное ограничение: если позиция после стоп-аута
        уже закрыта самим ботом, «самая свежая» запись — его собственная, и уточнение ждёт (до 2 ч, затем отбрасывается)."""
        st = self.store
        pend = st.get("pending_stopouts") or []
        if not pend:
            return
        now_ms = time.time() * 1000
        pend = [p for p in pend if now_ms - p["created_ms"] <= 2 * 3600 * 1000]
        left: list[dict] = []
        used = {t["order_id"] for t in st.trades(100)} | {r["order_id"] for r in st.roundtrip_rows() if r["order_id"]}
        for k, pe in enumerate(pend):
            rec = await self._lookup_pnl(None, pe["since_ms"], time.monotonic() + 3, once=True)
            later = pend[k + 1:]
            if rec and later and rec["updated_ms"] >= later[0]["since_ms"] + 5000:
                rec = None                      # это запись следующего стоп-аута
            if not rec or rec["order_id"] in used:
                left.append(pe)
                continue
            used.add(rec["order_id"])
            await self._send_followup(pe, rec)
        st.set("pending_stopouts", left)

    async def _send_followup(self, pend: dict, rec: dict) -> None:
        st = self.store
        p = pnl.record_parts(rec)
        acc = {**self._acc0(), **pend["acc"]}
        total = acc["realized"] + p["net"]
        eq0 = pend["eq0"]
        d = pend["data"]
        data = {"kind": "stopout", "followup": True, **d, "exit": rec["avg_exit"], "entry": rec["avg_entry"] or d.get("entry"),
                "qty": rec["closed_size"] or d.get("qty"), "pnl": total, "fees": acc["fees"] + p["fees"],
                "funding": acc["funding"] + p["funding"], "exact": not acc["approx"], "latched": False,
                "pct": (total / eq0) if eq0 and eq0 > 0 else None, "exit_kind": pnl.exit_kind(rec, pend.get("stop"), d.get("side") or "long"),
                "equity": await self._equity_now({})}
        st.update_roundtrip(pend["rt_id"], exit=rec["avg_exit"], pnl=total, fees=data["fees"], funding=data["funding"],
                            approx=int(acc["approx"]), order_id=rec["order_id"], kind=data["exit_kind"])
        self._emit("error", data)

    async def check_stopout(self) -> None:
        """Быстрая проверка из мониторинга (пропускается, если идёт торговая операция). Инвариант: НЕ зависит от enabled —
        стоп-аут при выключенной торговле тоже должен быть обнаружен и объявлен (иначе позиция «исчезла» молча)."""
        if self.paper or self.lock.locked():
            return
        async with self.lock:
            pos = await self.ex.position()
            self.detect_stopout(pos, self.store.get("target_exposure", 0.0) or 0.0)
            await self._announce()
            await self._followup_stopout()

    # ---------- основной путь ----------
    async def reconcile(self, target: float, bar: datetime | None = None, reason: str = "сигнал") -> str:
        async with self.lock:
            try:
                return await self._reconcile(target, bar or datetime.now(timezone.utc), reason)
            finally:
                await self._announce()

    async def _reconcile(self, target: float, bar: datetime, reason: str) -> str:
        st = self.store
        s = st.settings()
        if self.paper:
            return "PAPER"
        if st.get("killed", False):
            return "kill-switch"
        if not s["enabled"]:
            return "торговля выключена"
        # Инвариант (T1): неудачный setup блокирует ОТКРЫТИЕ/увеличение позиции, но не уменьшение. Позицию, которую надо закрыть
        # или сократить, держать нельзя только потому, что настройки аккаунта (плечо/режим) не подтвердились: reduce-only ордер
        # риск не растит. Открывающая нога (в том числе второй ордер переворота) в gated-режиме не уходит никогда.
        gated = not self.trade_ok and not await self.setup()
        try:
            if not gated:
                n_cancel = await self.cleanup_orders()
                if n_cancel:
                    self.notify("warn", M.w_orders_cancelled(n_cancel))
            pos = await self.ex.position()
            self.detect_stopout(pos, target)
            if st.get("latch", False):
                if abs(target - (st.get("latch_exposure") or 0.0)) <= EPS:
                    return "защёлка: жду смены сигнала"
                st.set("latch", False)
                log.info("целевая экспозиция изменилась — защёлка снята")
            inst, price = await self.ex.instrument(), await self.ex.mark_price()
            cur = pos.signed if pos else 0.0
            lev = float(s["leverage"])
            acted, held = st.get("acted_exposure"), st.get("acted_qty")
            if acted is not None and abs(target - acted) <= EPS:        # цель не менялась: размер удерживаем
                if held is None:
                    held = self._held_qty(target, cur)
                if held is not None and not needs_trade(acted, target, cur, held):
                    if gated:
                        return "аккаунт не настроен"
                    await self._ensure_stop(pos)
                    return "без изменений"
            # пересчёт размера: цель сменилась / плечо / включение / внешняя правка позиции
            flip = cur != 0 and abs(target) > EPS and (cur > 0) != (target > 0)
            balance = None
            if abs(target) <= EPS:
                # полное закрытие: баланс не нужен вовсе — сбой чтения баланса не должен держать позицию против нулевой цели
                tgt, orders = 0.0, plan_orders(cur, 0.0, inst)
            elif flip:
                # переворот: reduce-only закрытие уходит ВСЕГДА (баланс ему не нужен); баланс берём только перед открывающей
                # ногой и уже после закрытия (убыток закрытия уменьшил счёт)
                tgt, orders = None, plan_orders(cur, 0.0, inst)
            else:
                try:
                    balance = await self._sizing_base()
                except ExchangeError as e:
                    self.notify("warn", M.w_balance_unavailable(e))
                    return f"баланс недоступен: {e}"
                tgt = target_qty(target, balance, lev, price, inst)
                if tgt == 0:  # ниже minOrderQty -> считаем целью 0 (закрыть/не открывать)
                    log.info("целевой размер ниже minOrderQty — цель 0")
                orders = plan_orders(cur, tgt, inst)
            skipped = False
            if gated:
                kept = [o for o in orders if o[2]]          # только reduce-only ноги
                skipped = flip or len(kept) != len(orders)  # открывающая нога осталась невыполненной
                orders = kept
                if not orders:
                    return "аккаунт не настроен"
            sent_n = 0
            try:
                for side, qty, ro in orders:
                    await self._send(side, qty, ro, bar, reason, cur, balance)
                    sent_n += 1
                    p = await self.ex.position()
                    cur = p.signed if p else 0.0
                if flip and not gated:
                    # не получилось взять баланс -> ExchangeError: позиции нет, acted_* не трогаем, ниже одна тревога
                    balance, price = await self._sizing_base(), await self.ex.mark_price()
                    tgt = target_qty(target, balance, lev, price, inst)
                    if tgt != 0:
                        await self._send("buy" if tgt > 0 else "sell", abs(tgt), False, bar, reason, cur, balance)
            finally:
                await self.rearm_quietly()  # после любого исхода — позиция должна быть под стопом
            pos = await self.ex.position()
            await self._ensure_stop(pos)
            if gated and skipped:
                # acted_* НЕ пишем: цель не достигнута (открывающая нога не ушла); после починки настроек сверка увидит
                # acted != target и откроет позицию штатно
                return "аккаунт не настроен"
            st.set_acted(target, tgt)                 # парой и одной транзакцией
            pos = await self.ex.position()
            st.set("position", pos.as_dict() if pos else None)
            self._remember_pos(pos)
            return "выполнено"
        except ExchangeError as e:
            self.notify("error", M.w_exchange_error(e))
            return f"ошибка: {e}"

    # ---------- ручные и аварийные пути ----------
    async def close_all(self, reason: str = "закрытие вручную", latch: bool = True, quiet: bool = False) -> str:
        if self.paper:
            return "PAPER: ордера запрещены"
        async with self.lock:
            err: ExchangeError | None = None
            try:
                try:
                    await self._flatten(reason)
                    await self.cleanup_orders()
                except ExchangeError as e:
                    self.notify("error", M.w_close_failed(e))
                    err = e
                if err is None:
                    # состояние фиксируем ДО оповещения (оно может идти ~14 с): сбой в этом окне не должен оставить acted_* без защёлки
                    tgt = self.store.get("target_exposure", 0.0) or 0.0
                    if latch and abs(tgt) > EPS:  # не входим повторно, пока сигнал не изменится
                        self.store.set_acted(None, None, latch=tgt)      # сброс acted_* и защёлка одной транзакцией
                    else:
                        self.store.clear_acted()
            finally:
                await self._announce(quiet)
            return "закрыто" if err is None else f"ошибка: {err}"

    def _close_summary(self, results: list[dict]) -> dict:
        """Итог закрытий для сообщения kill/panic: суммарный P/L, доля счёта на входе, точность."""
        pn = [r["pnl"] for r in results if r.get("pnl") is not None]
        pc = [r["pct"] for r in results if r.get("pct") is not None]
        return {"closed": bool(results), "pnl": sum(pn) if pn else None, "pct": sum(pc) if pc else None,
                "exact": all(r.get("exact", True) for r in results)}

    async def panic(self) -> str:
        if self.paper:
            return "PAPER: ордера запрещены"
        self.store.set_setting("enabled", False)  # сначала выключаем, затем закрываем
        r = await self.close_all("паника", latch=False, quiet=True)
        data = {"kind": "panic", **self._close_summary(self._results), "failed": r.startswith("ошибка"),
                "equity": await self._equity_now({})}
        self._emit("error", data)
        return r

    async def kill(self, reason: str) -> None:
        if self.paper:
            return
        st = self.store
        st.mark_killed(reason)                       # enabled=False + killed + причина одной транзакцией
        failed = False
        async with self.lock:
            try:
                await self._flatten("kill-switch")
                await self.cleanup_orders()
            except ExchangeError as e:
                failed = True
                self.notify("error", M.w_close_failed(e))
            st.clear_acted()
            results = await self._announce(quiet=True)
        data = {"kind": "kill", "reason": reason, **self._close_summary(results), "failed": failed,
                "equity": await self._equity_now({})}
        self._emit("error", data)

    async def retry_kill_close(self) -> str:
        """kill-switch сработал, но позиция осталась (закрытие тогда не удалось). Монитор зовёт это не чаще раза в 5 минут:
        пока бот в состоянии killed, позиция не должна висеть без попыток закрытия. Тихо по ошибкам (повторять тревогу каждый
        раз незачем — дедуплицированное предупреждение шлёт раннер); успешное закрытие объявляется обычным сообщением.
        Возвращает "закрыто" | "позиции нет" | "не закрыто"."""
        if self.paper or not self.store.get("killed", False):
            return "позиции нет"
        async with self.lock:
            res = "не закрыто"
            try:
                pos = await self.ex.position()
                if pos is None:
                    self.store.set("stop_armed", False)
                    return "позиции нет"
                await self._send("sell" if pos.side == "long" else "buy", pos.qty, True, datetime.now(timezone.utc),
                                 "kill-switch (повтор)", pos.signed)
                pos = await self.ex.position()
                if pos is None:
                    self.store.set("stop_armed", False)
                    self.store.set("position", None)
                    self.store.clear_acted()
                    try:
                        await self.cleanup_orders()
                    except ExchangeError as e:
                        log.warning("kill-switch: отмена ордеров после повторного закрытия: %s", e)
                    res = "закрыто"
            except ExchangeError as e:
                log.warning("kill-switch: повторное закрытие не удалось: %s", e)
            finally:
                await self._announce()
            if res != "закрыто":
                await self.rearm_quietly()           # пока позиция жива — она под биржевым стопом
            return res
