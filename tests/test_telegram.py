"""Telegram: фильтр владельца, валидация callback_data, навигация, подтверждения, панель и автообновление."""
import asyncio
from types import SimpleNamespace as NS

import pytest
from html.parser import HTMLParser

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from tradebot.telegram import keyboards as kb
from tradebot.telegram import panel as panel_mod
from tradebot.telegram import texts as tx
from tradebot.telegram.texts import fit_html, strip_html
from tradebot.telegram.handlers import Controller, build_router
from tradebot.telegram.middleware import OwnerOnly
from tradebot.telegram.notify import Notifier
from tradebot.telegram.panel import Panel, Screen
from tradebot.trading.exchange import ExchangeError
from fakes.exchange import DryRunExchange
from tradebot.trading.executor import Executor
from tradebot.trading.state import Store

OWNER = 42


def run(c):
    return asyncio.run(c)


# ---------- фильтр владельца ----------
def upd(uid=OWNER, chat_type="private", chat_id=OWNER, kind="message"):
    user, chat = NS(id=uid), NS(type=chat_type, id=chat_id)
    if kind == "message":
        return NS(message=NS(from_user=user, chat=chat), callback_query=None)
    return NS(message=None, callback_query=NS(from_user=user, message=NS(chat=chat)))


def passes(u):
    hit = []

    async def h(e, d):
        hit.append(1)
        return "ok"

    run(OwnerOnly(OWNER)(h, u, {}))
    return bool(hit)


def test_owner_filter():
    assert passes(upd()) and passes(upd(kind="cb"))
    assert not passes(upd(uid=7)) and not passes(upd(uid=7, kind="cb"))
    assert not passes(upd(chat_type="group", chat_id=-100)) and not passes(upd(chat_type="supergroup", kind="cb"))
    assert not passes(NS(message=None, callback_query=None))
    assert not passes(NS(message=NS(from_user=None, chat=NS(type="private", id=OWNER)), callback_query=None))


# ---------- callback_data ----------
def test_callback_validation():
    for ok in ("h", "tr:on:y", "lv:s:3", "lv:s:2:y", "lg:t:1", "lg:w:0:p", "lg", "lg:0", "lg:9", "lg:2:t", "lg:2:t:y", "lg:1:w:0", "lg:1:w:35", "lg:1:w:100:y",
               "lg:eq", "lg:eq:y", "lg:t:1:y", "lg:w:0:m:y",
               "sl:a:m1", "sl:s:8", "rk:dd:p5", "rk:dd:s:25", "rk:da:s:10:y", "pn:y", "tx:0", "ev:9", "sg", "np"):
        assert kb.parse_cb(ok) is not None, ok
    for bad in ("lv:s:4", "lv:s:0", "cap", "cap:a:p100", "cap:s:150", "cap:s:1000:y", "cap:a:p99999", "cap:s:75",      # cap удалён
                "sl:s:7", "sl:s:50",
                "rk:dd:s:99", "rk:da:s:25", "tr:on:n", "x", "", None, "lg:t:-1", "lg:w:1:x", "lg:10", "lg:1:w:101", "lg:1:w:005", "lg:1:w:-5", "lg:1:w", "lg:1:x", "lg:eq:n", "lg:1:t:n", "rk:dd:p50", "m" * 100,
                "lv:s:3:y:z", "pn:Y", "lг", "sl:a:p5", "tx:10", "tx:-1", "tx", "ev:a", "m", "st",
                "pp"):                  # экран «Режим PAPER» удалён: старая кнопка не проходит белый список
        assert kb.parse_cb(bad) is None, bad


def _all_callbacks(markup):
    return [bt.callback_data for row in markup.inline_keyboard for bt in row]


def test_every_button_is_whitelisted_and_nav_present():
    ms = [kb.home_menu(True, False, False, True), kb.home_menu(False, True, False, False), kb.home_menu(False, False, True, False),
          kb.settings_menu("1x", "8%", "25%"), kb.confirm("pk:y", "set"), kb.legs_menu([("a", 0.5, True)]),
          kb.leverage_menu(2), kb.stop_menu(8), kb.risk_menu(25, 10), kb.position_menu(True),
          kb.pager_menu("tx", 0, 3), kb.pager_menu("ev", 2, 3), kb.trade_notice_menu()]
    for m in ms:
        assert all(kb.parse_cb(c) for c in _all_callbacks(m))
    assert any(b.text.startswith("✅") for b in kb.leverage_menu(2).inline_keyboard[0])        # текущее выделено
    assert [b.text for b in kb.leverage_menu(2).inline_keyboard[0]][1] == "✅ 2x"


# ---------- контроллер ----------
@pytest.fixture
def ctl(tmp_path):
    st = Store(tmp_path / "b.db")
    ex = DryRunExchange(equity=1000)
    exe = Executor(ex, st, lambda *a: None, "testnet")
    exe.poll_delay = 0
    clk = NS(t=1000.0)
    trig = []
    c = Controller(st, exe, NS(trigger=lambda: trig.append(1)), "testnet", mono=lambda: clk.t)
    c.clk = clk
    return c, st, ex, trig


def h(c, data):
    return run(c.handle(kb.parse_cb(data)))


def test_sub_screens_have_back_home_refresh(ctl):
    c = ctl[0]
    for d in ("set", "lv", "sl", "rk", "lg", "pos", "sg", "tx:0", "ev:0", "lt"):
        cbs = _all_callbacks(h(c, d).markup)
        assert "h" in cbs, d                                   # 🏠 Главная
        texts = [b.text for row in h(c, d).markup.inline_keyboard for b in row]
        assert "⬅️ Назад" in texts and "↻ Обновить" in texts, d
    assert "Главная › Настройки › Плечо" in h(c, "lv").text
    s = h(c, "set")
    assert not any("Капитал" in b.text or b.callback_data.startswith("cap") for row in s.markup.inline_keyboard for b in row)
    assert [b.text for row in s.markup.inline_keyboard for b in row][:3] == ["⚖️ Плечо: 1x", "🛡 Стоп: 8%", "🚨 Риск-лимиты: 25% / 10%"]


def test_home_text_and_buttons(ctl):
    c, st, *_ = ctl
    st.set("position", {"side": "long", "qty": 0.012, "entry": 98000.0, "upnl": 12.0, "mark": 99000.0})
    st.set("equity", 1234.5)
    s = h(c, "h")
    assert "TESTNET" not in s.text and "Тестовая сеть" in s.text                     # не боевая биржа -> одна строка-предупреждение
    assert "<code>1 234.50</code>" in s.text and "Нереализовано <code>+12.00 $</code>" in s.text and "Следующий бар" in s.text
    assert "⬆️ <b>ЛОНГ</b> <code>0.012</code> BTC" in s.text and "Своих денег" in s.text and "с плечом <code>1x</code>" in s.text
    assert s.live and s.view == ("h",)
    texts = [b.text for row in s.markup.inline_keyboard for b in row]
    assert texts[0] == "▶️ Включить торговлю" and texts[-1] == "🛑 Паника"
    st.set("killed", True)
    assert h(c, "h").markup.inline_keyboard[0][0].text == "🔓 Снять блокировку"


