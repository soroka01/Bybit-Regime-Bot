"""Аудит Telegram: обработчик callback (G1), разбор вставленного текста (G6), режим без ордеров (G7), подтверждения шагов (G8),
двойной тап (G10), время данных в подтверждении (G11), единый текст «устарело» (G13)."""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd
import pytest
from aiogram.exceptions import TelegramBadRequest

from tradebot.core.legspec import parse_leg_text, validate_leg
from tradebot.telegram import keyboards as kb
from tradebot.telegram import texts as tx
from tradebot.telegram.handlers import Controller, build_router
from tradebot.telegram.panel import Panel
from fakes.exchange import DryRunExchange
from tradebot.trading.executor import Executor
from tradebot.trading.legcheck import LegCheck
from tradebot.trading.state import Store

OWNER = 42
FRESH = (pd.Timestamp.now(tz="UTC").floor("1h") - pd.Timedelta(hours=1)).isoformat()
OK_CHECK = LegCheck(True, 480, 120, 0, 1.0, False, "окно 480 свечей воспроизводит бэктест (0 расхождений; точек: 120)", FRESH)
EMA = 'Leg("EMA cross", "1h", {"fast": 12, "slow": 26}, weight=0.5, long_only=False)'


def run(c):
    return asyncio.run(c)


class Bot:
    def __init__(self):
        self.sent, self.edits = [], []
        self._n = 100

    async def send_message(self, chat, text, **kw):
        self._n += 1
        self.sent.append(text)
        return NS(message_id=self._n)

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None, parse_mode=None):
        self.edits.append(text)

    async def delete_message(self, chat, msg): pass
    async def pin_chat_message(self, chat, msg, disable_notification=True): pass


def mk(tmp_path):
    st = Store(tmp_path / "h.db")
    exe = Executor(DryRunExchange(equity=1000), st, lambda *a: None)
    exe.poll_delay = 0
    clk, trig = NS(t=1000.0), []
    c = Controller(st, exe, NS(trigger=lambda: trig.append(1)), mono=lambda: clk.t)
    c.clk, c.trig = clk, trig
    return c, st


def h(c, data):
    return run(c.handle(kb.parse_cb(data)))


def router(c, st):
    p = Panel(st, c.handle, clock=lambda: 0.0, sleep=asyncio.sleep)
    return p, build_router(c, p)


def fake_q(bot, data, msg_id, answers, fail=False):
    async def answer(text=None, show_alert=False):
        if fail:
            raise TelegramBadRequest(method=NS(), message="Bad Request: query is too old and response timeout expired")
        answers.append((text, show_alert))
    return NS(data=data, message=NS(chat=NS(id=OWNER), message_id=msg_id), from_user=NS(id=OWNER), bot=bot, answer=answer)


# ---------------------------------------------------------------------------------------------------------- G1
def test_toast_is_truncated_to_telegram_limit(tmp_path):
    c, st = mk(tmp_path)
    p, r = router(c, st)
    cb = r.callback_query.handlers[0].callback
    bot, answers = Bot(), []

    async def long_handle(parts):
        s = c.home()
        s.toast, s.alert = "я" * 500, True
        return s
    c.handle = long_handle

    async def go():
        await p.open(bot, OWNER, c.home())
        await cb(fake_q(bot, "set", p.ref["msg"], answers))
        p.cancel()
    run(go())
    assert len(answers) == 1 and len(answers[0][0]) <= 200 and answers[0][0].endswith("…") and answers[0][1] is True


def test_failing_answer_never_blocks_panel_update(tmp_path):
    c, st = mk(tmp_path)
    p, r = router(c, st)
    cb = r.callback_query.handlers[0].callback
    bot = Bot()

    async def go():
        await p.open(bot, OWNER, c.home())
        n = len(bot.edits)
        await cb(fake_q(bot, "set", p.ref["msg"], [], fail=True))            # запрос устарел — answer падает
        assert len(bot.edits) == n + 1 and "Настройки" in bot.edits[-1]      # экран всё равно показан
        await cb(fake_q(bot, "evil:data", p.ref["msg"], [], fail=True))      # и на ветке «неизвестная кнопка»
        assert "🏠 Главная" in bot.edits[-1]
        p.cancel()
    run(go())


def test_slow_confirmed_action_answers_early_then_works_then_shows(tmp_path):
    c, st = mk(tmp_path)
    p, r = router(c, st)
    cb = r.callback_query.handlers[0].callback
    bot, answers, order = Bot(), [], []
    real_handle = c.handle

    async def spy_handle(parts):
        order.append(("handle", list(answers)))                               # на момент работы ответ уже ушёл
        return await real_handle(parts)
    c.handle = spy_handle

    async def go():
        await p.open(bot, OWNER, c.home())
        mid = p.ref["msg"]
        c._arm("pk")
        await cb(fake_q(bot, "pk:y", mid, answers))
        assert st.get("peak_equity") == 1000                                   # действие выполнено
        assert answers == [("Выполняю…", False)]                              # ровно один (ранний) ответ
        assert order[-1] == ("handle", [("Выполняю…", False)])
        assert "Настройки" in bot.edits[-1]
        p.cancel()
    run(go())


