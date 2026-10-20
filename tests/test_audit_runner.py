"""Аудит торгового ядра: раннер (T2 kill-check при нулевом эквити, T6 monitor_ok_ts и повторные тревоги монитора,
T7 стоп-аут/перевзвод без enabled и повтор закрытия при kill, T8 догоняющий цикл и ожидание отрезками, T10 копия БД)."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from helpers import run, Sink
from tradebot.trading import runner as runner_mod
from tradebot.trading.exchange import ExchangeError
from fakes.exchange import DryRunExchange
from tradebot.trading.executor import Executor
from tradebot.trading.runner import MANUAL_RECALC, Runner
from tradebot.trading.state import Store

BAR = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def mk(tmp_path, equity=1100.0):
    st = Store(tmp_path / "r.db")
    st.set_setting("enabled", True)
    st.set_setting("leverage", 2)
    ex = DryRunExchange(equity=equity, mark=100_000.0)
    sink = Sink()
    exe = Executor(ex, st, sink)
    exe.poll_delay = 0
    exe.retry_wait = 0
    run(exe.setup())
    r = Runner(st, NS(), exe, sink, backup_dir=tmp_path / "bk")
    return st, ex, exe, sink, r


# ---------- T2: эквити не прочитано / 0 / NaN ----------
@pytest.mark.parametrize("bad", [0.0, float("nan"), -5.0])
def test_tick_skips_kill_check_on_unreadable_equity_and_alerts_once(tmp_path, bad):
    st, ex, exe, sink, r = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    st.set("peak_equity", 1100.0)
    st.set("day_start_equity", 1100.0)
    st.set("baseline_equity", 1100.0)
    st.set("day", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    st.set("position", {"x": 1})

    async def eq():
        return bad
    ex.equity = eq
    run(r.tick())
    run(r.tick())
    assert not st.get("killed") and ex.qty == pytest.approx(0.010)                   # kill не сработал (0 -> «просадка 100%»)
    assert sink.count("проверка kill-switch пропущена", "warn") == 1                   # предупреждение одно (дедупликация)
    assert st.get("peak_equity") == 1100.0 and st.get("day_start_equity") == 1100.0    # мусор не попал в пик/начало суток
    assert st.get("equity") is None                                                    # и в кэш эквити мусор не пишется


def test_tick_equity_recovers_and_alert_rearms(tmp_path):
    st, ex, exe, sink, r = mk(tmp_path)
    orig = ex.equity
    state = {"bad": True}

    async def eq():
        return 0.0 if state["bad"] else await orig()
    ex.equity = eq
    run(r.tick())
    assert sink.count("kill-switch пропущена") == 1
    state["bad"] = False
    run(r.tick())
    assert st.get("equity") == 1100.0
    state["bad"] = True
    run(r.tick())
    assert sink.count("kill-switch пропущена") == 2                                    # после восстановления тревога снова возможна


# ---------- T6: monitor_ok_ts ----------
def test_monitor_ok_ts_written_only_after_exchange_read(tmp_path):
    st, ex, exe, sink, r = mk(tmp_path)

    async def boom():
        raise ExchangeError("сеть")
    ex.equity = boom
    with pytest.raises(ExchangeError):
        run(r.tick())
    assert st.get("heartbeat") is not None and st.get("monitor_ok_ts") is None        # процесс жив, биржа молчит
    del ex.equity
    run(r.tick())
    assert st.get("monitor_ok_ts") is not None


def _failing(r, n):
    calls = {"n": 0}

    async def tick():
        calls["n"] += 1
        if calls["n"] >= n:
            r.stop.set()
        raise RuntimeError("биржа недоступна")
    r.tick = tick


def test_monitor_realerts_every_30_minutes(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "MONITOR_EVERY", 0)
    st, ex, exe, sink, r = mk(tmp_path)
    _failing(r, 5)
    run(r.monitor())
    assert sink.count("5 сбоев подряд", "error") == 1                                   # как раньше: на 5-м сбое
    r.stop.clear()
    _failing(r, 4)
    run(r.monitor())
    assert sink.count("Мониторинг не работает") == 0                                    # 6-8 сбои: в пределах 30 минут — тишина
    r._alerts["monitor_failed"] -= 1801                                                 # прошло 30 минут
    r.stop.clear()
    _failing(r, 1)
    run(r.monitor())
    assert sink.count("Мониторинг не работает", "warn") == 1                            # простое предупреждение, не событие
    r.stop.clear()
    _failing(r, 3)
    run(r.monitor())
    assert sink.count("Мониторинг не работает") == 1                                    # и снова дедупликация


def test_monitor_recovery_resets_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "MONITOR_EVERY", 0)
    st, ex, exe, sink, r = mk(tmp_path)
    _failing(r, 5)
    run(r.monitor())
    assert r._fails == 5

    async def ok():
        r.stop.set()
    r.tick = ok
    r.stop.clear()
    run(r.monitor())
    assert r._fails == 0 and "monitor_failed" not in r._alerts


# ---------- T7: стоп-аут и перевзвод не зависят от enabled ----------
def test_stopout_detected_while_trading_disabled(tmp_path):
    st, ex, exe, sink, r = mk(tmp_path)
    st.set("target_exposure", 0.5)
    run(exe.reconcile(0.5, BAR))
    st.set_setting("enabled", False)
    ex.stop_out()
    run(r.tick())
    assert not st.get("stop_armed")
    rts = st.roundtrip_rows()
    assert len(rts) == 1 and rts[0]["reason"] == "закрыто биржей"                       # стоп-аут объявлен и записан
    assert sink.count("", "error") >= 1


def test_rearm_when_position_has_no_stop_even_if_disabled(tmp_path):
    st, ex, exe, sink, r = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    st.set_setting("enabled", False)
    ex.stop = None
    run(r.tick())
    assert ex.stop is not None


def test_no_rearm_without_position(tmp_path):
    st, ex, exe, sink, r = mk(tmp_path)
    called = []
    orig = exe.rearm_quietly

    async def spy():
        called.append(1)
        await orig()
    exe.rearm_quietly = spy
    run(r.tick())
    assert called == []


# ---------- T7: повтор закрытия при killed ----------
def _killed_with_position(tmp_path):
    st, ex, exe, sink, r = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    good = ex.market

    async def broken(*a, **k):
        raise ExchangeError("отказ", 10001)
    ex.market = broken
    run(exe.kill("тест"))
    assert st.get("killed") and ex.qty > 0
    attempts = []
    orig = exe.retry_kill_close

    async def spy():
        res = await orig()
        attempts.append(res)
        return res
    exe.retry_kill_close = spy
    return st, ex, exe, sink, r, good, attempts


def test_kill_retry_rate_limited_and_quiet(tmp_path):
    st, ex, exe, sink, r, good, attempts = _killed_with_position(tmp_path)
    n0 = len(sink.msgs)
    run(r.tick())
    assert attempts == ["не закрыто"]
    run(r.tick())
    run(r.tick())
    assert attempts == ["не закрыто"]                                                   # не чаще раза в 5 минут
    r._kill_retry_at -= 301
    run(r.tick())
    assert attempts == ["не закрыто", "не закрыто"]
    assert sink.count("повторяю закрытие", "warn") == 1                                 # простое предупреждение, один раз
    assert sink.count("", "error") == sum(1 for lv, _ in sink.msgs[:n0] if lv == "error")      # новых «error» не добавилось


def test_kill_retry_closes_when_exchange_recovers(tmp_path):
    st, ex, exe, sink, r, good, attempts = _killed_with_position(tmp_path)
    run(r.tick())
    ex.market = good
    r._kill_retry_at -= 301
    run(r.tick())
    assert attempts[-1] == "закрыто" and ex.qty == 0
    run(r.tick())
    assert attempts[-1] == "закрыто" and len(attempts) == 2                              # позиции нет — больше не пытаемся


# ---------- T8: догоняющий цикл ----------
def _runner_with_clock(tmp_path, start):
    st, ex, exe, sink, r = mk(tmp_path)
    clock = [pd.Timestamp(start)]

    async def now():
        return clock[0]
    r.feed = NS(now=now)
    return st, r, clock


def test_catchup_due_rules(tmp_path):
    st, r, clock = _runner_with_clock(tmp_path, "2026-10-20T12:00:30Z")
    r._last_cycle_end = 0.0
    now = clock[0]
    st.set("last_bar", "2026-10-20T10:00:00+00:00")
    assert r.catchup_due(now) is True                                                   # ожидался бар 11:00, обработан 10:00
    st.set("last_bar", "2026-10-20T11:00:00+00:00")
    assert r.catchup_due(now) is False                                                  # актуален
    st.set("last_bar", None)
    assert r.catchup_due(now) is True                                                   # циклов ещё не было
    st.set("last_bar", "2026-10-20T10:00:00+00:00")
    assert r.catchup_due(pd.Timestamp("2026-10-20T12:00:05Z")) is False                 # данные бара ещё не должны были появиться
    import time
    r._last_cycle_end = time.monotonic()
    assert r.catchup_due(now) is False                                                  # цикл только что был
    r._last_cycle_end = 0.0
    r._catchup_for = pd.Timestamp("2026-10-20T11:00:00Z")
    assert r.catchup_due(now) is False                                                  # на этот бар догоняющий уже был (ровно один)
    assert r.catchup_due(pd.Timestamp("2026-10-20T13:00:30Z")) is True                  # а на следующий — снова можно


def test_manual_recalc_across_hour_boundary_runs_exactly_one_catchup(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "WAIT_CHUNK", 0.02)
    monkeypatch.setattr(runner_mod, "CATCHUP_DELAY", 0)
    st, r, clock = _runner_with_clock(tmp_path, "2026-10-20T11:59:55Z")
    whys = []

    async def cycle(why):
        whys.append(why)
        if why == MANUAL_RECALC:
            clock[0] = pd.Timestamp("2026-10-20T12:00:20Z")           # ручной пересчёт затянулся за границу часа (+10 с)
            st.set("last_bar", "2026-10-20T10:00:00+00:00")
        elif why == "догоняющий пересчёт":
            st.set("last_bar", "2026-10-20T11:00:00+00:00")
            r.stop.set()
        else:
            st.set("last_bar", "2026-10-20T10:00:00+00:00")
    r.cycle = cycle
    r.trigger()
    run(asyncio.wait_for(r._run(), 15))
    assert whys == ["старт/перезапуск", MANUAL_RECALC, "догоняющий пересчёт"]


def test_catchup_is_single_even_if_cycle_fails_to_advance(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "WAIT_CHUNK", 0.02)
    monkeypatch.setattr(runner_mod, "CATCHUP_DELAY", 0)
    st, r, clock = _runner_with_clock(tmp_path, "2026-10-20T12:30:00Z")
    st.set("last_bar", "2026-10-20T09:00:00+00:00")                  # бары 10:00 и 11:00 пропущены
    whys = []

    async def cycle(why):
        whys.append(why)                                              # лента не отвечает: last_bar не двигается
    r.cycle = cycle

    async def stopper():
        await asyncio.sleep(0.3)
        r.stop.set()
    async def go():
        await asyncio.gather(r._run(), stopper())
    run(go())
    assert whys == ["старт/перезапуск", "догоняющий пересчёт"]       # один догоняющий, без переигрывания каждого пропущенного бара


def test_wait_is_chunked_and_clock_jump_triggers_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "WAIT_CHUNK", 0.02)
    st, r, clock = _runner_with_clock(tmp_path, "2026-10-20T11:30:00Z")
    st.set("last_bar", "2026-10-20T10:00:00+00:00")
    timeouts = []
    real = asyncio.wait_for

    async def spy(aw, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 3:
            clock[0] = pd.Timestamp("2026-10-20T12:00:11Z")           # ПК «проспал» границу часа
        return await real(aw, timeout)
    monkeypatch.setattr(runner_mod.asyncio, "wait_for", spy)
    whys = []

    async def cycle(why):
        whys.append(why)
        if why == "бар":
            st.set("last_bar", "2026-10-20T11:00:00+00:00")
            r.stop.set()
    r.cycle = cycle
    run(real(r._run(), 15))
    assert whys == ["старт/перезапуск", "бар"]
    assert len(timeouts) >= 3 and max(timeouts) <= 0.02              # до границы 30 минут, но ждём отрезками, а не одним таймаутом


# ---------- T10: копия БД из монитора ----------
def test_monitor_runs_backup_in_thread_once_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "MONITOR_EVERY", 0)
    st, ex, exe, sink, r = mk(tmp_path)
    st.add_event("info", "x")
    n = {"t": 0}

    async def tick():
        n["t"] += 1
        if n["t"] >= 2:
            r.stop.set()
    r.tick = tick

    async def go():
        await r.monitor()
        if r._maint_task is not None:
            await r._maint_task
    run(go())
    files = list((tmp_path / "bk").glob("bot_*.db"))
    assert len(files) == 1 and not st.backup_due(tmp_path / "bk")
    r._maybe_maintain()
    assert r._maint_task.done()                                         # копия за сегодня есть — новая задача не создаётся


def test_failed_backup_alerts_and_retries_later(tmp_path, monkeypatch):
    st, ex, exe, sink, r = mk(tmp_path)

    def bad(*a, **k):
        raise OSError("нет места")
    monkeypatch.setattr(st, "backup", bad)

    async def go():
        r._maybe_maintain()
        await r._maint_task
        r._maybe_maintain()                                             # сразу повторять не должен (час)
    run(go())
    assert sink.count("Резервная копия базы бота не сделана", "warn") == 1 and r._maint_next > 0
    assert not list((tmp_path / "bk").glob("bot_*.db")) and not list((tmp_path / "bk").glob("*.tmp"))