def test_position_screen_shows_pnl_without_position(ctl):
    c, st, *_ = ctl
    st.set("equity", 160.0)
    st.set("day_start_equity", 160.5)
    st.set("baseline_equity", 150.0)
    t = h(c, "pos").text
    assert "Позиция: нет" in t
    assert ("P/L сегодня: <code>−0.50 $</code> (<code>−0.31%</code>)" in t
            and "P/L всего: <code>+10.00 $</code> (<code>+6.67%</code>)" in t and "(от 150.00 $)" in t)


def test_position_screen_pnl_placeholders_when_no_data(ctl):
    c = ctl[0]
    t = h(c, "pos").text
    assert "P/L сегодня: —" in t and "P/L всего: —" in t


def test_enable_needs_fresh_confirm(ctl):
    c, st, ex, trig = ctl
    assert not st.settings()["enabled"]
    s = h(c, "tr:on")
    assert not st.settings()["enabled"] and "Включить торговлю" in s.text and "TESTNET" not in s.text
    assert [b.text for b in s.markup.inline_keyboard[0]] == ["✅ Подтвердить", "❌ Отмена"]
    c.clk.t += 61                                              # подтверждение устарело
    assert h(c, "tr:on:y").toast == "Подтверждение устарело" and not st.settings()["enabled"]
    h(c, "tr:on")
    c.clk.t += 59
    h(c, "tr:on:y")
    assert st.settings()["enabled"] and trig
    n_trig = len(trig)
    assert h(c, "tr:on:y").toast == "Уже выполнено" and len(trig) == n_trig   # двойной тап: одноразовое, без повторного действия
    c.clk.t += 6
    assert h(c, "tr:on:y").toast == "Подтверждение устарело" and len(trig) == n_trig   # позже — обычное «устарело»
    st.set("killed", True)
    st.set_setting("enabled", False)
    assert h(c, "tr:on").toast.startswith("Отклонено") and not st.settings()["enabled"]


def test_confirm_without_arm_is_rejected_for_all_two_step(ctl):
    c, st, ex, _ = ctl
    for d in ("lv:s:3:y", "pk:y", "cl:y", "pn:y", "ke:y", "tr:on:y"):
        assert h(c, d).toast == "Подтверждение устарело", d
    assert st.settings()["leverage"] == 1.0 and st.get("peak_equity") is None


def test_leverage_two_step_and_cap(ctl):
    c, st, ex, _ = ctl
    h(c, "lv:s:3")
    assert st.settings()["leverage"] == 1.0
    h(c, "lv:s:3:y")
    assert st.settings()["leverage"] == 3.0 and ex.account["leverage"] == 3.0


def test_presets_and_steps_applied(ctl):
    c, st, *_ = ctl
    h(c, "sl:s:10")
    assert st.settings()["stop_pct"] == pytest.approx(0.10)
    h(c, "rk:dd:s:30")
    h(c, "rk:da:s:5")
    s = st.settings()
    assert s["kill_dd"] == pytest.approx(0.30) and s["kill_daily"] == pytest.approx(0.05)
    for _ in range(40):
        h(c, "sl:a:p1")
    assert st.settings()["stop_pct"] <= 0.30


def test_min_order_warning_uses_live_account_balance(ctl):
    c, st, ex, _ = ctl
    assert "минимального ордера" not in h(c, "lg").text and "минимального ордера" not in h(c, "lg:0").text   # 1000 USDT хватает
    ex.cash = 100.0                                            # счёт похудел: 100 * 0.97 * 1x * 50% / 1e5 = 0.000485 < minQty
    c.clk.t += 31                                              # кэш limits() живёт 30 с
    t = h(c, "lg").text
    assert "на счёте 100 USDT (в работе 97%)" in t and "меньше минимального ордера" in t
    t0 = h(c, "lg:0").text
    assert "На счёте 100 USDT (в работе 97%)" in t0 and "меньше минимального ордера" in t0
    assert "при капитале" not in (t + t0)
    ex.cash = 1000.0
    c.clk.t += 31
    assert "минимального ордера" not in h(c, "lg").text
    ex.sizing_error = ExchangeError("net", -1)                 # баланс неизвестен -> без предупреждения, без падения
    c.clk.t += 31
    assert "минимального ордера" not in h(c, "lg").text


def test_stale_capital_callbacks_degrade_to_polite_toast(tmp_path):
    c, p, r = _router(tmp_path)
    bot = FakeBot()
    cb = r.callback_query.handlers[0].callback

    async def go():
        await p.open(bot, OWNER, c.home())
        mid = p.ref["msg"]
        for data in ("cap", "cap:s:250", "cap:a:p100", "cap:s:1000:y"):          # кнопки из старого сообщения
            q, ans = _fake_q(bot, data, mid)
            await cb(q)                                                           # исключения нет
            assert "Капитал больше не настраивается" in ans[0][0] and "🏠 Главная" in bot.edits[-1][1], data
        q, ans = _fake_q(bot, "cap:s:250", 999)                                   # и из чужого/устаревшего сообщения
        await cb(q)
        assert "Капитал больше не настраивается" in ans[0][0]
        p.cancel()
    run(go())
    scr = run(c.handle(("cap",)))                                                 # прямой вызов контроллера тоже безопасен
    assert scr.toast.startswith("Отклонено") and "🏠 Главная" in scr.text
    assert "allocated_capital" not in c.st.settings()


def test_stop_leverage_weight_warning_in_screens_and_confirm(ctl):
    c, st, ex, _ = ctl
    for d in ("sl", "lv", "rk", "set", "tr:on"):               # 8% * 1x * 100% = 8% < 10%: молчим
        assert "Стоп × плечо × веса" not in h(c, d).text, d
    st.set_setting("leverage", 2)                              # 8% * 2x * 100% = 16% >= 10% (лимит убытка за сутки)
    for d in ("sl", "lv", "rk", "set", "tr:on"):
        t = h(c, d).text
        assert "Стоп × плечо × веса" in t, d
        assert "<code>8%</code> при плече <code>2x</code> и весах <code>100%</code> = около <code>16%</code> счёта за одну сделку" in t, d
        assert "лимита убытка за сутки <code>10%</code>" in t and "сначала сработает биржевой стоп, затем kill-switch" in t, d
    st.set_setting("kill_daily", 0.20)                         # меньший лимит теперь просадка 25%? нет: 20% > 16% -> тихо
    assert "Стоп × плечо × веса" not in h(c, "sl").text
    st.set_setting("stop_pct", 0.20)                           # 20% * 2 * 100% = 40% >= min(25%, 20%)
    t = h(c, "sl").text
    assert "около <code>40%</code> счёта" in t and "лимита убытка за сутки <code>20%</code>" in t
    st.set_setting("kill_daily", 0.30)
    t = h(c, "sl").text                                        # теперь меньший лимит — просадка 25%
    assert "лимита просадки от пика <code>25%</code>" in t
    st.set_leg("SMA cross@4h", weight=0.0)
    st.set_leg("Regime switch (CHOP)@6h", weight=0.25)         # сумма весов 25%: 20% * 2 * 25% = 10% < 25%
    assert "Стоп × плечо × веса" not in h(c, "sl").text
    assert "счёта за одну сделку" not in h(c, "set").text