def test_unarmed_or_fast_callbacks_are_not_answered_early(tmp_path):
    c, st = mk(tmp_path)
    p, r = router(c, st)
    cb = r.callback_query.handlers[0].callback
    bot, answers = Bot(), []

    async def go():
        await p.open(bot, OWNER, c.home())
        await cb(fake_q(bot, "pk:y", p.ref["msg"], answers))                  # не взведено -> «устарело», без «Выполняю…»
        assert answers == [("Подтверждение устарело", False)] and st.get("peak_equity") is None
        await cb(fake_q(bot, "set", p.ref["msg"], answers))                   # обычная навигация: ответ без текста
        assert answers[-1] == (None, False)
        p.cancel()
    run(go())


def test_error_after_early_answer_is_shown_in_panel(tmp_path):
    c, st = mk(tmp_path)
    p, r = router(c, st)
    cb = r.callback_query.handlers[0].callback
    bot, answers = Bot(), []

    async def boom(parts):
        raise RuntimeError("биржа упала")
    c.handle = boom

    async def go():
        await p.open(bot, OWNER, c.home())
        c._arm("cl")
        await cb(fake_q(bot, "cl:y", p.ref["msg"], answers))
        assert answers == [("Выполняю…", False)]
        assert "Ошибка:" in bot.edits[-1] and "RuntimeError" in bot.edits[-1]
        answers.clear()
        await cb(fake_q(bot, "set", p.ref["msg"], answers))                    # без раннего ответа ошибка идёт всплывающим окном
        assert answers == [("Ошибка: RuntimeError", True)]
        p.cancel()
    run(go())


# ---------------------------------------------------------------------------------------------------------- G6
HUGE = "9" * 400


@pytest.mark.parametrize("text", [
    f'Leg("SMA cross", "4h", {{}}, weight={HUGE})',
    f'Leg("SMA cross", "4h", {{"fast": {HUGE}}}, weight=0.5)',
    f'Leg("SMA cross", "4h", {{"fast": -{HUGE}}})',
    'Leg("SMA cross", "4h", {"fast": ' + "[" * 3000 + "]" * 3000 + "})",
    "Leg(" * 300 + ")" * 300,
    'Leg("SMA cross", "4h", ' + "(" * 500 + "1" + ")" * 500 + ")",
])
def test_legspec_hostile_numbers_and_nesting_give_value_error(text):
    try:
        drafts = parse_leg_text(text)
    except ValueError:
        return
    for d in drafts:                                                        # разобралось — проверка должна быть вежливой
        with pytest.raises(ValueError):
            validate_leg(d.leg)


def _paste(tmp_path, text, boom=None):
    c, st = mk(tmp_path)
    p, r = router(c, st)
    leg_text = r.message.handlers[2].callback
    bot = Bot()
    if boom is not None:
        c.propose_from_text = lambda t: (_ for _ in ()).throw(boom)

    async def delete():
        pass
    m = NS(text=text, bot=bot, chat=NS(id=OWNER), delete=delete)

    async def go():
        await p.open(bot, OWNER, c.home())
        await leg_text(m)
        p.cancel()
    run(go())
    return bot


@pytest.mark.parametrize("text", [f'Leg("SMA cross", "4h", {{}}, weight={HUGE})', f'Leg("SMA cross", "4h", {{"fast": {HUGE}}})',
                                  "Leg(" * 300 + ")" * 300, 'Leg("SMA cross", "4h", {"fast": ' + "[" * 3000 + "]" * 3000 + "})"])
def test_paste_handler_replies_friendly_on_hostile_text(tmp_path, text):
    bot = _paste(tmp_path, text)
    assert "Новая стратегия" in bot.edits[-1] and "⚠️" in bot.edits[-1]


@pytest.mark.parametrize("boom", [OverflowError("int too large"), RecursionError("deep"), RuntimeError("что угодно")])
def test_paste_handler_survives_any_unexpected_exception(tmp_path, boom):
    bot = _paste(tmp_path, 'Leg("SMA cross", "4h", {})', boom=boom)
    assert "не удалось разобрать" in bot.edits[-1]


# ---------------------------------------------------------------------------------------------------------- G8
@pytest.mark.parametrize("cb_data,key,val", [("sl:a:p1", "stop_pct", 0.30), ("sl:a:m1", "stop_pct", 0.01),
                                             ("rk:dd:p5", "kill_dd", 0.50), ("rk:dd:m5", "kill_dd", 0.05),
                                             ("rk:da:p1", "kill_daily", 0.30), ("rk:da:m1", "kill_daily", 0.02)])
