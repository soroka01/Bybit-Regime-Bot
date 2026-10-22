"""Раннер: пересчёт цели на каждом закрытии часовой свечи (+10 с), сверка с биржей, мониторинг риска."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from tradebot.i18n import tr
from tradebot.telegram import messages as M
from tradebot.trading import risk
from tradebot.trading.executor import Executor, Notify
from tradebot.trading.market_feed import TF_DELTA, FeedError, MarketFeed, compute_target, expected_last_bar
from tradebot.trading.state import BACKUP_DIR, Store

log = logging.getLogger("runner")
WAKE_DELAY = pd.Timedelta(seconds=10)
MONITOR_EVERY = 60
EQUITY_EVERY = 300
WAIT_CHUNK = 60            # ожидание следующего бара — отрезками не длиннее: время сверяется с часами заново (сон ПК, сдвиг часов)
CATCHUP_DELAY = 45         # догоняющий пересчёт — не раньше, чем через столько секунд после конца последнего цикла
MONITOR_REALERT_S = 1800   # монитор падает подряд: повторное предупреждение не чаще раза в 30 минут
KILL_RETRY_S = 300         # повтор закрытия при killed + открытая позиция — не чаще раза в 5 минут
MAINT_RETRY_S = 3600       # ежедневная копия БД не удалась — следующая попытка не раньше, чем через час


def _tz(name: str):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


SUMMARY_HOUR = 9  # тихий итог дня в 09:00 по местному времени
MANUAL_RECALC = "внеплановый пересчёт"


class Runner:
    def __init__(self, store: Store, feed: MarketFeed, executor: Executor, notify: Notify, tz_name: str = "Europe/Moscow",
                 backup_dir: Path | str | None = None):
        self.store, self.feed, self.ex, self.notify = store, feed, executor, notify
        self.tz = _tz(tz_name)
        self.wake = asyncio.Event()  # внеплановый пересчёт (включение, смена настроек)
        self.stop = asyncio.Event()
        self._cycle_lock = asyncio.Lock()
        self._last_eq_log = 0.0
        self._fails = 0
        self.backup_dir = Path(backup_dir) if backup_dir is not None else BACKUP_DIR
        self._alerts: dict[str, float] = {}          # дедупликация простых предупреждений: ключ -> когда послано (monotonic)
        self._kill_retry_at: float | None = None
        self._catchup_for: pd.Timestamp | None = None    # ожидаемый бар, для которого догоняющий цикл уже запускался
        self._last_cycle_end = 0.0
        self._maint_task: asyncio.Task | None = None
        self._maint_next = 0.0

    def trigger(self) -> None:
        self.wake.set()

    def _alert(self, key: str, text: str, level: str = "warn", every: float | None = None) -> bool:
        """Простое (plain) предупреждение с дедупликацией: пока ключ не сброшен (_alert_clear) — один раз; с every — не чаще
        раза в every секунд. Нужно для условий, которые держатся минутами (монитор проверяет раз в минуту): структурное
        событие каждую минуту завалило бы Telegram."""
        now = time.monotonic()
        last = self._alerts.get(key)
        if last is not None and (every is None or now - last < every):
            return False
        self._alerts[key] = now
        self.notify(level, text)
        return True

    def _alert_clear(self, key: str) -> None:
        self._alerts.pop(key, None)

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
                log.info(tr("acting %d min late (after a restart/pause)"), int(late.total_seconds() // 60))
            reason = M.stored_reason(why, changes)
            sent0 = getattr(self.ex, "sent", 0)
            res = await self.ex.reconcile(target, fd.bar.to_pydatetime(), reason)
            log.info(tr("bar %s target %+.3f -> %s"), fd.bar, target, res)
            if res.startswith("ошибка") or res in ("аккаунт не настроен", "защёлка: жду смены сигнала"):
                log.info(tr("reconcile: %s"), res)        # причина уже отправлена владельцу самим исполнителем (или это штатное ожидание)
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
            log.info(tr("trade results backfill: %s"), type(e).__name__)

    async def run(self) -> None:
        bf = asyncio.create_task(self._backfill(), name="backfill") if hasattr(self.ex, "backfill_pnl") else None
        try:
            await self._run()
        finally:
            if bf is not None and not bf.done():
                bf.cancel()

    def catchup_due(self, now: pd.Timestamp) -> bool:
        """Последний обработанный бар (kv last_bar) отстаёт от ожидаемого, а штатный цикл за ним уже не придёт: он ушёл (ручной
        пересчёт затянулся за границу часа, сбой ленты в момент бара, сон ПК). Раз на ожидаемый бар и не сразу после цикла."""
        exp = expected_last_bar(now, "1h")
        if self._catchup_for is not None and self._catchup_for >= exp:
            return False                                              # на этот бар догоняющий цикл уже был (ровно один)
        if now < exp + TF_DELTA["1h"] + WAKE_DELAY:
            return False                                              # данные бара ещё не должны были появиться — ждём штатный цикл
        if time.monotonic() - self._last_cycle_end < CATCHUP_DELAY:
            return False                                              # только что был цикл: не долбим ленту подряд
        raw = self.store.get("last_bar")
        try:
            last = pd.Timestamp(raw) if raw else None
        except (ValueError, TypeError):
            last = None
        if last is not None and last.tzinfo is None:
            last = last.tz_localize("UTC")
        return last is None or last < exp

    async def _run(self) -> None:
        await self._safe_cycle("старт/перезапуск")
        while not self.stop.is_set():
            now = await self.feed.now()
            nxt = (now - WAKE_DELAY).floor("1h") + TF_DELTA["1h"] + WAKE_DELAY
            if self.catchup_due(now):
                # Инвариант: ровно ОДИН цикл на последнем баре; пропущенные бары не переигрываем (цикл всегда берёт свежую ленту
                # и действует по ней — торговать «задним числом» по старым барам нельзя).
                self._catchup_for = expected_last_bar(now, "1h")
                log.info(tr("the last bar is behind the expected one (%s) — one catch-up recalculation"), self._catchup_for)
                await self._safe_cycle("догоняющий пересчёт")
                continue
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=min(float(WAIT_CHUNK), max(1.0, (nxt - now).total_seconds())))
            except asyncio.TimeoutError:
                if await self.feed.now() >= nxt:            # дошли до бара (а не просто отрезок кончился)
                    await self._safe_cycle("бар")
                continue
            self.wake.clear()
            await asyncio.sleep(1)  # схлопнуть пачку нажатий
            self.wake.clear()
            await self._safe_cycle(MANUAL_RECALC)

    async def _safe_cycle(self, why: str) -> None:
        try:
            await self.cycle(why)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("cycle failed")
            self.notify("error", M.w_cycle_failed(e))
        finally:
            self._last_cycle_end = time.monotonic()

    # ---------- мониторинг ----------
    async def monitor(self) -> None:
        while not self.stop.is_set():
            try:
                await self.tick()
                self._fails = 0
                self._alert_clear("monitor_failed")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._fails += 1
                log.warning("monitor: %s", e)
                if self._fails == 5:
                    self.notify("error", M.w_monitor_failed(5, e))
                    self._alerts["monitor_failed"] = time.monotonic()
                elif self._fails > 5:
                    # сбои идут и дальше: напоминаем раз в 30 минут простым предупреждением (иначе долгая недоступность
                    # биржи/ошибка в тике после 5-го сбоя оставалась бы молчаливой)
                    self._alert("monitor_failed", f"Мониторинг не работает уже {self._fails} проверок подряд "
                                f"(≈{self._fails * MONITOR_EVERY // 60} мин): {str(e) or type(e).__name__}", every=MONITOR_REALERT_S)
            self._maybe_maintain()
            await asyncio.sleep(MONITOR_EVERY)

    # ---------- ежедневное обслуживание БД ----------
    def _maybe_maintain(self) -> None:
        """Раз в сутки: копия bot.db -> чистка журналов -> усечение WAL (Store.maintenance), в отдельном потоке — торговлю и
        мониторинг не задерживает. Первый такт монитора после старта фактически и есть «запуск при старте, если копии за
        сегодня нет». Сбой копии не ломает бота: предупреждение и повтор не раньше, чем через час."""
        if self._maint_task is not None and not self._maint_task.done():
            return
        if time.monotonic() < self._maint_next or not self.store.backup_due(self.backup_dir):
            return
        self._maint_task = asyncio.create_task(self._maintain(), name="db-maintenance")

    async def _maintain(self) -> None:
        try:
            res = await asyncio.to_thread(self.store.maintenance, self.backup_dir)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            res = {"error": f"{type(e).__name__}: {e}"}
        if res.get("error"):
            self._maint_next = time.monotonic() + MAINT_RETRY_S
            log.warning(tr("DB maintenance: %s"), res["error"])
            self._alert("db_backup", f"Резервная копия базы бота не сделана ({res['error']}) — повторю через час.", every=86400)
        else:
            self._alert_clear("db_backup")
            if res.get("ran"):
                log.info(tr("DB maintenance: backup %s, purged %s"), res.get("backup"), res.get("purged"))

    async def tick(self) -> None:
        st = self.store
        # heartbeat — «процесс жив» (пишется ДО чтения биржи, нарочно не двигать); monitor_ok_ts — «биржа отвечает» (ниже,
        # только после успешного чтения). Сторожевой скрипт различает по ним «процесс молчит» и «биржа не отвечает».
        st.set("heartbeat", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        eq = await self.ex.ex.equity()
        st.set("monitor_ok_ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        s = st.settings()
        peak = None
        # Инвариант: баланс не прочитан или равен нулю/NaN (`not (eq > 0)` ловит и NaN) — это НЕ данные: ни кривая, ни пик, ни
        # начало суток из такого значения не строятся, kill-switch в этом такте пропускается (с дедуплицированным предупреждением).
        eq_ok = eq > 0
        if eq_ok:
            self._alert_clear("equity_bad")
            peak = self._record_equity(eq)
        else:
            self._alert("equity_bad", "баланс счёта не прочитан/равен нулю: проверка kill-switch пропущена")
        # kill-switch сразу после чтения эквити, независимо от успеха чтения позиции
        if eq_ok and not st.get("killed", False) and (s["enabled"] or st.get("position")):
            reason = risk.check_kill(eq, peak, st.get("day_start_equity"), s["kill_dd"], s["kill_daily"])
            if reason:
                await self.ex.kill(reason)
                return
        pos = await self.ex.refresh_cache()
        # Инвариант: обнаружение стоп-аута и перевзвод стопа НЕ зависят от enabled — при выключенной торговле позиция остаётся
        # на бирже, и её закрытие биржей надо заметить и объявить.
        await self.ex.check_stopout()
        killed = st.get("killed", False)
        if pos is not None and pos.stop is None and not killed and not self.ex.lock.locked():
            async with self.ex.lock:
                await self.ex.rearm_quietly()  # позиция без биржевого стопа
        if killed and pos is not None:
            await self._retry_kill_close()

    async def _retry_kill_close(self) -> None:
        """killed, а позиция на бирже осталась (закрытие в kill не удалось): повторяем закрытие не чаще раза в 5 минут,
        предупреждение — простое и дедуплицированное (не структурное событие каждую минуту)."""
        now = time.monotonic()
        if self._kill_retry_at is not None and now - self._kill_retry_at < KILL_RETRY_S:
            return
        self._kill_retry_at = now
        self._alert("kill_retry", "kill-switch: позиция всё ещё открыта — повторяю закрытие (раз в 5 минут), проверьте биржу.",
                    every=MONITOR_REALERT_S)
        if await self.ex.retry_kill_close() != "не закрыто":
            self._kill_retry_at = None
            self._alert_clear("kill_retry")

    def _record_equity(self, eq: float) -> float:
        """Бухгалтерия валидного эквити: значение, базовая точка, пик, начало суток, итог дня, кривая. Возвращает пик."""
        st = self.store
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
        return peak

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