def test_trade_on_confirm_has_no_capital_and_resets_sizing(ctl):
    c, st, ex, trig = ctl
    st.set("equity", 1234.5)
    t = h(c, "tr:on").text
    assert "весь счёт <code>1 234.50 USDT</code> (в работе 97%, технический резерв 3%)" in t and "апитал" not in t
    st.set("acted_exposure", 0.5)
    st.set("acted_qty", 0.01)
    h(c, "tr:on:y")
    assert st.get("acted_exposure") is None and st.get("acted_qty") is None and st.settings()["enabled"] and trig


def test_leverage_change_forces_resize(ctl):
    c, st, ex, trig = ctl
    st.set("acted_exposure", 0.5)
    st.set("acted_qty", 0.01)
    h(c, "lv:s:3")
    assert st.get("acted_qty") == 0.01                         # пока не подтверждено — ничего не сброшено
    h(c, "lv:s:3:y")
    assert st.get("acted_exposure") is None and st.get("acted_qty") is None and st.settings()["leverage"] == 3.0


def test_weights_validated_server_side_and_legs_screen(ctl):
    c, st, *_ = ctl
    assert [round(w, 2) for _, w, _ in st.leg_cfg()] == [0.5, 0.0, 0.5]           # чистая БД: дефолт 0.5/0/0.5 (OI flush — вес 0)
    t0 = h(c, "lg").text
    assert "OI flush · 1h — не торгуется (вес 0%)" in t0 and "Неактивные (1)" in t0 and "Распределено <code>100%</code>" in t0
    assert "OI flush" not in h(c, "sg").text and "OI flush" not in h(c, "h").text               # неактивная нога не загромождает экраны
    assert "неактивных: 1" in h(c, "sg").text and "неактивных: 1" in h(c, "h").text             # ...но о ней напоминает одна строка
    assert {"lg:0", "lg:1", "lg:2", "lg:eq"} <= set(_all_callbacks(h(c, "lg").markup))
    cbs1 = set(_all_callbacks(h(c, "lg:1").markup))
    assert {"lg:1:t", "lg:1:w:0"} <= cbs1 and "lg:1:w:50" not in cbs1             # 50% уже не влезает
    st.set("s_legs", {"SMA cross@4h": {"weight": 0.5, "enabled": True}, "OI flush@1h": {"weight": 0.5, "enabled": True}})
    assert [w for _, w, _ in st.leg_cfg()] == [0.5, 0.5, 0.0]                      # новая нога не меняет настроенное
    s = h(c, "lg:0:w:60")                                      # сумма 1.1
    assert s.toast.startswith("Отклонено") and "уменьшите" in s.toast.lower() and "10%" in s.toast
    assert "SMA cross · 4h" in s.text                          # остаёмся на экране ноги, а не на Главной
    assert [w for _, w, _ in st.leg_cfg()] == [0.5, 0.5, 0.0]
    h(c, "lg:0:w:40")
    assert [w for _, w, _ in st.leg_cfg()] == [0.4, 0.5, 0.0]
    assert h(c, "lg:2:w:10").toast.startswith("Сохранено") and [w for _, w, _ in st.leg_cfg()] == [0.4, 0.5, 0.1]
    assert h(c, "lg:2:w:20").toast.startswith("Отклонено")                          # 0.4 + 0.5 + 0.2 > 1
    assert h(c, "lg:2:w:10").toast == "Без изменений"
    h(c, "lg:1:t")
    assert st.leg_cfg()[1][2] is False
    assert h(c, "lg:0:w:50").toast.startswith("Сохранено") and st.leg_cfg()[0][1] == 0.5
    assert h(c, "lg:1:t").toast.startswith("Отклонено") and "включить" in h(c, "lg:1:t").toast   # 0.5+0.5+0.1 > 1
    assert h(c, "lg:9").toast.startswith("Отклонено") and h(c, "lg:9:t").toast.startswith("Отклонено")
    t = h(c, "lg").text
    assert "Regime switch (CHOP)</b> · 6h" in t and "вес <code>10%</code>" in t
    assert "Regime switch (CHOP) · 6h" in h(c, "sg").text and "Regime switch (CHOP) · 6h" in h(c, "h").text
    assert "Choppiness" in h(c, "lg:2").text                    # описание на экране ноги


def test_leg_detail_screen_each_leg(ctl):
    c, st, *_ = ctl
    st.log_signals("2026-01-01T00:00:00+00:00", {"SMA cross@4h": 1.0, "Regime switch (CHOP)@6h": -1.0}, 0.0)
    assert len(st.leg_cfg()) == 3
    for i, (leg, w, en) in enumerate(st.leg_cfg()):
        s = h(c, f"lg:{i}")
        assert f"Главная › Стратегии › {leg.strategy} · {leg.tf}" in s.text and "<blockquote>" in s.text
        cbs = _all_callbacks(s.markup)
        assert f"lg:{i}" in cbs and "lg" in cbs and "h" in cbs and f"lg:{i}:t" in cbs
        assert all(kb.parse_cb(x) for x in cbs)
    t = h(c, "lg:0").text
    assert "вклад в цель <code>+0.50</code>" in t and "Можно до <code>50%</code>" in t
    assert "вклад в цель <code>-0.50</code>" in h(c, "lg:2").text
    assert "не торгуется" in h(c, "lg:1").text and "сначала уменьшите вес другой" in h(c, "lg:1").text
    assert "✅ 50%" in [b.text for b in h(c, "lg:0").markup.inline_keyboard[0]]


