"""Динамические ноги бота: хранилище (добавить/заменить/удалить), проверка окна, сценарии Telegram (текст с сайта -> карточка -> вес ->
подтверждение; удаление), очередь предложений «В бота» с сайта."""
import asyncio
import sqlite3
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tradebot.core.legspec import draft_payload, parse_leg_text, validate_leg
from tradebot.core.portfolio import DEFAULT_LEGS, Leg
from tradebot.core.strategies import STRATEGIES, Strategy
from tradebot.telegram import keyboards as kb
from tradebot.telegram.handlers import Controller, build_router
from tradebot.telegram.panel import Panel
from tradebot.telegram.proposals import ProposalWatcher
from tradebot.trading import legcheck
from tradebot.trading import state as state_mod
from fakes.exchange import DryRunExchange
from tradebot.trading.executor import Executor
from tradebot.trading.legcheck import CheckError, LegCheck, check_leg
from tradebot.trading.market_feed import window_for
from tradebot.trading.state import MAX_LEGS, MAX_OPEN_PROPOSALS, ProposalError, Store, legs_catalog, read_snapshot, submit_proposals
from _synth import bars

OWNER = 42
EMA = 'Leg("EMA cross", "1h", {"fast": 12, "slow": 26}, weight=0.5, long_only=False)'
LAB = ('Стратегия: EMA cross | Таймфрейм: 1h | Параметры: fast=12, slow=26 | Символ: BTCUSDT | Рынок: Перп | Только лонг: нет | '
       'Плечо: 1x | Период: 365 дн. | Результат: доход +10%, макс. просадка 5%, сделок 40 | Оверлеи: нет\n' + EMA)
FRESH = (pd.Timestamp.now(tz="UTC").floor("1h") - pd.Timedelta(hours=1)).isoformat()          # последняя закрытая 1h свеча
OK_CHECK = LegCheck(True, 480, 120, 0, 1.0, False, "окно 480 свечей воспроизводит бэктест (0 расхождений; точек: 120)", FRESH)


def run(c):
    return asyncio.run(c)


def keys(st):
    return [l.key for l, _, _ in st.leg_cfg()]


def weights(st):
    return {l.key: round(w, 2) for l, w, _ in st.leg_cfg()}


def ema(tf="1h", **p):
    return Leg("EMA cross", tf, p or {"fast": 12, "slow": 26}, 0.0)


# ---------- хранилище ----------
@pytest.fixture
def st(tmp_path):
    s = Store(tmp_path / "b.db")
    yield s
    s.close()


def test_untouched_db_uses_default_legs(st):
    assert keys(st) == [l.key for l in DEFAULT_LEGS] and weights(st) == {"SMA cross@4h": 0.5, "OI flush@1h": 0.0, "Regime switch (CHOP)@6h": 0.5}
    assert st.settings()["legs_catalog"] is None


def test_first_mutation_freezes_catalog_and_keeps_user_overrides(st):
    st.set("s_legs", {"SMA cross@4h": {"weight": 0.0, "enabled": True}, "Regime switch (CHOP)@6h": {"weight": 1.0, "enabled": True},
                      "OI flush@1h": {"weight": 0.0, "enabled": True}})          # боевая конфигурация пользователя
    st.add_leg(ema(), 0.0)
    assert keys(st) == ["SMA cross@4h", "OI flush@1h", "Regime switch (CHOP)@6h", "EMA cross@1h"]
    assert weights(st) == {"SMA cross@4h": 0.0, "OI flush@1h": 0.0, "Regime switch (CHOP)@6h": 1.0, "EMA cross@1h": 0.0}
    assert [l.key for l in st.effective_legs()] == ["Regime switch (CHOP)@6h"]    # веса не поплыли: торгует только Regime


def test_add_replace_and_remove(st):
    assert st.add_leg(ema(), 0.0) is False
    assert st.add_leg(Leg("EMA cross", "1h", {"fast": 20, "slow": 50}, 0.0, False, 720), 0.0) is True      # тот же ключ -> замена
    (new,) = [l for l, _, _ in st.leg_cfg() if l.key == "EMA cross@1h"]
    assert new.params == {"fast": 20, "slow": 50} and new.warmup == 720 and len(st.leg_cfg()) == 4
    st.remove_leg("EMA cross@1h")
    assert "EMA cross@1h" not in keys(st) and len(keys(st)) == 3
    st.remove_leg("OI flush@1h")                                                   # штатная нога тоже удаляется
    assert keys(st) == ["SMA cross@4h", "Regime switch (CHOP)@6h"] and "OI flush@1h" not in st.settings()["legs"]
    with pytest.raises(ValueError, match="нет такой"):
        st.remove_leg("OI flush@1h")


def test_remove_all_legs_leaves_flat_target(st):
    for k in keys(st):
        st.remove_leg(k)
    assert st.leg_cfg() == [] and st.effective_legs() == [] and st.catalog() == []     # [] != None: каталог не откатывается к дефолту
    from tradebot.trading.market_feed import compute_target
    assert compute_target(st.effective_legs(), {}, False, {}) == (0.0, {})


