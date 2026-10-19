"""Раннер: пересчёт цели на каждом закрытии часовой свечи (+10 с), сверка с биржей, мониторинг риска."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from tradebot.telegram import messages as M
from tradebot.trading import risk
from tradebot.trading.executor import Executor, Notify
from tradebot.trading.market_feed import TF_DELTA, FeedError, MarketFeed, compute_target
from tradebot.trading.state import Store

log = logging.getLogger("runner")
WAKE_DELAY = pd.Timedelta(seconds=10)
MONITOR_EVERY = 60
EQUITY_EVERY = 300


def _tz(name: str):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


SUMMARY_HOUR = 9  # тихий итог дня в 09:00 по местному времени
MANUAL_RECALC = "внеплановый пересчёт"


class Runner:
    def __init__(self, store: Store, feed: MarketFeed, executor: Executor, notify: Notify, tz_name: str = "Europe/Moscow"):
        self.store, self.feed, self.ex, self.notify = store, feed, executor, notify
        self.tz = _tz(tz_name)
        self.wake = asyncio.Event()  # внеплановый пересчёт (включение, смена настроек)
        self.stop = asyncio.Event()
        self._cycle_lock = asyncio.Lock()
        self._last_eq_log = 0.0
        self._fails = 0

    def trigger(self) -> None:
        self.wake.set()

    def _warn_weights(self) -> None:
        """Эффективные веса ограничены (сумма была бы > 100%) — предупредить один раз на каждое новое состояние."""
        notes = self.store.weight_notes()
        sig = "|".join(notes)
        if sig != (self.store.get("weights_warned") or ""):
            self.store.set("weights_warned", sig)
            unknown = [n for n in notes if "нет в этой версии" in n]
            weights = [n for n in notes if n not in unknown]
            if weights:
                self.notify("warn", M.w_weights(weights))
            if unknown:
                self.notify("warn", "; ".join(unknown))

    # ---------- цикл по бару ----------
    async def cycle(self, why: str = "бар") -> None:
        async with self._cycle_lock:
            st = self.store
            legs = st.effective_legs()
            self._warn_weights()
            try:
                fd = await self.feed.fetch(legs)
            except FeedError as e:
                self.notify("error", M.w_feed_down(e))
                return
            prev = {k: v[0] for k, v in st.latest_signals().items()}
            if not fd.oi_ok:
                self.notify("warn", M.w_oi_late())
            for tf, why_bad in fd.stale.items():
                self.notify("error", M.w_tf_stale(tf, why_bad))
            target, sigs = compute_target(legs, fd.dfs, skip_oi=not fd.oi_ok, prev=prev, stale_tfs=set(fd.stale))
            bar_close = fd.bar + TF_DELTA["1h"]
            if target is None:
                self.notify("error", M.w_no_signal(sigs))
                return
            st.log_signals(fd.bar.isoformat(), sigs, target)
            # смена сигналов: уходит в причину сделки; отдельным сообщением — только если ордера не последовало
            changes = [{"label": k, "prev": prev[k], "new": v} for k, v in sigs.items()
                       if prev.get(k) is not None and abs(prev[k] - v) > 1e-9]
            st.set("target_exposure", target)
            st.set("last_bar", fd.bar.isoformat())
            late = await self.feed.now() - bar_close
            # ручной пересчёт (смена настроек/включение) всегда идёт по уже закрытой свече — «опоздание» там не новость
            if late >= pd.Timedelta(minutes=2) and why != MANUAL_RECALC:
                log.info("действую с опозданием на %d мин (после перезапуска/паузы)", int(late.total_seconds() // 60))
            reason = M.stored_reason(why, changes)
            if self.ex.paper:
                try:
                    plan = await self.ex.paper_plan(target)
                    log.info("бар %s цель %+.3f без ордеров: %s %s", fd.bar, target, plan["side"], plan["qty"])
                except Exception as e:
                    log.warning("paper_plan: %s", e)
                if changes:
                    M.emit(self.notify, "signal", {"kind": "signal", "changes": changes, "target": target,
                                                   "note": M.NO_ORDER_NOTES["торговля выключена"]})
                return
            sent0 = getattr(self.ex, "sent", 0)
            res = await self.ex.reconcile(target, fd.bar.to_pydatetime(), reason)
            log.info("бар %s цель %+.3f -> %s", fd.bar, target, res)
            if res.startswith("ошибка") or res in ("аккаунт не настроен", "защёлка: жду смены сигнала"):
                log.info("сверка: %s", res)        # причина уже отправлена владельцу самим исполнителем (или это штатное ожидание)
            if changes and getattr(self.ex, "sent", 0) == sent0:
                M.emit(self.notify, "signal", {"kind": "signal", "changes": changes, "target": target,
                                               "note": M.NO_ORDER_NOTES.get(res)})

    async def _backfill(self) -> None:
        """Фоновый добор точных итогов прошлых сделок с биржи (один раз при старте; старт бота не задерживает)."""
        try:
            await self.ex.backfill_pnl()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.info("добор итогов сделок: %s", type(e).__name__)

    async def run(self) -> None:
        bf = asyncio.create_task(self._backfill(), name="backfill") if hasattr(self.ex, "backfill_pnl") else None
        try:
            await self._run()
        finally:
            if bf is not None and not bf.done():
                bf.cancel()

    async def _run(self) -> None:
        await self._safe_cycle("старт/перезапуск")
        while not self.stop.is_set():
            now = await self.feed.now()
            nxt = (now - WAKE_DELAY).floor("1h") + TF_DELTA["1h"] + WAKE_DELAY
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=max(1.0, (nxt - now).total_seconds()))
                self.wake.clear()
                await asyncio.sleep(1)  # схлопнуть пачку нажатий
                self.wake.clear()
                await self._safe_cycle(MANUAL_RECALC)
            except asyncio.TimeoutError:
                await self._safe_cycle("бар")

    async def _safe_cycle(self, why: str) -> None:
        try:
            await self.cycle(why)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("cycle failed")
            self.notify("error", M.w_cycle_failed(e))

    # ---------- мониторинг ----------
    async def monitor(self) -> None:
        while not self.stop.is_set():
            try:
                await self.tick()
                self._fails = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._fails += 1
                log.warning("monitor: %s", e)
                if self._fails == 5:
                    self.notify("error", M.w_monitor_failed(5, e))
            await asyncio.sleep(MONITOR_EVERY)

    async def tick(self) -> None:
        st = self.store
        st.set("heartbeat", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        eq = await self.ex.ex.equity()
        st.set("equity", eq)
        if st.get("baseline_equity") is None:
            st.set("baseline_equity", eq)      # только для строки «P/L всего» в Telegram; на риск не влияет
        peak = risk.update_peak(st.get("peak_equity"), eq)
        st.set("peak_equity", peak)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if st.get("day") != today:
            st.set("day", today)
            st.set("day_start_equity", eq)
        local_now = datetime.now(self.tz)
        ldate = local_now.strftime("%Y-%m-%d")
        if local_now.hour >= SUMMARY_HOUR and st.get("summary_day") != ldate:
            if st.get("summary_day") is not None:  # при первом запуске молча запоминаем день
                M.emit(self.notify, "daily", self.daily_data(eq))
            st.set("summary_day", ldate)
        if asyncio.get_running_loop().time() - self._last_eq_log >= EQUITY_EVERY:
            st.add_equity(eq)
            self._last_eq_log = asyncio.get_running_loop().time()
        s = st.settings()
        # kill-switch сразу после чтения эквити, независимо от успеха чтения позиции
        if not self.ex.paper and not st.get("killed", False) and (s["enabled"] or st.get("position")):
            reason = risk.check_kill(eq, peak, st.get("day_start_equity"), s["kill_dd"], s["kill_daily"])
            if reason:
                await self.ex.kill(reason)
                return
        pos = await self.ex.refresh_cache()
        if s["enabled"]:
            await self.ex.check_stopout()
        if not self.ex.paper and pos is not None and pos.stop is None and not st.get("killed", False) and not self.ex.lock.locked():
            async with self.ex.lock:
                await self.ex.rearm_quietly()  # позиция без биржевого стопа

    def daily_data(self, eq: float) -> dict:
        """Данные итога суток (окно — последние 24 часа): изменение счёта в $ и %, закрытые за окно сделки с суммарным
        реализованным P/L, текущая позиция с нереализованным результатом, отступ от пика."""
        st = self.store
        now = datetime.now(timezone.utc)
        since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
        base = st.equity_before(since)                     # счёт 24 часа назад (ближайшая предыдущая точка кривой)
        if base is None:
            base = st.get("day_start_equity") or eq        # кривой ещё нет — начало суток по UTC
        trips = [t for t in st.closed_trips() if (t["ts_close"] or "") >= since and t.get("pnl") is not None]
        fees = [t["fees"] for t in trips if t.get("fees") is not None]
        peak = st.get("peak_equity")
        pos = st.get("position")
        return {"kind": "daily", "equity": eq, "base": base, "trips_n": len(trips), "trips_pnl": sum(t["pnl"] for t in trips),
                "trips_fees": sum(fees) if fees else None,
                "pos": {"side": pos["side"], "qty": pos["qty"], "upnl": pos.get("upnl") or 0.0} if pos else None,
                "dd": max(0.0, (peak - eq) / peak) if peak and peak > 0 else None}

    def daily_summary(self, eq: float) -> str:
        return M.plain(self.daily_data(eq))