def test_presets_only_feasible_and_current_included(ctl):
    c, st, *_ = ctl
    # SMA 50, OI 0, Regime 50: у SMA максимум 50 -> 75/100 не предлагаются; шаг +10 скрыт
    assert [b.text for b in h(c, "lg:0").markup.inline_keyboard[0]] == ["0%", "25%", "✅ 50%"]
    assert [b.text for b in h(c, "lg:0").markup.inline_keyboard[1]] == ["−10%"]
    st.set_leg("SMA cross@4h", weight=0.33)
    row = [b.text for b in h(c, "lg:0").markup.inline_keyboard[0]]
    assert "✅ 33%" in row and "75%" not in row and "50%" in row          # текущее добавлено
    st.set_leg("Regime switch (CHOP)@6h", weight=0.0)
    row = [b.text for b in h(c, "lg:0").markup.inline_keyboard[0]]
    assert row[-1] == "100%" and "✅ 33%" in row
    assert [b.text for b in h(c, "lg:0").markup.inline_keyboard[1]] == ["−10%", "+10%"]
    assert {"lg:0:w:43", "lg:0:w:23"} <= set(_all_callbacks(h(c, "lg:0").markup))
    st.set_leg("SMA cross@4h", weight=0.05)
    assert [b.text for b in h(c, "lg:0").markup.inline_keyboard[1]] == ["+10%"]    # −10% от 5% невозможен
    tog = h(c, "lg:0").markup.inline_keyboard[2][0]
    assert tog.text == "⏸ Выключить" and tog.callback_data == "lg:0:t"
    h(c, "lg:0:t")
    assert h(c, "lg:0").markup.inline_keyboard[2][0].text == "▶️ Включить"


def test_equalize_floors_and_skips_zero_weight_legs(ctl):
    c, st, trig = ctl[0], ctl[1], ctl[3]
    st.set("s_legs", {"SMA cross@4h": {"weight": 0.2, "enabled": True}, "OI flush@1h": {"weight": 0.0, "enabled": True},
                      "Regime switch (CHOP)@6h": {"weight": 0.8, "enabled": True}})
    s = h(c, "lg:eq")
    assert s.toast == "Сохранено; пересчёт цели запущен" and trig
    assert [w for _, w, _ in st.leg_cfg()] == [0.5, 0.0, 0.5]                # порядок применения не ломает сумму; вес-0 нога не тронута
    st.set("s_legs", {"SMA cross@4h": {"weight": 0.9, "enabled": True}, "OI flush@1h": {"weight": 0.05, "enabled": True},
                      "Regime switch (CHOP)@6h": {"weight": 0.05, "enabled": True}})
    h(c, "lg:eq")
    ws = [w for _, w, _ in st.leg_cfg()]
    assert ws == [0.33, 0.33, 0.33] and sum(ws) <= 1.0
    assert h(c, "lg:eq").toast == "Без изменений"
    st.set("s_legs", {k: {"weight": 0.0, "enabled": True} for k in ("SMA cross@4h", "OI flush@1h", "Regime switch (CHOP)@6h")})
    assert h(c, "lg:eq").toast.startswith("Отклонено")


def test_old_leg_callbacks_still_work_and_show_detail(ctl):
    c, st, *_ = ctl
    s = h(c, "lg:w:0:m")
    assert st.leg_cfg()[0][1] == 0.4 and "Главная › Стратегии › SMA cross · 4h" in s.text and s.toast.startswith("Сохранено")
    h(c, "lg:w:0:p")
    assert st.leg_cfg()[0][1] == 0.5
    assert h(c, "lg:w:0:p").toast.startswith("Отклонено")                  # 0.6 + 0.5 > 1
    s = h(c, "lg:t:2")
    assert st.leg_cfg()[2][2] is False and "Regime switch (CHOP) · 6h" in s.text
    assert h(c, "lg:w:1:m").toast.startswith("Отклонено")                  # вес 0 -> −10% невозможен
    assert "вес не может быть меньше 0%" in h(c, "lg:w:1:m").toast


def test_reset_peak_two_step(ctl):
    c, st, *_ = ctl
    h(c, "pk")
    assert st.get("peak_equity") is None
    h(c, "pk:y")
    assert st.get("peak_equity") == 1000


def test_panic_and_reenable_after_kill(ctl):
    c, st, ex, _ = ctl
    run(c.ex.setup())
    st.set_setting("enabled", True)
    run(c.ex.reconcile(0.5))
    assert ex.qty != 0
    h(c, "pn")
    assert ex.qty != 0
    h(c, "pn:y")
    assert ex.qty == 0 and not st.settings()["enabled"]
    st.set("killed", True)
    st.set("peak_equity", 5000.0)
    st.set("baseline_equity", 5000.0)
    h(c, "ke")
    assert h(c, "ke:y").toast.startswith("Отклонено")
    h(c, "pk"); h(c, "pk:y")
    h(c, "ke"); h(c, "ke:y")
    assert not st.get("killed") and st.settings()["enabled"]


def test_pagination_bounds(ctl):
    c, st, *_ = ctl
    for i in range(13):                                          # 13 закрытых сделок (вход + выход)
        st.add_trade("buy", 0.01, 100000.0 + i, "сигнал", f"o{i}")
        st.add_trade("sell", 0.01, 100100.0 + i, "бар, SMA cross · 4h: лонг → вне рынка", f"c{i}")
    s0 = h(c, "tx:0")
    assert "стр. 1/3" in s0.text and _all_callbacks(s0.markup)[0] == "tx:0"          # ◀ на первой странице не уходит за 0
    s9 = h(c, "tx:9")                                           # за пределами -> последняя
    assert "стр. 3/3" in s9.text and "tx:2" in _all_callbacks(s9.markup)
    assert _all_callbacks(s9.markup)[2] == "tx:2"                                     # ▶ на последней остаётся на ней
    assert text_lines(s0.text) == kb.PAGE_SIZE and text_lines(s9.text) == 1
    assert "Событий пока нет" in h(c, "ev:5").text and "стр. 1/1" in h(c, "ev:5").text


def text_lines(t):
    return sum(1 for x in t.splitlines() if x.startswith(("✅", "❌", "➖")))     # по строке на закрытую сделку


def _mainnet(tmp_path, name="m.db"):
    st = Store(tmp_path / name)
    exe = Executor(DryRunExchange(), st, lambda *a: None, "mainnet")
    trig = []
    clk = NS(t=1000.0)
    c = Controller(st, exe, NS(trigger=lambda: trig.append(1)), "mainnet", mono=lambda: clk.t)
    c.clk = clk
    return c, st, trig


def test_mainnet_requires_confirm_for_setting_changes(tmp_path):
    c, st, _ = _mainnet(tmp_path)
    for data in ("lg:t:1", "lg:w:0:m", "lg:0:w:25", "lg:2:t", "lg:eq", "sl:a:p1", "sl:s:5",
                 "rk:dd:p5", "rk:dd:s:20"):
        if data == "lg:eq":                                   # чтобы «Поровну» что-то меняло
            st.set_leg("SMA cross@4h", weight=0.3)
        before = (st.settings(), st.leg_cfg())
        s = h(c, data)
        assert (st.settings(), st.leg_cfg()) == before and "MAINNET" not in s.text.upper(), data
        assert [b.text for b in s.markup.inline_keyboard[0]] == ["✅ Подтвердить", "❌ Отмена"], data
        assert "(действует 60 с)" in s.text and "→" in s.text, data
        h(c, data + ":y")
        assert (st.settings(), st.leg_cfg()) != before, data