def test_add_keeps_weight_sum_and_is_atomic(st):
    with pytest.raises(ValueError, match="сумма весов"):
        st.add_leg(ema(), 0.5)                                                      # 0.5 + 0.5 + 0.5 > 1
    assert keys(st) == [l.key for l in DEFAULT_LEGS] and st.settings()["legs_catalog"] is None          # ничего не записано
    st.add_leg(ema(), 0.33, others={"SMA cross@4h": 0.33, "Regime switch (CHOP)@6h": 0.33})          # «Поровну»
    assert weights(st)["EMA cross@1h"] == 0.33 and sum(weights(st).values()) <= 1.0
    with pytest.raises(ValueError):
        st.add_leg(ema("4h"), 0.0, others={"нет такой": 0.1})
    with pytest.raises(ValueError, match="вес вне"):
        st.add_leg(ema("4h"), 1.5)


def test_max_legs(st):
    tfs = [("EMA cross", "1h"), ("EMA cross", "4h"), ("EMA cross", "6h"), ("SMA cross", "1h"), ("SMA cross", "6h"), ("RSI", "1h"), ("RSI", "4h")]
    for i, (name, tf) in enumerate(tfs):
        st.add_leg(Leg(name, tf, {}, 0.0), 0.0)
    assert len(st.leg_cfg()) == MAX_LEGS
    with pytest.raises(ValueError, match=f"не больше {MAX_LEGS}"):
        st.add_leg(Leg("RSI", "6h", {}, 0.0), 0.0)
    st.add_leg(Leg("RSI", "1h", {"n": 20}, 0.0), 0.0)                               # замена существующей — можно и на лимите


def test_garbage_catalog_does_not_crash():
    s = {"legs_catalog": [{"strategy": "EMA cross"}, "мусор", {"strategy": "A", "tf": "1h", "params": {"x": 1}},
                          {"strategy": "A", "tf": "1h", "params": {}}, {"strategy": "B", "tf": "4h", "params": "не словарь"}]}
    assert [l.key for l in legs_catalog(s)] == ["A@1h"]


def test_window_for_uses_leg_warmup_over_strategy_default():
    assert window_for("1h", [Leg("EMA cross", "1h", {}, 1.0, False, 720)]) == 720
    assert window_for("1h", [Leg("EMA cross", "1h", {}, 1.0)]) == 300
    assert window_for("6h", [DEFAULT_LEGS[2]]) == 480                              # штатные ноги — как раньше (WARMUP_BARS)


def test_snapshot_lists_dynamic_legs_with_long_only(st, tmp_path):
    st.add_leg(Leg("EMA cross", "1h", {"fast": 12, "slow": 26}, 0.0, True), 0.0)
    snap = read_snapshot(tmp_path / "b.db")
    leg = snap["legs"][-1]
    assert leg["key"] == "EMA cross@1h" and leg["long_only"] is True and leg["weight"] == 0.0 and leg["enabled"] is True
    assert set(leg) == {"key", "strategy", "tf", "params", "long_only", "weight", "enabled", "signal", "updated"}


# ---------- предложения ----------
def test_proposal_lifecycle_is_atomic(st):
    pid = st.add_proposal("text", {"leg": {}})
    assert st.open_proposals() == 1 and st.proposal(pid)["status"] == "new"
    assert st.close_proposal(pid, "added") is True and st.close_proposal(pid, "rejected") is False      # второй тап не проходит
    assert st.proposal(pid)["status"] == "added" and st.open_proposals() == 0
    old = st.add_proposal("web", {})
    st._x("UPDATE proposals SET ts='2020-01-01T00:00:00+00:00' WHERE id=?", (old,))
    assert st.open_proposals() == 0                                                # старше суток не считается
    st.expire_proposals()
    assert st.proposal(old)["status"] == "expired" and st.proposal(999) is None


def test_submit_proposals_from_web_thread(tmp_path):
    p = tmp_path / "b.db"
    with pytest.raises(ProposalError) as e:
        submit_proposals([{"x": 1}], p)
    assert e.value.code == "no_bot"
    sqlite3.connect(p).close()                                                      # БД без таблицы = бот старой версии
    with pytest.raises(ProposalError) as e:
        submit_proposals([{"x": 1}], p)
    assert e.value.code == "old_bot"
    p.unlink()
    s = Store(p)
    ids = submit_proposals([{"leg": {"strategy": "EMA cross"}}], p)
    assert s.proposal(ids[0])["source"] == "web" and s.proposal(ids[0])["status"] == "new"      # бот видит запись из другого соединения
    submit_proposals([{"a": 1}] * (MAX_OPEN_PROPOSALS - 1), p)
    with pytest.raises(ProposalError) as e:
        submit_proposals([{"a": 1}], p)
    assert e.value.code == "too_many" and s.open_proposals() == MAX_OPEN_PROPOSALS      # откат: лишнее не записано
    s.close()


def test_heartbeat_age(tmp_path):
    p = tmp_path / "b.db"
    assert state_mod.heartbeat_age(p) is None
    s = Store(p)
    assert state_mod.heartbeat_age(p) is None
    s.set("heartbeat", state_mod.now_iso())
    assert 0 <= state_mod.heartbeat_age(p) < 5
    s.close()