def test_relative_step_out_of_range_is_rejected_politely_before_confirm(tmp_path, cb_data, key, val):
    c, st = mk(tmp_path)
    st.set_setting(key, val)
    s = h(c, cb_data)
    assert s.toast.startswith("Отклонено:") and "должен быть от" in s.toast and "вне диапазона" not in s.toast
    assert not c.pending and st.settings()[key] == val


def test_stop_step_confirm_is_bound_to_absolute_value_at_arm_time(tmp_path):
    c, st = mk(tmp_path)
    s = h(c, "sl:a:p1")
    assert "8% → 9%" in s.text
    st.set_setting("stop_pct", 0.12)                                         # пока лежит подтверждение, настройка изменилась
    s = h(c, "sl:a:p1:y")
    assert st.settings()["stop_pct"] == 0.09 and s.toast == "Сохранено"      # применено именно показанное значение
    assert h(c, "sl:a:p1:y").toast == "Уже выполнено" and st.settings()["stop_pct"] == 0.09   # двойной тап ничего не меняет


def test_risk_step_confirm_is_bound_and_rearm_rebinds(tmp_path):
    c, st = mk(tmp_path)
    assert "25% → 30%" in h(c, "rk:dd:p5").text
    st.set_setting("kill_dd", 0.40)
    assert "40% → 45%" in h(c, "rk:dd:p5").text                              # новое нажатие — новая привязка
    st.set_setting("kill_dd", 0.10)
    h(c, "rk:dd:p5:y")
    assert st.settings()["kill_dd"] == 0.45


def test_bound_confirm_still_expires_after_60s_and_whitelist_is_strict(tmp_path):
    c, st = mk(tmp_path)
    h(c, "sl:a:p1")
    c.clk.t += 61
    assert h(c, "sl:a:p1:y").toast == "Подтверждение устарело" and st.settings()["stop_pct"] == 0.08 and not c.bound
    for bad in ("sl:a:p1:y:9", "sl:a:p2", "sl:a:p1:9", "sl:s:9:y:1", "rk:dd:p7", "sl:a:p10:y"):
        assert kb.parse_cb(bad) is None, bad
    for ok in ("sl:a:p1", "sl:a:m1:y", "rk:dd:p5:y", "rk:da:m1"):
        assert kb.parse_cb(ok) is not None, ok


# --------------------------------------------------------------------------------------------------------- G10/G13
def test_double_tap_on_confirm_and_leg_removal(tmp_path):
    c, st = mk(tmp_path)
    n0 = len(st.leg_cfg())
    h(c, "lg:0:rm")
    assert h(c, "lg:0:rm:y").toast.startswith("Удалена") and len(st.leg_cfg()) == n0 - 1
    assert h(c, "lg:0:rm:y").toast == "Уже выполнено" and len(st.leg_cfg()) == n0 - 1        # второй тап не удаляет следующую
    c.clk.t += 6
    assert h(c, "lg:0:rm:y").toast == "Подтверждение устарело"
    h(c, "pk")
    h(c, "pk:y")
    h(c, "pk")                                                                # повторное взведение стирает «выполнено»
    assert h(c, "pk:y").toast != "Уже выполнено"


def test_stale_confirm_literal_is_defined_once():
    src = Path(__file__).resolve().parents[1] / "src" / "tradebot" / "telegram"
    hits = [f.name for f in src.glob("*.py") if '"Подтверждение устарело"' in f.read_text(encoding="utf-8")]
    assert hits == ["common.py"]


# ---------------------------------------------------------------------------------------------------------- G11
def test_leg_add_confirm_states_data_time_of_cached_check(tmp_path, monkeypatch):
    c, st = mk(tmp_path)
    monkeypatch.setattr("tradebot.telegram.legflow.check_leg", lambda leg: OK_CHECK)
    st.set_leg("SMA cross@4h", weight=0.0)
    st.set_leg("Regime switch (CHOP)@6h", weight=0.5)
    ids, errors = c.propose_from_text(EMA)
    assert ids and not errors
    s = h(c, f"ad:{ids[0]}:w:25")
    expected = tx.local(pd.Timestamp(FRESH).to_pydatetime() + pd.Timedelta(hours=1), c.tz, c.clock())
    assert "Подтвердить" in s.markup.inline_keyboard[0][0].text
    assert f"по данным на {expected}" in s.text and re.search(r"\d\d:\d\d", expected)


def test_dead_code_removed():
    from tradebot.telegram import handlers, legflow, texts
    assert not hasattr(handlers, "DASHBOARD_URL") and not hasattr(handlers, "LEVEL_ICON") and not hasattr(texts, "pct")
    assert not hasattr(legflow, "p0") and not hasattr(Controller, "settings_screen_toast")