def test_mainnet_leg_confirm_text_before_after(tmp_path):
    c, st, trig = _mainnet(tmp_path)
    st.log_signals("2026-01-01T00:00:00+00:00", {"SMA cross@4h": 1.0, "Regime switch (CHOP)@6h": 0.0}, 0.5)
    s = h(c, "lg:0:w:25")
    assert "SMA cross · 4h: <b>вес 50% → 25%</b>" in s.text and "Сумма весов <b>100% → 75%</b>" in s.text
    assert "Цель сейчас <b>+0.50 → +0.25</b>" in s.text
    assert s.markup.inline_keyboard[0][1].callback_data == "lg:0"          # «Отмена» -> экран ноги
    assert st.leg_cfg()[0][1] == 0.5 and not trig
    h(c, "lg:0:w:25:y")
    assert st.leg_cfg()[0][1] == 0.25 and trig
    st.log_signals("2026-01-01T01:00:00+00:00", {"SMA cross@4h": None}, None)  # неизвестный сигнал -> «пересчитается»
    assert "пересчитается" in h(c, "lg:0:w:20").text
    assert "Выровнять" in h(c, "lg:eq").text and h(c, "lg:eq").markup.inline_keyboard[0][1].callback_data == "lg"


def test_mainnet_confirm_freshness_and_rejection_for_legs(tmp_path):
    c, st, _ = _mainnet(tmp_path)
    assert h(c, "lg:0:w:25:y").toast == "Подтверждение устарело" and st.leg_cfg()[0][1] == 0.5   # без «взвода»
    h(c, "lg:0:w:25")
    c.clk.t += 61                                                             # срок вышел
    assert h(c, "lg:0:w:25:y").toast == "Подтверждение устарело" and st.leg_cfg()[0][1] == 0.5
    assert h(c, "lg:0:w:80").toast.startswith("Отклонено")                   # отказ уже на первом шаге, подтверждение не взведено
    assert not c.pending
    assert h(c, "lg:0:w:50").toast == "Без изменений" and not c.pending      # нечего подтверждать


def test_enable_blocked_in_paper_at_controller_level(tmp_path):
    st = Store(tmp_path / "pp.db", paper=True)
    exe = Executor(DryRunExchange(), st, lambda *a: None, "mainnet", paper=True)
    c = Controller(st, exe, NS(trigger=lambda: None), "mainnet")
    for d in ("tr:on", "tr:on:y", "ke", "ke:y"):
        s = h(c, d)
        assert s.toast.startswith("Отклонено") and "PAPER" not in s.toast and not st.settings()["enabled"]
    t = h(c, "h").text
    assert "⚠️ Режим без ордеров" in t and "PAPER" not in t and "Что бы сделал бот" not in t
    assert all(b.text != "ℹ️ Режим PAPER" for row in h(c, "h").markup.inline_keyboard for b in row)
    assert not any(cb in ("tr:on", "tr:off", "ke", "pp") for cb in _all_callbacks(h(c, "h").markup))   # переключателя нет
    assert kb.parse_cb("pp") is None


# ---------- панель: правка на месте, fallback ----------
class FakeBot:
    def __init__(self):
        self.sent, self.edits, self.deleted, self.pinned = [], [], [], []
        self.edit_errors: list[Exception] = []
        self.send_errors: list[Exception] = []
        self.edit_modes: list = []
        self._n = 100

    async def send_message(self, chat, text, **kw):
        if self.send_errors:
            raise self.send_errors.pop(0)
        self._n += 1
        self.sent.append((chat, text, kw))
        return NS(message_id=self._n)

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None, parse_mode=None):
        if self.edit_errors:
            raise self.edit_errors.pop(0)
        self.edits.append((message_id, text))
        self.edit_modes.append(parse_mode)

    async def delete_message(self, chat, msg): self.deleted.append(msg)
    async def pin_chat_message(self, chat, msg, disable_notification=True): self.pinned.append(msg)


def bad(msg):
    return TelegramBadRequest(method=NS(), message=msg)


def mk_screen(text, live=False):
    return Screen(text, kb.info_menu("h", "sg"), live=live, view=("sg",) if live else ())


@pytest.fixture
def pan(tmp_path):
    st = Store(tmp_path / "pn.db")
    box = NS(text="v0", n=0)
    clk = NS(t=0.0)
    sleeps = []

    async def render(parts):
        box.n += 1
        return mk_screen(box.text, live=True)

    async def sleep(d):
        sleeps.append(d)
        clk.t += d
        await asyncio.sleep(0)
        if hasattr(box, "hook"):
            box.hook(len(sleeps))

    return Panel(st, render, clock=lambda: clk.t, sleep=sleep), FakeBot(), box, clk, sleeps


def test_open_pins_and_replaces_old_panel(pan):
    p, bot, *_ = pan

    async def go():
        await p.open(bot, 1, mk_screen("a"))
        first = p.ref["msg"]
        await p.open(bot, 1, mk_screen("b"))
        assert bot.deleted == [first] and bot.pinned == [first, p.ref["msg"]] and p.ref["msg"] != first
    run(go())


def test_edit_in_place_and_fallbacks(pan):
    p, bot, *_ = pan

    async def go():
        await p.open(bot, 1, mk_screen("a"))
        mid = p.ref["msg"]
        await p.show(bot, 1, mk_screen("b"))
        assert bot.edits == [(mid, "b")] and len(bot.sent) == 1          # правка на месте, без нового сообщения
        bot.edit_errors.append(bad("Bad Request: message is not modified"))
        await p.show(bot, 1, mk_screen("b"))
        assert len(bot.sent) == 1 and p.ref["msg"] == mid                # молча
        bot.edit_errors.append(bad("Bad Request: message to edit not found"))
        await p.show(bot, 1, mk_screen("c"))
        assert len(bot.sent) == 2 and p.ref["msg"] != mid and bot.deleted == [mid]   # fallback: новая панель
    run(go())


def test_autorefresh_skips_unchanged_and_edits_on_change(pan):
    p, bot, box, clk, sleeps = pan
    box.hook = lambda n: setattr(box, "text", "v1") if n == 5 else None

    async def go():
        await p.open(bot, 1, mk_screen("v0", live=True))
        await p._task
    run(go())
    texts = [t for _, t in bot.edits]
    assert texts[0] == "v1" and texts.count("v1") == 1                    # одна правка при изменении, без спама
    assert all(s == panel_mod.PANEL_REFRESH_S for s in sleeps)
    assert "режиме ожидания" in texts[-1] and len(texts) == 2             # + одна правка «ожидание»