# ---------- проверка окна ----------
def _fake(monkeypatch, name, fn, tf="1h"):
    monkeypatch.setitem(STRATEGIES, name, Strategy(name, "тест", fn, {"x": [1, 2]}, {"x": 1}))
    return Leg(name, tf, {"x": 1}, 0.5)


def test_check_accepts_sma_and_finds_window():
    df = bars(1, 9500, 3)
    r = check_leg(Leg("SMA cross", "1h", {"fast": 10, "slow": 50}, 0.5), df)
    assert r.ok and r.warmup in legcheck.CANDIDATES and r.bad == 0 and r.signal in (-1.0, 0.0, 1.0) and not r.deriv
    assert "воспроизводит бэктест" in r.msg


def test_check_rejects_window_dependent_signal(monkeypatch):
    leg = _fake(monkeypatch, "Тест: зависит от позиции", lambda df, x: pd.Series(np.where(np.arange(len(df)) % 2 == 0, 1.0, -1.0), index=df.index))
    r = check_leg(leg, bars(1, 9500, 3))
    assert not r.ok and r.warmup == 0 and r.bad > 0 and "расходится" in r.msg


def test_check_rejects_fractional_signals(monkeypatch):
    leg = _fake(monkeypatch, "Тест: дробный", lambda df, x: pd.Series(0.5, index=df.index))
    r = check_leg(leg, bars(1, 9500, 3))
    assert not r.ok and "не только -1/0/+1" in r.msg
    big = _fake(monkeypatch, "Тест: плечо", lambda df, x: pd.Series(1.5, index=df.index))
    assert not check_leg(big, bars(1, 9500, 3)).ok


def test_check_needs_enough_history_and_local_data(tmp_path, monkeypatch):
    with pytest.raises(CheckError, match="мало"):
        check_leg(Leg("SMA cross", "1h", {"fast": 10, "slow": 50}, 0.5), bars(1, 3000, 3))
    monkeypatch.setattr(legcheck.klines, "DATA_DIR", tmp_path)
    monkeypatch.setattr(legcheck.klines, "_path", lambda sym, tf: tmp_path / f"{sym}_{tf}.parquet")
    with pytest.raises(CheckError, match="нет локальной истории"):                     # без сети: нет файла — отказ, не докачка
        check_leg(Leg("SMA cross", "1h", {"fast": 10, "slow": 50}, 0.5))


def test_check_flags_data_that_would_wedge_the_live_feed():
    df = bars(1, 9500, 3)
    df.iloc[-5:, df.columns.get_loc("close")] = np.nan                                 # NaN в нужной колонке у края
    r = check_leg(Leg("SMA cross", "1h", {"fast": 10, "slow": 50}, 0.5), df)
    assert not r.ok


# ---------- Telegram ----------
def _ctl(tmp_path, env="testnet", monkeypatch=None, check=OK_CHECK):
    st = Store(tmp_path / "c.db")
    exe = Executor(DryRunExchange(equity=1000), st, lambda *a: None, env)
    trig = []
    clk = NS(t=1000.0)
    c = Controller(st, exe, NS(trigger=lambda: trig.append(1)), env, mono=lambda: clk.t)
    c.trig = trig
    if monkeypatch:
        monkeypatch.setattr("tradebot.telegram.legflow.check_leg", lambda leg: check)
    return c, st


def h(c, data):
    return run(c.handle(kb.parse_cb(data)))


def cbs(screen):
    return [b.callback_data for row in screen.markup.inline_keyboard for b in row]


def texts(screen):
    return [b.text for row in screen.markup.inline_keyboard for b in row]


def propose(c, text=LAB):
    ids, errors = c.propose_from_text(text)
    assert ids and not errors, errors
    return ids[0]


def free_up(st, pct=0.5):
    """Освобождает место: SMA 0, Regime pct."""
    st.set_leg("SMA cross@4h", weight=0.0)
    st.set_leg("Regime switch (CHOP)@6h", weight=pct)


def test_legs_screen_groups_idle_and_offers_add(tmp_path):
    c, st = _ctl(tmp_path)
    s = h(c, "lg")
    assert "Неактивные (1)" in s.text and "➕ Добавить стратегию" in texts(s) and "ad" in cbs(s)
    order = [t for t in texts(s) if " — " in t]
    assert order[-1].startswith("⏸ OI flush")                                           # неактивные кнопки ниже работающих
    assert all(kb.parse_cb(x) for x in cbs(s))


def test_add_screen_and_bad_text(tmp_path):
    c, st = _ctl(tmp_path)
    s = h(c, "ad")
    assert "Копировать для бота" in s.text and "В бота" in s.text and "ad" in cbs(s) or "lg" in cbs(s)
    with pytest.raises(ValueError):
        c.propose_from_text("просто текст")
    ids, errors = c.propose_from_text('Leg("SMA cross", "15m", {})')
    assert not ids and "не поддерживается" in errors[0]
    assert "не поддерживается" in c.add_screen(error=errors[0]).text