def test_autorefresh_stops_after_idle(pan):
    p, bot, box, clk, sleeps = pan

    async def go():
        await p.open(bot, 1, mk_screen("v0", live=True))
        await p._task
    run(go())
    assert p._task.done() and sleeps and sum(sleeps) >= panel_mod.IDLE_S
    assert sum(sleeps) < panel_mod.IDLE_S + 2 * panel_mod.PANEL_REFRESH_S
    assert "нажмите ↻" in bot.edits[-1][1]
    n = len(bot.edits)
    run(asyncio.sleep(0))
    assert len(bot.edits) == n


def test_autorefresh_cancelled_on_navigation_and_not_for_static(pan):
    p, bot, box, clk, sleeps = pan

    async def go():
        await p.open(bot, 1, mk_screen("v0", live=True))
        t = p._task
        await p.show(bot, 1, mk_screen("settings", live=False))          # ушли на не-live экран
        await asyncio.sleep(0.01)
        assert t.cancelled() or t.done()
        assert p._task is None and box.n == 0 and not sleeps            # ни одного опроса render
        await p.show(bot, 1, mk_screen("home", live=True))
        t2 = p._task
        p.cancel()                                                      # /start пересоздаёт панель — отмена
        await asyncio.sleep(0.01)
        assert t2.cancelled() or t2.done()
    run(go())


def test_autorefresh_retry_after_backoff(pan):
    p, bot, box, clk, sleeps = pan
    box.hook = lambda n: setattr(box, "text", f"v{n}")                   # текст меняется — каждая итерация шлёт правку
    bot.edit_errors.append(TelegramRetryAfter(method=NS(), message="flood", retry_after=7))

    async def go():
        await p.open(bot, 1, mk_screen("v0", live=True))
        await p._task
    run(go())
    assert sleeps[:3] == [3, 7, 10]                                        # обычный период, пауза от Telegram, затем медленно
    assert sleeps[3:9] == [10] * 6 or sleeps[3] == 10
    assert 3 in sleeps[3:]                                                 # через минуту вернулись к 3 с


def test_autorefresh_swallows_errors_and_stops_when_message_gone(pan):
    p, bot, box, clk, sleeps = pan
    bot.edit_errors += [RuntimeError("net"), bad("Bad Request: message to edit not found")]
    box.hook = lambda n: setattr(box, "text", f"v{n}")

    async def go():
        await p.open(bot, 1, mk_screen("v0", live=True))
        await p._task
    run(go())
    assert p._task.done() and not p._task.cancelled() and p._task.exception() is None


# ---------- роутер: устаревшие callback и онбординг ----------
def _router(tmp_path, paper=False):
    st = Store(tmp_path / "r.db", paper=paper)
    ex = DryRunExchange(equity=1000)
    exe = Executor(ex, st, lambda *a: None, "testnet", paper=paper)
    c = Controller(st, exe, NS(trigger=lambda: None), "testnet")
    clk = NS(t=0.0)
    p = Panel(st, c.handle, clock=lambda: clk.t, sleep=asyncio.sleep)
    r = build_router(c, p)
    return c, p, r


def _fake_q(bot, data, msg_id, chat=OWNER):
    answers = []

    async def answer(text=None, show_alert=False):
        answers.append((text, show_alert))
    return NS(data=data, message=NS(chat=NS(id=chat), message_id=msg_id), from_user=NS(id=OWNER), bot=bot, answer=answer), answers


def test_stale_and_unknown_callbacks_rerender_panel_with_toast(tmp_path):
    c, p, r = _router(tmp_path)
    bot = FakeBot()
    cb = r.callback_query.handlers[0].callback

    async def go():
        await p.open(bot, OWNER, c.home())
        mid = p.ref["msg"]
        q, ans = _fake_q(bot, "set", 999)                                   # нажата старая копия меню
        await cb(q)
        assert "старое меню" in ans[0][0] and bot.edits[-1][0] == mid and "🏠 Главная" in bot.edits[-1][1]
        q, ans = _fake_q(bot, "pp", mid)                                    # кнопка удалённого экрана «Режим PAPER»
        await cb(q)
        assert "больше не используется" in ans[0][0] and "🏠 Главная" in bot.edits[-1][1]
        q, ans = _fake_q(bot, "evil:data", mid)                             # не из белого списка
        await cb(q)
        assert "Неизвестная" in ans[0][0] and "🏠 Главная" in bot.edits[-1][1]
        q, ans = _fake_q(bot, "lv", mid)                                    # обычная навигация правит панель
        await cb(q)
        assert ans[0][0] is None and "Плечо" in bot.edits[-1][1] and len(bot.sent) == 1
        q, ans = _fake_q(bot, "np", 12345)                                  # кнопка из уведомления -> «Позиция» в панели
        await cb(q)
        assert "Позиция" in bot.edits[-1][1] and bot.edits[-1][0] == mid
        p.cancel()
    run(go())


def test_onboarding_only_first_time(tmp_path):
    c, p, r = _router(tmp_path, paper=True)
    bot = FakeBot()
    start = r.message.handlers[0].callback
    menu = r.message.handlers[1].callback
    answers = []

    async def answer(text, **kw):
        answers.append(text)
    m = NS(bot=bot, chat=NS(id=OWNER), answer=answer)

    async def go():
        await start(m)
        assert len(answers) == 1 and "Режим без ордеров" in answers[0] and "PAPER" not in answers[0] + bot.sent[0][1]
        await start(m)
        await menu(m)
        assert len(answers) == 1 and len(bot.sent) == 3                      # дальше только панель
        p.cancel()
    run(go())


# ---------- уведомления ----------
def test_notifier_format_dedup_and_buttons(tmp_path):
    st = Store(tmp_path / "n.db")
    t = NS(v=0.0)
    n = Notifier(st, OWNER, "testnet", False, clock=lambda: t.v)
    n("error", "[TESTNET] Ошибка биржи при сверке: timeout 12")
    n("error", "[TESTNET] Ошибка биржи при сверке: timeout 15")             # дубль (числа не в счёт)
    assert n.queue.qsize() == 1 and n.suppressed == 1
    t.v += 601
    n("error", "[TESTNET] Ошибка биржи при сверке: timeout 20")
    assert n.queue.qsize() == 2
    n("trade", "Покупка 0.01 BTC по 100000.0 (сигнал)")
    n("daily", "Итоги дня\nСчёт 1 000.00 $\nПозиции нет")
    items = [n.queue.get_nowait() for _ in range(4)]
    assert items[0][1] == "🚨 Ошибка биржи при сверке: timeout 12"            # без тегов среды и без заголовка-режима
    assert items[2][1] == "💱 Покупка 0.01 BTC по 100000.0 (сигнал)"
    assert items[3][1].startswith("📅 Итоги дня\n") and "TESTNET" not in "".join(i[1] for i in items)
    assert n._kwargs("trade")["reply_markup"].inline_keyboard[0][0].text == "📊 Позиция"
    assert n._kwargs("daily") == {"disable_notification": True}
    assert len(st.events(10)) == 5                                           # в журнал пишутся все


HOSTILE = '<script>alert("x")</script> & <b>unclosed'


def test_notifier_kinds_are_wellformed_and_escape_hostile_text(tmp_path):
    st = Store(tmp_path / "n2.db")
    n = Notifier(st, OWNER, "mainnet", False)
    samples = {"info": f"Бот запущен {HOSTILE}", "warn": f"warn {HOSTILE}", "error": f"KILL-SWITCH: {HOSTILE}",
               "signal": f"Сигнал {HOSTILE}", "daily": f"Итоги дня\n{HOSTILE}", "trade": f"Продажа 0.5 BTC ({HOSTILE})"}
    for level, text in samples.items():
        out = n.fmt(level, text)
        assert_html(out)
        assert "<script>" not in out and len(out) < 4096
        assert "MAINNET" not in out and "🔴" not in out
    assert "&lt;script&gt;" in n.fmt("warn", HOSTILE)
    assert len(n.fmt("error", "<" * 5000)) < 4096                           # самый плохой случай: всё экранируется 4x
    # структурное событие с враждебной причиной: причина экранируется, разметка цела
    d = {"kind": "open", "side": "long", "qty": 0.003, "price": 81550.1, "leverage": 2, "equity": 163.44, "stop": 65240.0, "reason": HOSTILE}
    out = n.fmt("trade", "x", d)
    assert_html(out) and "<script>" not in out and "&lt;script&gt;" in out


def test_notifier_parse_error_falls_back_to_plain_once(tmp_path):
    st = Store(tmp_path / "n3.db")
    n = Notifier(st, OWNER, "testnet", False)
    bot = FakeBot()
    n.attach(bot)
    bot.send_errors.append(bad("Bad Request: can't parse entities: Unsupported start tag"))
    run(n._send("trade", n.fmt("trade", "[TESTNET] Покупка 0.01 BTC по 100000.0 (a < b)")))
    assert len(bot.sent) == 1                                                # повтор ровно один
    _, text, kw = bot.sent[0]
    assert "<" not in text.replace("a < b", "") and "Покупка" in text and kw["parse_mode"] is None
    assert kw["reply_markup"].inline_keyboard[0][0].text == "📊 Позиция"     # кнопка сохранена
    bot.send_errors.append(bad("Bad Request: chat not found"))               # другие ошибки не глотаются
    with pytest.raises(TelegramBadRequest):
        run(n._send("info", n.fmt("info", "x")))