def test_draft_card_content_and_weight_buttons(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    free_up(st, 0.5)                                                                    # свободно 50%
    pid = propose(c)
    s = h(c, f"ad:{pid}")
    t = s.text
    assert "Новая стратегия" in t and "EMA cross</b> · 1h" in t and "fast=12, slow=26" in t and "лонг и шорт" in t
    assert "На сайте: доход +10%" in t and "воспроизводит бэктест" in t and "Сигнал по " in t and "⬆️ лонг" in t and "Свободно <code>50%</code>" in t and "отстают" not in t
    assert {f"ad:{pid}:w:10", f"ad:{pid}:w:25", f"ad:{pid}:w:50", f"ad:{pid}:w:0", f"ad:{pid}:x", f"ad:{pid}:eq"} <= set(cbs(s))
    assert f"ad:{pid}:w:75" not in cbs(s) and f"ad:{pid}:w:100" not in cbs(s)           # не помещаются
    assert all(kb.parse_cb(x) for x in cbs(s)) and "ad" in cbs(s) and "h" in cbs(s)       # «Назад» — к списку предложений


def test_add_on_testnet_is_immediate_and_closes_proposal(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    free_up(st, 0.5)
    pid = propose(c)
    s = h(c, f"ad:{pid}:w:25")
    assert s.toast.startswith("Стратегия добавлена") and c.trig
    assert weights(st)["EMA cross@1h"] == 0.25 and st.proposal(pid)["status"] == "added"
    (leg,) = [l for l, _, _ in st.leg_cfg() if l.key == "EMA cross@1h"]
    assert leg.warmup == 480 and leg.params == {"fast": 12, "slow": 26}                  # окно из проверки, не из WARMUP_BARS
    assert "EMA cross · 1h" in s.text                                                    # открылся экран новой стратегии
    assert h(c, f"ad:{pid}:w:25").toast.startswith("Отклонено") and "уже обработано" in h(c, f"ad:{pid}:w:25").toast   # второй тап
    assert any("Добавлена стратегия EMA cross · 1h" in e["text"] for e in st.events())


def test_add_on_mainnet_needs_confirmation_with_before_after(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, "mainnet", monkeypatch)
    free_up(st, 0.5)
    st.log_signals("2026-01-01T00:00:00+00:00", {"Regime switch (CHOP)@6h": 1.0, "SMA cross@4h": 0.0, "OI flush@1h": 0.0}, 0.5)
    pid = propose(c)
    h(c, f"ad:{pid}")                                                                       # карточка открыта: проверка выполнена
    s = h(c, f"ad:{pid}:w:50")
    assert "EMA cross@1h" not in weights(st) and "Добавить стратегию?" in s.text and "MAINNET" not in s.text
    assert "вес 0% → 50%" in s.text and "Сумма весов <b>50% → 100%</b>" in s.text and "Цель сейчас <b>+0.50 → +1.00</b>" in s.text
    assert cbs(s) == [f"ad:{pid}:w:50:y", f"ad:{pid}"]
    assert h(c, f"ad:{pid}:w:50:y").toast.startswith("Стратегия добавлена") and weights(st)["EMA cross@1h"] == 0.5
    new = propose(c, 'Leg("Parabolic SAR", "1h", {"af_start": 0.02, "af_step": 0.02, "af_max": 0.2})')
    assert h(c, f"ad:{new}:w:0").toast.startswith("Стратегия добавлена")                    # НОВАЯ без веса — в торговлю не попадает: без подтверждения
    rep_ = propose(c, 'Leg("SMA cross", "4h", {"fast": 20, "slow": 120})')
    assert "Заменить параметры стратегии?" in h(c, f"ad:{rep_}:w:0").text                   # а замена существующей меняет цель — подтверждается всегда
    assert "Заменить параметры стратегии?" in h(c, f"ad:{rep_}:w:25").text


def test_confirmation_expires_and_cancel_returns_to_card(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, "mainnet", monkeypatch)
    free_up(st, 0.5)
    pid = propose(c)
    h(c, f"ad:{pid}:w:25")
    c.clk = None
    c.mono = lambda: 99999.0                                                              # прошло больше 60 с
    assert h(c, f"ad:{pid}:w:25:y").toast == "Подтверждение устарело" and "EMA cross@1h" not in weights(st)


def test_equal_split_rebalances_active_legs(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)                                        # SMA 0.5 + Regime 0.5: места нет
    pid = propose(c)
    s = h(c, f"ad:{pid}")
    assert "Места нет" in s.text and f"ad:{pid}:w:10" not in cbs(s) and f"ad:{pid}:eq" in cbs(s)
    r = h(c, f"ad:{pid}:eq")
    assert weights(st) == {"SMA cross@4h": 0.33, "OI flush@1h": 0.0, "Regime switch (CHOP)@6h": 0.33, "EMA cross@1h": 0.33}
    assert sum(weights(st).values()) <= 1.0 and r.toast.startswith("Стратегия добавлена")


def test_overweight_is_rejected_server_side(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    pid = propose(c)
    s = h(c, f"ad:{pid}:w:50")                                                              # руками подсунули вес, которому нет места
    assert s.toast.startswith("Отклонено") and "сумма весов" in s.toast and "EMA cross@1h" not in weights(st)
    assert st.proposal(pid)["status"] == "new"                                              # не потеряно: можно выбрать другой вес


def test_failed_check_blocks_add_and_error_is_retryable(tmp_path, monkeypatch):
    bad = LegCheck(False, 0, 100, 7, 1.0, False, "на окне 720 свечей сигнал расходится с бэктестом в 7 из 100 точек")
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch, check=bad)
    pid = propose(c)
    s = h(c, f"ad:{pid}")
    assert "❌" in s.text and "расходится" in s.text and not [x for x in cbs(s) if ":w:" in x or x.endswith(":eq")]
    assert "🔄 Проверить снова" not in texts(s) and f"ad:{pid}:x" in cbs(s)
    assert h(c, f"ad:{pid}:w:25").toast.startswith("Отклонено") and "проверка не пройдена" in h(c, f"ad:{pid}:w:25").toast

    def boom(leg):
        raise CheckError("нет локальной истории 1h")
    monkeypatch.setattr("tradebot.telegram.legflow.check_leg", boom)
    pid2 = propose(c)
    s = h(c, f"ad:{pid2}")
    assert "нет локальной истории" in s.text and "🔄 Проверить снова" in texts(s)
    monkeypatch.setattr("tradebot.telegram.legflow.check_leg", lambda leg: OK_CHECK)
    free_up(st, 0.5)
    assert f"ad:{pid2}:w:10" in cbs(h(c, f"ad:{pid2}"))                                     # ошибка не кэшируется: перепроверка прошла


def test_reject_and_stale_draft(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    pid = propose(c)
    assert h(c, f"ad:{pid}:x").toast == "Предложение отклонено" and st.proposal(pid)["status"] == "rejected"
    s = h(c, f"ad:{pid}")
    assert s.toast.startswith("Отклонено") and "уже обработано" in s.toast and "Новая стратегия" in s.text      # экран «Добавить», не падение
    assert h(c, "ad:999").toast.startswith("Отклонено")


def test_replacing_same_key_says_so_and_keeps_weight(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    pid = propose(c, 'Leg("SMA cross", "4h", {"fast": 20, "slow": 120}, weight=0.5)')
    s = h(c, f"ad:{pid}")
    assert "Заменит параметры" in s.text and "Такая стратегия уже есть (вес 50%)" in s.text
    assert "✅ 50%" in texts(s) and f"ad:{pid}:w:0" not in cbs(s)                         # текущий вес отмечен; «без веса» для замены нет
    h(c, f"ad:{pid}:w:50")
    (leg,) = [l for l, _, _ in st.leg_cfg() if l.key == "SMA cross@4h"]
    assert leg.params == {"fast": 20, "slow": 120} and len(st.leg_cfg()) == 3 and weights(st)["SMA cross@4h"] == 0.5


def test_pending_list_and_open_proposal_cap(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    ids = [propose(c, f'Leg("EMA cross", "1h", {{"fast": {12 + i}, "slow": 26}}, weight=0.5)') for i in range(MAX_OPEN_PROPOSALS)]
    s = h(c, "ad")
    assert all(f"ad:{i}" in cbs(s) for i in ids) and "Ждут решения" in s.text
    got, errors = c.propose_from_text(EMA)
    assert not got and "слишком много" in errors[0]


def test_text_with_many_legs_opens_queue(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    ids, errors = c.propose_from_text(EMA + '\nLeg("SMA cross", "4h", {"fast": 10, "slow": 100})\nLeg("Nope", "4h", {})')
    assert len(ids) == 2 and len(errors) == 1 and "неизвестная стратегия" in errors[0]


# ---------- удаление ----------
def test_remove_on_clean_db_keeps_default_weights(st):
    st.remove_leg("OI flush@1h")                                                    # в БД не было явных весов: дефолты должны сохраниться
    assert weights(st) == {"SMA cross@4h": 0.5, "Regime switch (CHOP)@6h": 0.5}
    assert [l.key for l in st.effective_legs()] == ["SMA cross@4h", "Regime switch (CHOP)@6h"]


def test_remove_always_asks_confirmation_even_on_testnet(tmp_path):
    c, st = _ctl(tmp_path)
    s = h(c, "lg:1:rm")
    assert "OI flush@1h" in keys(st) and "Удалить стратегию?" in s.text and cbs(s) == ["lg:1:rm:y", "lg:1"]
    assert "Сумма весов <b>100% → 100%</b>" in s.text and "единственная" not in s.text
    r = h(c, "lg:1:rm:y")
    assert keys(st) == ["SMA cross@4h", "Regime switch (CHOP)@6h"] and r.toast == "Удалена: OI flush · 1h" and c.trig
    assert "OI flush" not in r.text and "Неактивные" not in r.text


def test_remove_last_active_leg_warns_and_sets_flat_target(tmp_path):
    c, st = _ctl(tmp_path, "mainnet")
    st.set_leg("SMA cross@4h", weight=0.0)
    st.log_signals("2026-01-01T00:00:00+00:00", {"Regime switch (CHOP)@6h": 1.0, "SMA cross@4h": 0.0, "OI flush@1h": 0.0}, 0.5)
    s = h(c, "lg:2:rm")
    assert "единственная работающая" in s.text and "Цель сейчас <b>+0.50 → +0.00</b>" in s.text
    h(c, "lg:2:rm:y")
    assert [l.key for l in st.effective_legs()] == []                                       # цель станет 0 — позицию закроет раннер


def test_remove_confirmation_is_bound_to_the_leg_not_the_index(tmp_path):
    c, st = _ctl(tmp_path)
    st.add_leg(ema(), 0.0)
    h(c, "lg:1:rm")                                                                         # взвели удаление OI flush (индекс 1)
    st.remove_leg("SMA cross@4h")                                                           # список сдвинулся мимо контроллера: индекс 1 — уже Regime
    assert keys(st)[1] == "Regime switch (CHOP)@6h"
    s = h(c, "lg:1:rm:y")
    assert s.toast.startswith("Отклонено") and "изменился" in s.toast and "Regime switch (CHOP)@6h" in keys(st)


def test_catalog_changes_disarm_pending_confirmations(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, "mainnet", monkeypatch)
    free_up(st, 0.5)
    h(c, "lg:0:w:10")                                                                       # взвели изменение веса (индекс 0 = SMA)
    pid = propose(c)
    h(c, f"ad:{pid}:w:0")                                                                   # любое изменение каталога снимает все взведённые подтверждения
    assert h(c, "lg:0:w:10:y").toast == "Подтверждение устарело"


def test_leg_screen_shows_params_and_delete_button(tmp_path):
    c, st = _ctl(tmp_path)
    s = h(c, "lg:0")
    assert "fast=10, slow=100" in s.text and "лонг и шорт" in s.text and "lg:0:rm" in cbs(s)
    assert "🗑 Удалить" in texts(s) and all(kb.parse_cb(x) for x in cbs(s))


def test_callback_whitelist_for_new_actions():
    for ok in ("ad", "ad:1", "ad:123456", "ad:7:x", "ad:7:w:0", "ad:7:w:100", "ad:7:w:25:y", "ad:7:eq", "ad:7:eq:y", "lg:3:rm", "lg:3:rm:y"):
        assert kb.parse_cb(ok) is not None, ok
    for bad in ("ad:", "ad:x", "ad:1234567", "ad:7:w", "ad:7:w:101", "ad:7:w:05", "ad:7:rm", "ad:7:x:y", "ad:7:eq:n", "lg:10:rm", "lg:rm", "lg:3:rm:n", "ad:-1"):
        assert kb.parse_cb(bad) is None, bad


# ---------- роутер: текст с сайта и кнопка из уведомления ----------
class Bot:
    def __init__(self):
        self.sent, self.edits, self.deleted, self.pinned = [], [], [], []
        self._n = 100

    async def send_message(self, chat, text, **kw):
        self._n += 1
        self.sent.append((chat, text, kw))
        return NS(message_id=self._n)

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None, parse_mode=None):
        self.edits.append((message_id, text))

    async def delete_message(self, chat, msg): self.deleted.append(msg)
    async def pin_chat_message(self, chat, msg, disable_notification=True): pass


def _router(tmp_path, monkeypatch, env="testnet"):
    c, st = _ctl(tmp_path, env, monkeypatch)
    p = Panel(st, c.handle, clock=lambda: 0.0, sleep=asyncio.sleep)
    return c, st, p, build_router(c, p)


def test_pasting_site_text_opens_card_in_panel_and_deletes_message(tmp_path, monkeypatch):
    c, st, p, r = _router(tmp_path, monkeypatch)
    free_up(st, 0.5)
    bot, gone = Bot(), []

    async def delete():
        gone.append(1)
    leg_text = next(h_.callback for h_ in r.message.handlers if h_.callback.__name__ == "leg_text")

    async def go():
        await p.open(bot, OWNER, c.home())
        mid = p.ref["msg"]
        await leg_text(NS(text=LAB, bot=bot, chat=NS(id=OWNER), delete=delete))
        assert gone == [1] and len(bot.sent) == 1                                           # панель одна, новых сообщений нет
        assert any("Проверяю" in t for _, t in bot.edits) and "Новая стратегия" in bot.edits[-1][1] and bot.edits[-1][0] == mid
        await leg_text(NS(text='Leg("Nope", "4h", {})', bot=bot, chat=NS(id=OWNER), delete=delete))
        assert "неизвестная стратегия" in bot.edits[-1][1]
        p.cancel()
    run(go())


def test_ordinary_messages_are_not_touched_by_the_text_handler(tmp_path, monkeypatch):
    c, st, p, r = _router(tmp_path, monkeypatch)
    flt = next(h_ for h_ in r.message.handlers if h_.callback.__name__ == "leg_text").filters[0].callback
    assert flt(NS(text=LAB)) and flt(NS(text=EMA))
    assert not flt(NS(text="привет")) and not flt(NS(text=None)) and not flt(NS(text="/start"))      # обычные сообщения, как и раньше, молча игнорируются


def test_notice_button_works_from_the_notice_message_and_deletes_it(tmp_path, monkeypatch):
    c, st, p, r = _router(tmp_path, monkeypatch)
    free_up(st, 0.5)
    pid = propose(c)
    bot = Bot()
    cb = r.callback_query.handlers[0].callback
    answers, dead = [], []

    async def answer(text=None, show_alert=False):
        answers.append(text)

    async def kill():
        dead.append(1)

    async def go():
        await p.open(bot, OWNER, c.home())
        mid = p.ref["msg"]
        q = NS(data=f"ad:{pid}", message=NS(chat=NS(id=OWNER), message_id=555, delete=kill), from_user=NS(id=OWNER), bot=bot, answer=answer)
        await cb(q)
        assert "Новая стратегия" in bot.edits[-1][1] and bot.edits[-1][0] == mid and dead == [1]    # карточка в панели, уведомление убрано
        q2 = NS(data=f"ad:{pid}:w:10", message=NS(chat=NS(id=OWNER), message_id=555, delete=kill), from_user=NS(id=OWNER), bot=bot, answer=answer)
        await cb(q2)
        assert "старое меню" in answers[-1] and "EMA cross@1h" not in weights(st)               # действия — только из панели
        p.cancel()
    run(go())


def test_watcher_announces_web_proposals_once(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    (d,) = parse_leg_text(LAB)
    ids = submit_proposals([draft_payload(d)], tmp_path / "c.db")
    w = ProposalWatcher(c, OWNER, poll_s=0)
    bot = Bot()
    w.attach(bot)
    assert run(w.tick()) == 1 and run(w.tick()) == 0
    chat, text, kw = bot.sent[0]
    assert chat == OWNER and "Предложение с сайта" in text and "EMA cross" in text and "окно проверено" in text
    assert [b.callback_data for row in kw["reply_markup"].inline_keyboard for b in row] == [f"ad:{ids[0]}"]
    assert st.proposal(ids[0])["status"] == "announced" and st.proposal(ids[0])["payload"]["check"]["ok"] is True
    free_up(st, 0.5)
    assert h(c, f"ad:{ids[0]}:w:25").toast.startswith("Стратегия добавлена")                    # announced -> можно добавить


def test_watcher_survives_telegram_failure_and_retries(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    (d,) = parse_leg_text(LAB)
    ids = submit_proposals([draft_payload(d)], tmp_path / "c.db")
    w = ProposalWatcher(c, OWNER, poll_s=0)
    bot = Bot()

    async def failing(*a, **k):
        raise RuntimeError("telegram down")
    bot.send_message = failing
    w.attach(bot)
    with pytest.raises(RuntimeError):
        run(w.tick())
    assert st.proposal(ids[0])["status"] == "new"                                           # не потеряно: повтор на следующем тике
    bot2 = Bot()
    w.attach(bot2)
    assert run(w.tick()) == 1 and st.proposal(ids[0])["status"] == "announced"


# ---------- веб-API ----------
def test_api_proposal_endpoint(tmp_path, monkeypatch):
    from tradebot.web.api import app
    client = TestClient(app)
    monkeypatch.setattr(state_mod, "DB_PATH", tmp_path / "w.db")
    r = client.post("/api/bot/proposals", json={"text": LAB})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_bot"                  # бот не запускался
    s = Store(tmp_path / "w.db")
    r = client.post("/api/bot/proposals", json={"text": LAB})
    assert r.status_code == 200 and r.json() == {"ok": True, "added": 1, "errors": [], "bot_alive": False}
    s.set("heartbeat", state_mod.now_iso())
    mixed = LAB + '\nLeg("Nope", "4h", {})'
    r = client.post("/api/bot/proposals", json={"text": mixed})
    assert r.json()["added"] == 1 and "неизвестная стратегия" in r.json()["errors"][0] and r.json()["bot_alive"] is True
    row = s.proposals("new")[0]
    assert row["source"] == "web" and row["payload"]["leg"]["strategy"] == "EMA cross" and s.settings()["legs_catalog"] is None   # торговля не тронута
    assert client.post("/api/bot/proposals", json={"text": 'Leg("Nope", "4h", {})'}).status_code == 422
    assert client.post("/api/bot/proposals", json={"text": "просто текст"}).status_code == 422
    assert client.post("/api/bot/proposals", json={"text": ""}).status_code == 422
    assert client.post("/api/bot/proposals", json={"text": "x" * 5000}).status_code == 422
    assert client.post("/api/bot/proposals", json={"text": LAB}, headers={"Origin": "http://evil.example"}).status_code == 403
    for _ in range(5):
        client.post("/api/bot/proposals", json={"text": LAB})
    full = client.post("/api/bot/proposals", json={"text": LAB})
    assert full.status_code == 429 and full.json()["detail"]["code"] == "too_many"
    s.close()


# ---------- замечания финального ревью ----------
def test_watcher_ignores_proposals_pasted_in_telegram(tmp_path, monkeypatch):
    """Присланный в чат текст уже показан карточкой в панели: второго уведомления «с сайта» быть не должно."""
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    pid = propose(c)                                                                        # source = text
    w = ProposalWatcher(c, OWNER, poll_s=0)
    bot = Bot()
    w.attach(bot)
    assert run(w.tick()) == 0 and not bot.sent and st.proposal(pid)["status"] == "new"


def test_watcher_does_not_resurrect_a_proposal_decided_during_the_check(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    (d,) = parse_leg_text(LAB)
    (pid,) = submit_proposals([draft_payload(d)], tmp_path / "c.db")
    w = ProposalWatcher(c, OWNER, poll_s=0)
    bot = Bot()
    w.attach(bot)
    real = c.ensure_check

    async def decided_meanwhile(i):
        out = await real(i)
        st.close_proposal(i, "rejected")                                                    # владелец успел отклонить, пока шла проверка
        return out
    c.ensure_check = decided_meanwhile
    assert run(w.tick()) == 0 and not bot.sent and st.proposal(pid)["status"] == "rejected"


def test_claim_is_atomic_and_unclaim_never_touches_closed(st):
    pid = st.add_proposal("web", {})
    assert st.claim_proposal(pid) is True and st.claim_proposal(pid) is False
    st.unclaim_proposal(pid)
    assert st.proposal(pid)["status"] == "new"
    st.close_proposal(pid, "added")
    st.unclaim_proposal(pid)
    assert st.proposal(pid)["status"] == "added"
    assert [r["id"] for r in st.proposals("new", source="web")] == [] and st.proposals("added", source="text") == []


def test_stale_local_data_is_flagged_on_the_card(tmp_path, monkeypatch):
    old = (pd.Timestamp.now(tz="UTC").floor("1h") - pd.Timedelta(days=3)).isoformat()
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch, check=LegCheck(True, 480, 120, 0, 1.0, False, "окно 480", old))
    free_up(st, 0.5)
    t = h(c, f"ad:{propose(c)}").text
    assert "отстают" in t and "приблизительные" in t


def test_replacing_a_disabled_leg_says_it_will_be_enabled(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, "mainnet", monkeypatch)
    free_up(st, 0.5)
    st.set_leg("SMA cross@4h", enabled=False)
    pid = propose(c, 'Leg("SMA cross", "4h", {"fast": 20, "slow": 120})')
    h(c, f"ad:{pid}")
    s = h(c, f"ad:{pid}:w:25")
    assert "выключена" in s.text and "будет <b>включена</b>" in s.text
    h(c, f"ad:{pid}:w:25:y")
    assert [l.key for l in st.effective_legs()].count("SMA cross@4h") == 1


def test_replacing_an_idle_leg_when_there_is_no_room_still_has_a_weight_button(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)                                         # SMA 50 + Regime 50; OI flush: 0
    pid = propose(c, 'Leg("OI flush", "1h", {"n": 6, "drop": 3.0, "hold_bars": 24}, long_only=True)')
    s = h(c, f"ad:{pid}")
    assert "Заменит параметры" in s.text and f"ad:{pid}:w:0" in cbs(s) and "⏸ Заменить без веса" in texts(s)


def test_latch_is_mentioned_before_a_confirmation_that_changes_the_target(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, "mainnet", monkeypatch)
    st.set("latch", True)
    st.log_signals("2026-01-01T00:00:00+00:00", {"Regime switch (CHOP)@6h": 1.0, "SMA cross@4h": 1.0, "OI flush@1h": 0.0}, 1.0)
    free_up(st, 0.5)
    pid = propose(c)
    assert "защёлка" in h(c, f"ad:{pid}:w:25").text
    assert "защёлка" in h(c, "lg:2:rm").text
    st.set("latch", False)
    assert "защёлка" not in h(c, "lg:2:rm").text


def test_bot_revalidates_a_payload_written_by_another_process(tmp_path, monkeypatch):
    c, st = _ctl(tmp_path, monkeypatch=monkeypatch)
    free_up(st, 0.5)
    pid = st.add_proposal("web", {"leg": {"strategy": "EMA cross", "tf": "1h", "params": {"fast": 5000, "slow": 26}, "long_only": False, "weight": 0.5},
                                  "result": "", "bt_leverage": None, "notes": [], "check": {"ok": True, "warmup": 480, "signal": 1.0}})
    s = h(c, f"ad:{pid}:w:25")
    assert s.toast.startswith("Отклонено") and "вне допустимого диапазона" in s.toast and "EMA cross@1h" not in weights(st)
    assert st.proposal(pid)["status"] == "new"


def test_unknown_strategy_in_catalog_is_skipped_by_the_runner_and_reported(st):
    ghost = {"strategy": "Удалена в новой версии", "tf": "1h", "params": {}, "long_only": False, "warmup": 0}
    st.add_leg(Leg("EMA cross", "1h", {"fast": 12, "slow": 26}, 0.0), 0.0)             # фиксирует каталог в БД
    st.set("s_legs_catalog", st.settings()["legs_catalog"] + [ghost])                   # стратегию «убрали» из реестра в новой версии
    st.set("s_legs", {"SMA cross@4h": {"weight": 0.5, "enabled": True}, "Regime switch (CHOP)@6h": {"weight": 0.2, "enabled": True},
                      "Удалена в новой версии@1h": {"weight": 0.3, "enabled": True}})
    assert "Удалена в новой версии@1h" in keys(st)                                      # в списке остаётся: владелец может её удалить
    assert [l.key for l in st.effective_legs()] == ["SMA cross@4h", "Regime switch (CHOP)@6h"]      # а в расчёт цели не идёт (не KeyError каждый час)
    assert any("Удалена в новой версии@1h" in n and "нет в этой версии" in n for n in st.weight_notes())
    st.remove_leg("Удалена в новой версии@1h")
    assert not st.weight_notes()