# ---------- HTML: корректность, экранирование, длина, обрезка ----------
class _Strict(HTMLParser):
    ALLOWED = {"b", "i", "code", "pre", "blockquote", "a"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED:
            self.bad.append(f"tag {tag}")
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.bad.append(f"selfclosing {tag}")

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.bad.append(f"unbalanced /{tag}")
        else:
            self.stack.pop()

    def handle_data(self, data):
        if any(ch in data for ch in "<>&"):
            self.bad.append(f"raw char in {data!r}")

    def handle_entityref(self, name):
        if name not in ("lt", "gt", "amp", "quot"):
            self.bad.append(f"entity {name}")

    def handle_charref(self, name):
        self.bad.append(f"charref {name}")


def assert_html(text):
    p = _Strict()
    p.feed(text)
    p.close()
    assert not p.bad and not p.stack, (p.bad, p.stack, text[:200])


def _rich(c, st):
    """Состояние «как в проде» + враждебные строки во всех местах, куда попадают данные."""
    st.set("equity", 160.62)
    st.set("peak_equity", 170.0)
    st.set("day_start_equity", 159.0)
    st.set("baseline_equity", 150.0)
    st.set("heartbeat", "2026-01-01T00:00:00+00:00")
    st.set("position", {"side": "short", "qty": 0.0012, "entry": 98000.0, "upnl": -1.5, "mark": 98500.0})
    st.set("target_exposure", -0.5)
    st.log_signals("2026-01-01T00:00:00+00:00", {"SMA cross@4h": 1.0, "Regime switch (CHOP)@6h": -1.0}, 0.0)
    for i in range(12):
        st.add_trade("buy" if i % 2 else "sell", 0.001, 98000.0 + i, f"{HOSTILE} {i}", f"o{i}")
        st.add_event(("info", "warn", "error", "trade", "signal", "daily")[i % 6], HOSTILE + " x" * 300)


def _all_screens(c, st):
    out = {}
    for d in ("h", "pos", "sg", "tx:0", "tx:1", "ev:0", "ev:1", "set", "lv", "sl", "rk", "lg", "lg:0", "lg:1", "lg:2",
             "lt", "np", "tr:on", "lv:s:3", "pk", "cl", "pn", "lg:0:w:25", "lg:2:t", "lg:eq", "sl:s:5",
             "rk:dd:s:20"):
        out[d] = h(c, d)
    return out


def test_all_screens_wellformed_html_and_hostile_strings_escaped(tmp_path):
    for env in ("testnet", "mainnet"):
        st = Store(tmp_path / f"{env}.db")
        exe = Executor(DryRunExchange(equity=160.62), st, lambda *a: None, env)
        c = Controller(st, exe, NS(trigger=lambda: None), env)
        _rich(c, st)
        for killed in (False, True):
            st.set("killed", killed)
            st.set("kill_reason", HOSTILE if killed else None)
            st.set("latch", killed)
            screens = _all_screens(c, st)
            if killed:
                screens["ke"] = h(c, "ke")
            for name, scr in screens.items():
                assert_html(scr.text)
                assert "<script>" not in scr.text, name
                assert len(scr.text) < 4096 or name in ("ev:0", "ev:1"), (name, len(scr.text))
                assert all(kb.parse_cb(x) for x in _all_callbacks(scr.markup)), name
            if killed:
                assert "&lt;script&gt;" in screens["h"].text and "&amp;" in screens["h"].text
                assert "Причина: &lt;script&gt;" in screens["ke"].text
            assert "&lt;script&gt;" in screens["ev:0"].text and "&lt;script&gt;" in screens["tx:0"].text
            assert "Торгуется весь баланс счёта" in screens["set"].text and "Капитал" not in screens["set"].text


def test_screen_length_worst_case_after_panel_fit(tmp_path):
    st = Store(tmp_path / "w.db")
    exe = Executor(DryRunExchange(), st, lambda *a: None, "testnet")
    c = Controller(st, exe, NS(trigger=lambda: None), "testnet")
    for i in range(10):
        st.add_event("error", "<&" * 400)                                     # 4x раздувается при экранировании
    old = kb.PAGE_SIZE
    kb.PAGE_SIZE = 10
    try:
        scr = h(c, "ev:0")
    finally:
        kb.PAGE_SIZE = old
    assert len(scr.text) > 4000                                               # без обрезки не влезло бы
    p = Panel(st, c.handle, clock=lambda: 0.0)
    bot = FakeBot()
    run(p.open(bot, 1, scr))
    sent = bot.sent[0][1]
    assert len(sent) <= 4096 and sent.endswith("…")
    assert_html(sent)
    assert bot.sent[0][2]["parse_mode"] == "HTML"


def test_fit_html_never_leaves_open_tag_or_cut_entity():
    body = "".join(f"<b>строка {i} &amp; &lt;x&gt;</b>\n<blockquote>цитата <code>{i}</code> &lt;</blockquote>\n" for i in range(80))
    assert fit_html(body, 10 ** 6) == body
    for limit in list(range(40, 260)) + [1000, 3000]:
        out = fit_html(body, limit)
        assert len(out) <= limit, limit
        assert_html(out)
    assert fit_html("плоский текст " * 50, 100).endswith("…") and len(fit_html("плоский текст " * 50, 100)) <= 100
    assert strip_html("<b>a &amp; b</b> &lt;c&gt;") == "a & b <c>"


def test_panel_parse_error_falls_back_to_plain_text_once(pan):
    p, bot, *_ = pan
    html_text = "<b>Заголовок</b> &lt;tag&gt; <i>ok</i>"

    async def go():
        bot.send_errors.append(bad("Bad Request: can't parse entities: Can't find end tag"))
        await p.open(bot, 1, mk_screen(html_text))
        assert len(bot.sent) == 1 and bot.sent[0][1] == "Заголовок <tag> ok" and bot.sent[0][2]["parse_mode"] is None
        mid = p.ref["msg"]
        await p.show(bot, 1, mk_screen(html_text + " 2"))                     # обычная правка — HTML
        assert bot.edit_modes == ["HTML"] and bot.edits[-1] == (mid, html_text + " 2")
        bot.edit_errors.append(bad("Bad Request: can't parse entities: Unsupported start tag"))
        await p.show(bot, 1, mk_screen(html_text + " 3"))
        assert bot.edits[-1] == (mid, "Заголовок <tag> ok 3") and bot.edit_modes[-1] is None
        assert len(bot.sent) == 1                                             # панель не пересоздана
    run(go())


def test_panel_uses_html_everywhere_and_idle_note_is_valid(pan):
    p, bot, box, clk, sleeps = pan

    async def go():
        await p.open(bot, 1, mk_screen("<b>v0</b>", live=True))
        await p._task
    run(go())
    assert bot.sent[0][2]["parse_mode"] == "HTML" and set(bot.edit_modes) == {"HTML"}


def test_every_keyboard_callback_passes_parse_cb(ctl):
    c, st, *_ = ctl
    st.log_signals("2026-01-01T00:00:00+00:00", {"SMA cross@4h": 1.0}, 0.5)
    menus = [kb.home_menu(e, k, pp, l) for e in (0, 1) for k in (0, 1) for pp in (0, 1) for l in (0, 1)]
    menus += [kb.legs_menu([("a", 0.0, True), ("b", 0.5, False), ("c", 1.0, True)]), kb.trade_notice_menu(),
              kb.leg_menu(9, 100, True, [0, 25, 100], True, False, True), kb.leg_menu(0, 0, False, [0], False, False, False)]
    for st_cfg in ({}, {"SMA cross@4h": {"weight": 0.0, "enabled": False}}, {"SMA cross@4h": {"weight": 1.0, "enabled": True}}):
        st.set("s_legs", st_cfg)
        menus += [h(c, d).markup for d in ("lg", "lg:0", "lg:1", "lg:2", "set", "lv", "sl", "rk", "pos", "sg")]
    for m in menus:
        for cb in _all_callbacks(m):
            assert kb.parse_cb(cb) is not None and len(cb) <= 64 and cb.isascii(), cb


def test_home_dashboard_layout(tmp_path):
    st = Store(tmp_path / "hd.db")
    exe = Executor(DryRunExchange(equity=160.62), st, lambda *a: None, "mainnet")
    c = Controller(st, exe, NS(trigger=lambda: None), "mainnet")
    st.set("equity", 160.62)
    st.set("day_start_equity", 159.0)
    st.set("peak_equity", 160.62)
    st.log_signals("2026-01-01T00:00:00+00:00", {"SMA cross@4h": 1.0, "Regime switch (CHOP)@6h": -1.0}, 0.0)
    t = h(c, "h").text
    lines = t.splitlines()
    assert lines[0] == "<b>🏠 Главная</b>" and lines[1] == tx.SEP and "MAINNET" not in t and "Режим" not in t
    assert "⬆️ SMA cross · 4h — лонг · вес 50%" in t and "⬇️ Regime switch (CHOP) · 6h — шорт · вес 50%" in t
    assert "OI flush" not in t and "неактивных: 1" in t                       # вес 0 — не показываем ногу, только счётчик
    assert "сегодня <code>+1.62 $</code>" in t and "от пика <code>0%</code>" in t
    assert t.index("Счёт") < t.index("Позиция") < t.index("Цель") < t.index("Сигналы") < t.index("Следующий бар")
    st.set("position", {"side": "long", "qty": 0.01, "entry": 100000.0, "upnl": 5.0, "mark": 100500.0})
    pt = h(c, "pos").text
    assert "⬆️ <b>ЛОНГ</b>" in pt and "Стоп <code>92 000.0</code> (<code>−8%</code>)" in pt and "сейчас <code>100 500.0</code>" in pt
    assert "Позиция <code>1 000.00</code> $ с плечом <code>1x</code>" in pt and "Своих денег <code>1 000.00</code> $" in pt
    stt = h(c, "set").text
    assert "Торгуется весь баланс счёта: <code>160.62 USDT</code>" in stt and "технический резерв 3%" in stt
    assert "Капитал" not in stt and "сумма весов <code>100%</code>" in stt


def test_onboarding_mentions_whole_balance_and_reserve():
    from tradebot.telegram.handlers import ONBOARDING
    assert "всего баланса счёта" in ONBOARDING and "технический резерв 3%" in ONBOARDING and "апитал" not in ONBOARDING


def test_reenable_uses_wallet_equity_and_resets_sizing(ctl):
    """Порт прежней логики kill-switch без «капитала»: просадка считается от пика кошелька, день обнуляется."""
    c, st, ex, trig = ctl
    st.set("killed", True)
    st.set("peak_equity", 5000.0)                               # кошелёк 1000 при пике 5000 -> просадка 80%
    h(c, "ke")
    toast = h(c, "ke:y").toast
    assert toast.startswith("Отклонено") and "просадка от пика 80.0%" in toast
    assert st.get("killed")
    st.set("peak_equity", 1300.0)                               # 23% < 25% -> можно снять
    st.set("acted_exposure", 0.5)
    st.set("acted_qty", 0.01)
    h(c, "ke")
    h(c, "ke:y")
    assert not st.get("killed") and st.settings()["enabled"] and trig
    assert st.get("acted_exposure") is None and st.get("acted_qty") is None
    assert st.get("day_start_equity") == 1000 and st.get("baseline_equity") == 1000
