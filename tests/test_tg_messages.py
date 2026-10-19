"""Уведомления и экраны Telegram после рефакторинга текстов: построители сообщений (telegram/messages.py), реализованный P/L
(closed-pnl биржи и оценка), стоп-аут, kill/panic, итоги суток, запуск/остановка, слияние сигналов, отсутствие шума и «среды»."""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import httpx
import pandas as pd
import pytest

from tradebot.telegram import messages as M
from tradebot.telegram import texts as tx
from tradebot.telegram.handlers import Controller
from tradebot.telegram import keyboards as kb
from tradebot.telegram.notify import Notifier
from tradebot.trading import pnl
from tradebot.trading.exchange import BybitClient, DryRunExchange, ReadOnlyClient
from tradebot.trading.executor import Executor
from tradebot.trading.market_feed import FeedData
from tradebot.trading.runner import MANUAL_RECALC, Runner
from tradebot.trading.state import Store

BAR = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
ENV_WORDS = re.compile(r"mainnet|testnet|paper", re.I)
HOSTILE = '<script>alert("x")</script> & <b>unclosed'


def run(c):
    return asyncio.run(c)


class Rec:
    """Получатель уведомлений, умеющий data: хранит (level, text, data)."""

    def __init__(self):
        self.items = []

    def __call__(self, level, text, data=None):
        self.items.append((level, text, data))

    def kinds(self):
        return [d["kind"] if d else None for _, _, d in self.items]

    def data(self, kind):
        return [d for _, _, d in self.items if d and d["kind"] == kind]

    def html(self, kind=None):
        return [M.build(d) for _, _, d in self.items if d and (kind is None or d["kind"] == kind)]

    def texts(self):
        return [t for _, t, _ in self.items]

    def has(self, s):
        return any(s in t for t in self.texts())


class Sink:
    """Старый получатель (level, text) — должен продолжать работать."""

    def __init__(self):
        self.msgs = []

    def __call__(self, level, text):
        self.msgs.append((level, text))

    def has(self, s):
        return any(s in t for _, t in self.msgs)


def mk(tmp_path, name="b.db", equity=163.44, mark=81550.1, lev=2, stop_pct=0.2, notify=None, paper=False):
    st = Store(tmp_path / name, paper=paper)
    st.set_setting("enabled", not paper)
    st.set_setting("leverage", lev)
    st.set_setting("stop_pct", stop_pct)
    ex = DryRunExchange(equity=equity, mark=mark)
    rec = notify if notify is not None else Rec()
    exe = Executor(ex, st, rec, "mainnet", paper=paper)
    exe.poll_delay = 0
    exe.pnl_delays = (0.0, 0.0, 0.0)
    run(exe.setup())
    return st, ex, exe, rec


# ---------- числа и слова ----------
def test_formatting_helpers():
    assert M.usd(244.654) == "244.65 $" and M.usd(2.31, 2, True) == "+2.31 $" and M.usd(-32.1, 2, True) == "−32.10 $"
    assert M.usd(-0.001, 2, True) == "0.00 $" and M.usd(None) == "—" and M.usd(81550.1, 1) == "81 550.1 $"
    assert M.pct(0.014) == "+1.4%" and M.pct(-0.196) == "−19.6%" and M.pct(-0.2) == "−20%" and M.pct(0) == "0%"
    assert M.share(0.748) == "75%" and M.share(0.052) == "5.2%"
    assert M.dur(45) == "45 с" and M.dur(754) == "12 мин" and M.dur(8100) == "2 ч 15 мин" and M.dur(97200) == "1 д 3 ч"
    assert M.target_words(0) == "вне рынка" and M.target_words(1.0) == "ЛОНГ 100%" and M.target_words(-0.5) == "ШОРТ 50%"
    assert M.leg_label("Regime switch (CHOP)@6h") == "Regime switch (CHOP) · 6h"
    assert M.sig_change("Regime switch (CHOP)@6h", -1.0, 0.0) == "Regime switch (CHOP) · 6h: шорт → вне рынка"
    assert M.pnl_icon(1.0) == "✅" and M.pnl_icon(-1.0) == "❌" and M.pnl_icon(0.0) == "➖" and M.pnl_icon(None) == "➖"


def test_reason_human_and_stored_reason():
    ch = [{"label": "SMA cross@4h", "prev": 1.0, "new": 0.0}]
    stored = M.stored_reason("бар", ch)
    assert stored == "бар, SMA cross · 4h: лонг → вне рынка"
    assert stored.split(",")[0] == "бар"                                   # группировка веб-статистики идёт по тексту до запятой
    assert M.reason_human(stored) == "SMA cross · 4h: лонг → вне рынка"
    assert M.reason_human("бар") == "плановая сверка по закрытию бара"
    assert M.reason_human("старт/перезапуск, с опозданием 12 мин") == "сверка при запуске бота"
    assert M.reason_human("внеплановый пересчёт") == "пересчёт после смены настроек"
    assert M.reason_human("паника") == "Паника" and M.reason_human("") == "—"
    assert M.stored_reason("бар", []) == "бар"


# ---------- OPEN ----------
@pytest.mark.parametrize("lev", [1, 2, 3])
@pytest.mark.parametrize("side", ["long", "short"])
def test_open_msg_leverage_and_own_money(side, lev):
    d = {"kind": "open", "side": side, "qty": 0.003, "price": 81550.1, "leverage": lev, "equity": 163.44,
         "stop": 65240.0 if side == "long" else 97860.1, "reason": "Regime switch (CHOP) · 6h: вне рынка → лонг"}
    t = tx.strip_html(M.open_msg(d))
    notional = 0.003 * 81550.1
    assert t.splitlines()[0] == ("⬆️ Открыт ЛОНГ" if side == "long" else "⬇️ Открыт ШОРТ")
    assert f"Вход 81 550.1 · 0.003 BTC" in t
    assert f"Позиция {tx.n(notional)} $ с плечом {lev}x" in t
    assert f"Своих денег {tx.n(notional / lev)} $ ({round(notional / lev / 163.44 * 100)}% счёта)" in t
    assert "Причина: Regime switch (CHOP) · 6h: вне рынка → лонг" in t and t.splitlines()[-1] == "Счёт 163.44 $"
    assert ("Стоп 65 240.0 (−20%)" if side == "long" else "Стоп 97 860.1 (+20%)") in t
    assert "🔴" not in t and "🟢" not in t
    assert_html(M.open_msg(d))


def test_open_msg_matches_owner_example():
    d = {"kind": "open", "side": "long", "qty": 0.003, "price": 81550.1, "leverage": 2, "equity": 163.44, "stop": 65240.0,
         "reason": "Regime switch (6h) → лонг"}
    assert tx.strip_html(M.open_msg(d)).splitlines() == [
        "⬆️ Открыт ЛОНГ", "Вход 81 550.1 · 0.003 BTC", "Позиция 244.65 $ с плечом 2x", "Своих денег 122.33 $ (75% счёта)",
        "Стоп 65 240.0 (−20%)", "Причина: Regime switch (6h) → лонг", "Счёт 163.44 $"]


def test_open_msg_without_confirmed_stop_warns():
    d = {"kind": "open", "side": "short", "qty": 0.01, "price": 100000.0, "leverage": 1, "equity": 1000.0, "stop": None, "reason": "бар"}
    assert "Стоп на бирже ещё не подтверждён" in M.open_msg(d)


class _Strict:
    pass


def assert_html(text):
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.stack, self.bad = [], []

        def handle_starttag(self, tag, attrs):
            if tag not in {"b", "i", "code"}:
                self.bad.append(tag)
            self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack or self.stack.pop() != tag:
                self.bad.append("/" + tag)

        def handle_data(self, data):
            if any(ch in data for ch in "<>&"):
                self.bad.append(f"raw {data!r}")

    p = P()
    p.feed(text)
    p.close()
    assert not p.bad and not p.stack, (p.bad, p.stack, text)


def test_executor_open_event_has_all_facts(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    assert run(exe.reconcile(1.0, BAR, "бар, Regime switch (CHOP) · 6h: вне рынка → лонг")) == "выполнено"
    (d,) = rec.data("open")
    assert d["side"] == "long" and d["qty"] == pytest.approx(0.003) and d["price"] == 81550.1 and d["leverage"] == 2
    assert d["equity"] == pytest.approx(163.44) and d["stop"] == pytest.approx(65240.0)
    t = tx.strip_html(M.build(d))
    assert "Своих денег 122.33 $ (75% счёта)" in t and "Причина: Regime switch (CHOP) · 6h: вне рынка → лонг" in t
    assert rec.kinds() == ["open"]                     # одно сообщение, никаких «Покупка …» и служебных строк
    assert st.get("pos_entry_equity") == pytest.approx(163.44) and st.get("pos_entry_eq_est") is False
    assert st.get("pos_entry_lev") == 2 and st.get("pos_entry_ts")


def test_plain_sink_still_works_and_gets_plain_text(tmp_path):
    sink = Sink()
    st, ex, exe, _ = mk(tmp_path, notify=sink)
    run(exe.reconcile(1.0, BAR, "бар"))
    assert sink.msgs and sink.msgs[0][0] == "trade" and sink.msgs[0][1].startswith("⬆️ Открыт ЛОНГ")
    assert "<b>" not in sink.msgs[0][1] and "\n" not in sink.msgs[0][1]
    assert M.accepts_data(Rec()) and M.accepts_data(lambda *a: None) and not M.accepts_data(sink)
    n = Notifier(Store(tmp_path / "n.db"), 1)
    assert M.accepts_data(n)


# ---------- INCREASE / REDUCE / CLOSE ----------
def test_increase_and_reduce_notices(tmp_path):
    st, ex, exe, rec = mk(tmp_path, equity=1000, mark=100000.0, lev=2)
    run(exe.reconcile(0.5, BAR, "бар, A: вне рынка → лонг"))
    rec.items.clear()
    ex.mark = 100000.0
    run(exe.reconcile(1.0, BAR, "бар, B: вне рынка → лонг"))                  # цель 0.5 -> 1.0: добор
    (inc,) = rec.data("increase")
    assert inc["side"] == "long" and inc["delta"] == pytest.approx(0.010) and inc["total_qty"] == pytest.approx(0.019)
    t = tx.strip_html(M.increase_msg(inc))
    assert t.splitlines()[0] == "⬆️ Увеличена позиция ЛОНГ" and "Добавлено +0.01 BTC" in t and "Теперь 0.019 BTC" in t
    rec.items.clear()
    ex.mark = 101000.0
    run(exe.reconcile(0.5, BAR, "бар, B: лонг → вне рынка"))               # сокращение
    (red,) = rec.data("reduce")
    assert red["side"] == "long" and red["delta"] > 0 and red["pnl"] is not None and red["exact"]
    t = tx.strip_html(M.reduce_msg(red))
    assert t.splitlines()[0] == "⬆️ Уменьшена позиция ЛОНГ" and "Убрано −" in t and "Осталось" in t
    assert "✅ Зафиксировано +" in t and "Причина: B: лонг → вне рынка" in t
    assert not rec.data("close")                                          # позиция ещё открыта


def test_close_win_with_fees_and_funding(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR, "бар, Regime switch (CHOP) · 6h: вне рынка → лонг"))
    rec.items.clear()
    ex.mark = 82300.0
    orig = ex.closed_pnl

    async def with_funding(oid, since=0):
        r = await orig(oid, since)
        if r:
            r["closed_pnl"] -= 0.10                     # биржа списала фандинг 0.10 $
        return r
    ex.closed_pnl = with_funding
    run(exe.reconcile(0.0, BAR, "бар, Regime switch (CHOP) · 6h: лонг → вне рынка"))
    (c,) = rec.data("close")
    gross = 0.003 * (82300.0 - 81550.1)
    fees = 0.003 * 81550.1 * 0.001 + 0.003 * 82300.0 * 0.001
    assert c["exact"] and c["pnl"] == pytest.approx(gross - fees - 0.10, abs=1e-6)
    assert c["fees"] == pytest.approx(fees, abs=1e-6) and c["funding"] == pytest.approx(-0.10, abs=1e-6)
    assert c["pct"] == pytest.approx(c["pnl"] / 163.44) and c["pct_est"] is False
    assert c["roe"] == pytest.approx(c["pnl"] / (0.003 * 81550.1 / 2), rel=1e-6)
    t = tx.strip_html(M.close_msg(c)).splitlines()
    assert t[0] == f"✅ Закрыт ЛОНГ: +{c['pnl']:.2f} $"
    assert "от счёта" in t[1] and "на свои деньги" in t[1]
    assert t[2] == "Вход 81 550.1 → выход 82 300.0 · 0.003 BTC"
    assert any(x.startswith("Комиссии") and "фандинг −0.10 $" in x for x in t)
    assert "Причина: Regime switch (CHOP) · 6h: лонг → вне рынка" in t and t[-1].startswith("Счёт ")
    assert "≈" not in "\n".join(t)
    (row,) = st.roundtrip_rows()
    assert row["pnl"] == pytest.approx(c["pnl"]) and row["approx"] == 0 and row["side"] == "long" and row["equity_open"] == pytest.approx(163.44)
    assert st.get("pos_entry_equity") is None and st.get("pos_entry_ts") is None            # круг закрыт


def test_close_loss_shows_cross_and_percent_of_entry_equity(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(-1.0, BAR, "бар"))                                   # шорт
    rec.items.clear()
    ex.mark = 83000.0                                                       # цена выросла: шорт в минусе
    run(exe.reconcile(0.0, BAR, "бар"))
    (c,) = rec.data("close")
    assert c["side"] == "short" and c["pnl"] < 0
    t = tx.strip_html(M.close_msg(c)).splitlines()
    assert t[0].startswith("❌ Закрыт ШОРТ: −") and "% от счёта" in t[1] and "−" in t[1]
    assert not re.search(r"[🟢🔴]", "\n".join(t))


def test_flip_gives_close_then_open_in_order(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR, "бар"))
    rec.items.clear()
    ex.mark = 82300.0
    run(exe.reconcile(-1.0, BAR, "бар, Regime switch (CHOP) · 6h: лонг → шорт"))
    assert rec.kinds() == ["close", "open"]                                # сначала итог закрытия, затем вход
    c, o = rec.data("close")[0], rec.data("open")[0]
    assert c["side"] == "long" and o["side"] == "short" and o["price"] == 82300.0
    assert o["equity"] == pytest.approx(163.44 + c["pnl"], abs=0.2)        # счёт на входе = после результата закрытия
    for h in rec.html():
        assert_html(h)
    assert st.get("pos_entry_equity") == pytest.approx(o["equity"]) and len(st.roundtrip_rows()) == 1


# ---------- closed-pnl: задержка, запасной вариант ----------
def test_closed_pnl_lag_is_retried_then_exact(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    rec.items.clear()
    ex.closed_pnl_lag = 2                                                  # запись появляется с третьей попытки
    ex.mark = 82300.0
    run(exe.reconcile(0.0, BAR))
    (c,) = rec.data("close")
    assert c["exact"] and ex.closed_pnl_calls >= 3


def test_closed_pnl_missing_falls_back_to_marked_estimate(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    ex.mark = 82300.0
    run(exe.reconcile(0.0, BAR))
    exact = rec.data("close")[0]["pnl"]
    st2, ex2, exe2, rec2 = mk(tmp_path, "b2.db")
    run(exe2.reconcile(1.0, BAR))
    ex2.closed_pnl_off = True                                              # биржа записи не отдаёт
    ex2.mark = 82300.0
    run(exe2.reconcile(0.0, BAR))
    (c,) = rec2.data("close")
    assert c["exact"] is False and c["funding"] is None
    assert c["pnl"] == pytest.approx(exact, abs=1e-6)                      # оценка: средняя цена минус 0.1% taker с обеих сторон
    t = tx.strip_html(M.close_msg(c))
    assert t.splitlines()[0].startswith(f"✅ Закрыт ЛОНГ: ≈ +") and "оценка по сделкам" in t
    assert st2.roundtrip_rows()[0]["approx"] == 1


def test_closed_pnl_errors_never_break_trading(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    ex.closed_pnl_error = RuntimeError("boom")
    ex.mark = 81000.0
    assert run(exe.reconcile(0.0, BAR)) == "выполнено" and ex.qty == 0
    (c,) = rec.data("close")
    assert c["exact"] is False and c["pnl"] < 0 and tx.strip_html(M.close_msg(c)).startswith("❌ Закрыт ЛОНГ: ≈ −")


def test_pnl_lookup_budget_is_bounded(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    ex.closed_pnl_off = True
    exe.pnl_delays = (0.0, 1.0, 1.5, 2.0)
    exe.pnl_budget_s = 0.3                                                 # бюджет меньше первой паузы: ждать нельзя
    import time
    t0 = time.monotonic()
    run(exe.reconcile(0.0, BAR))
    assert time.monotonic() - t0 < 1.0 and ex.closed_pnl_calls == 1


# ---------- эквити на входе: хранение и запасные варианты ----------
def test_entry_equity_fallback_from_equity_curve(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    ex.external_trade(0.003)                                               # позиция «от старой версии»: бот о входе не знает
    st.add_trade("buy", 0.003, 81550.1, "сигнал", "old1")
    first = st.all_trades()[0]["ts"]
    before = (datetime.fromisoformat(first) - timedelta(minutes=5)).isoformat(timespec="seconds")
    st.add_equity(170.0, before)                                           # точка кривой ДО первого входного исполнения
    st.add_equity(160.0, (datetime.fromisoformat(first) + timedelta(minutes=5)).isoformat(timespec="seconds"))
    run(exe.refresh_cache())
    assert st.get("pos_entry_equity") == 170.0 and st.get("pos_entry_eq_est") is True and st.get("pos_entry_ts") == first
    ex.mark = 82300.0
    run(exe.reconcile(0.0, BAR, "бар"))
    (c,) = rec.data("close")
    assert c["pct_est"] is True and c["pct"] == pytest.approx(c["pnl"] / 170.0)
    assert "(оценка)" in tx.strip_html(M.close_msg(c))


def test_entry_equity_fallback_equity_minus_unrealised(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    ex.external_trade(0.003)
    ex.mark = 82300.0
    st.set("equity", 165.0)
    st.set("position", {"side": "long", "qty": 0.003, "entry": 81550.1, "upnl": 2.25, "mark": 82300.0})
    run(exe.refresh_cache())
    assert st.get("pos_entry_equity") == pytest.approx(165.0 - ex.qty * (ex.mark - ex.entry))
    assert st.get("pos_entry_eq_est") is True


def test_entry_equity_persisted_value_wins_over_fallback(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    st.set("equity", 999.0)
    run(exe.refresh_cache())                                               # не перезаписывает записанное при входе
    assert st.get("pos_entry_equity") == pytest.approx(163.44) and st.get("pos_entry_eq_est") is False


# ---------- стоп-аут, kill, panic ----------
def test_stopout_with_record_reports_realised_pnl(tmp_path):
    st, ex, exe, rec = mk(tmp_path, stop_pct=0.2)
    run(exe.reconcile(1.0, BAR))
    run(exe.refresh_cache())
    rec.items.clear()
    ex.stop_out()                                                          # биржа закрыла по стопу -20%
    assert run(exe.reconcile(1.0, BAR)).startswith("защёлка")
    (d,) = rec.data("stopout")
    assert d["pnl"] is not None and d["pnl"] < -30 and d["exit_kind"] == "stop" and d["latched"] and d["exit"] == pytest.approx(65240.0)
    assert d["pct"] == pytest.approx(d["pnl"] / 163.44)
    t = tx.strip_html(M.stopout_msg(d))
    assert t.splitlines()[0].startswith("🛑 Сработал стоп-лосс: −4") and "% счёта)" in t.splitlines()[0]
    assert "Заново не вхожу, пока сигнал не изменится" in t and "⬆️ ЛОНГ 0.003 BTC" in t
    # событие в журнале сохраняет подстроку, по которой веб-статистика находит стоп-ауты
    assert any("Позиция исчезла (стоп/ликвидация)" in x for x in rec.texts())
    (row,) = st.roundtrip_rows()
    assert row["kind"] == "stop" and row["pnl"] == pytest.approx(d["pnl"]) and st.get("pos_entry_ts") is None


def test_stopout_without_record_then_followup(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    run(exe.refresh_cache())
    rec.items.clear()
    ex.closed_pnl_off = True
    ex.stop_out()
    run(exe.reconcile(1.0, BAR))
    (d,) = rec.data("stopout")
    assert d["pnl"] is None
    t = tx.strip_html(M.stopout_msg(d))
    assert "Сработал стоп-лосс — сумма уточняется" in t and st.get("pending_stopouts")
    assert st.roundtrip_rows()[0]["pnl"] is None
    # следующий тик мониторинга: запись появилась -> уточнение
    ex.closed_pnl_off = False
    st.set_setting("enabled", True)
    run(exe.check_stopout())
    (f,) = rec.data("stopout")[1:]
    assert f["followup"] and f["pnl"] < -30 and not st.get("pending_stopouts")
    assert tx.strip_html(M.stopout_msg(f)).startswith("🛑 Уточнение: сработал стоп-лосс: −")
    assert st.roundtrip_rows()[0]["pnl"] == pytest.approx(f["pnl"])


def test_stopout_when_target_changed_has_no_latch_text(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    run(exe.refresh_cache())
    rec.items.clear()
    ex.stop_out()
    run(exe.reconcile(0.0, BAR))
    (d,) = rec.data("stopout")
    assert not d["latched"] and "Цель уже изменилась" in M.stopout_msg(d) and not st.get("latch")


def test_stopout_external_close_is_not_called_a_stop(tmp_path):
    rec_ = {"exec_type": "Trade", "avg_exit": 90000.0}
    assert pnl.exit_kind(rec_, 65240.0, "long") == "external"
    assert pnl.exit_kind({"exec_type": "BustTrade", "avg_exit": 1.0}, 65240.0, "long") == "liquidation"
    assert pnl.exit_kind({"exec_type": "Trade", "avg_exit": 65200.0}, 65240.0, "long") == "stop"
    assert pnl.exit_kind(None, None, "long") == "stop"
    d = {"kind": "stopout", "side": "long", "qty": 0.003, "entry": 81550.1, "exit": 90000.0, "pnl": 25.0, "exit_kind": "external",
         "latched": True, "pct": 0.15}
    assert "Позиция закрыта на бирже (не ботом): +25.00 $" in tx.strip_html(M.stopout_msg(d))
    assert "Ликвидация" in M.stopout_msg({**d, "exit_kind": "liquidation", "pnl": -100.0})


def test_kill_switch_message_has_pnl_and_single_message(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    rec.items.clear()
    ex.mark = 80000.0
    run(exe.kill("просадка от пика 25.1% >= 25%"))
    assert rec.kinds() == ["kill"]                                         # закрытие входит в одно сообщение kill
    (d,) = rec.data("kill")
    assert d["closed"] and d["pnl"] < 0 and d["pct"] == pytest.approx(d["pnl"] / 163.44) and d["equity"]
    t = tx.strip_html(M.kill_msg(d))
    assert t.startswith("🛑 Kill-switch: просадка от пика 25.1% >= 25%") and "Позиция закрыта: ❌ −" in t
    assert "Торговля выключена" in t and st.get("killed")


def test_panic_message_has_pnl(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    rec.items.clear()
    ex.mark = 82300.0
    assert run(exe.panic()) == "закрыто"
    assert rec.kinds() == ["panic"]
    t = tx.strip_html(M.panic_msg(rec.data("panic")[0]))
    assert t.startswith("🛑 Паника: торговля выключена") and "Позиция закрыта: ✅ +" in t
    assert not st.settings()["enabled"]


def test_manual_close_message_and_flat_kill(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    rec.items.clear()
    run(exe.close_all("закрытие из Telegram"))
    (c,) = rec.data("close")
    assert "Причина: Закрытие из Telegram" in tx.strip_html(M.close_msg(c))
    rec.items.clear()
    run(exe.kill("тест"))
    assert "Открытой позиции не было" in tx.strip_html(M.kill_msg(rec.data("kill")[0]))


# ---------- итоги суток ----------
def test_daily_msg_with_and_without_trades():
    base = {"kind": "daily", "equity": 165.75, "base": 163.44, "trips_n": 0, "trips_pnl": 0.0, "trips_fees": None, "pos": None, "dd": 0.008}
    t = tx.strip_html(M.daily_msg(base)).splitlines()
    assert t[0] == "📅 Итоги суток" and t[1] == "Счёт 165.75 $ · за сутки +2.31 $ (+1.4%)"
    assert t[2] == "Закрытых сделок нет" and t[3] == "Позиции нет" and t[4] == "От пика счёта: −0.8%"
    full = {**base, "trips_n": 2, "trips_pnl": 3.2, "trips_fees": 0.98,
            "pos": {"side": "long", "qty": 0.003, "upnl": 1.06}, "dd": 0.0}
    t = tx.strip_html(M.daily_msg(full)).splitlines()
    assert t[2] == "Закрыто сделок: 2 · итого +3.20 $ (комиссии 0.98 $)"
    assert t[3] == "Позиция: ⬆️ ЛОНГ 0.003 BTC · нереализовано +1.06 $" and t[4] == "От пика счёта: 0%"
    assert_html(M.daily_msg(full))


def test_runner_daily_data_counts_closed_trips_of_last_24h(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    ex.mark = 82300.0
    run(exe.reconcile(0.0, BAR))
    pnl_ = rec.data("close")[0]["pnl"]
    st.set("peak_equity", 170.0)
    st.add_equity(163.44, (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds"))
    r = Runner(st, NS(), exe, rec)
    d = r.daily_data(165.0)
    assert d["trips_n"] == 1 and d["trips_pnl"] == pytest.approx(pnl_) and d["base"] == 163.44
    assert d["trips_fees"] == pytest.approx(0.49, abs=0.01) and d["dd"] == pytest.approx(5 / 170)
    assert "Итоги суток" in r.daily_summary(165.0)


# ---------- запуск / остановка ----------
def test_startup_msg_variants_and_misconfig_warning():
    d = {"kind": "startup", "env": "mainnet", "paper": False, "enabled": True, "killed": False, "no_keys": False, "equity": 163.44,
         "pos": {"side": "long", "qty": 0.003, "upnl": 1.06}, "pos_known": True}
    assert tx.strip_html(M.startup_msg(d)) == "Бот запущен ✅ · счёт 163.44 $ · торговля включена · позиция ЛОНГ 0.003 BTC (+1.06 $)"
    assert tx.strip_html(M.startup_msg({**d, "enabled": False, "pos": None})) == "Бот запущен ✅ · счёт 163.44 $ · торговля выключена · позиции нет"
    assert "kill-switch" in M.startup_msg({**d, "killed": True}) and "ключи не заданы" in M.startup_msg({**d, "no_keys": True, "equity": None})
    assert "позицию проверить не удалось" in M.startup_msg({**d, "pos_known": False})
    # неверная настройка запуска -> ОДНА заметная строка
    bad = tx.strip_html(M.startup_msg({**d, "paper": True, "env": "testnet"})).splitlines()
    assert len(bad) == 2 and bad[1] == "⚠️ Режим без ордеров / тестовая сеть"
    assert tx.strip_html(M.startup_msg({**d, "env": "testnet"})).splitlines()[1] == "⚠️ Тестовая сеть"
    assert tx.strip_html(M.startup_msg({**d, "paper": True})).splitlines()[1] == "⚠️ Режим без ордеров"
    assert M.config_warning("mainnet", False) is None


def test_shutdown_msg_variants():
    pos = {"side": "long", "qty": 0.003}
    assert tx.strip_html(M.shutdown_msg({"kind": "shutdown", "pos": pos, "stop": 65240.0})) == \
        "Бот остановлен · позиция ЛОНГ 0.003 BTC под биржевым стопом 65 240.0"
    assert "БЕЗ биржевого стопа" in M.shutdown_msg({"kind": "shutdown", "pos": pos, "stop": None})
    assert "позиции нет" in M.shutdown_msg({"kind": "shutdown", "pos": None})
    assert "проверьте вручную" in M.shutdown_msg({"kind": "shutdown", "unknown": True})
    assert "Бот запущен" in M.plain({"kind": "startup", "enabled": True, "pos": None}) and "Бот остановлен" in M.plain({"kind": "shutdown", "pos": None})


def test_app_startup_data(tmp_path):
    from tradebot.app import startup_data, shutdown_data
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(1.0, BAR))
    env = NS(bybit_env="mainnet", paper=False, has_keys=True)
    d = run(startup_data(env, st, ex))
    assert d["equity"] == pytest.approx(163.44 - 0.24, abs=0.01) or d["equity"] > 0
    assert d["pos"]["side"] == "long" and d["enabled"] and d["env"] == "mainnet"
    assert tx.strip_html(M.startup_msg(d)).startswith("Бот запущен ✅ · счёт ") and "позиция ЛОНГ 0.003 BTC" in M.startup_msg(d)
    sd = run(shutdown_data(ex))
    assert "под биржевым стопом" in M.shutdown_msg(sd)


# ---------- сигналы: слияние с торговым сообщением ----------
def _runner(tmp_path, monkeypatch, scripted, name="r.db"):
    st, ex, exe, rec = mk(tmp_path, name, equity=1000, mark=100_000, lev=1, stop_pct=0.08)
    seq = iter(scripted)
    cur = {}

    async def fetch(legs):
        return FeedData({}, pd.Timestamp("2026-03-01 05:00", tz="UTC"), True)

    async def now():
        return pd.Timestamp("2026-03-01 06:00:20", tz="UTC")

    def fake_target(legs, dfs, skip_oi=False, prev=None, stale_tfs=None):
        t, sigs = next(seq)
        cur["sigs"] = sigs
        return t, sigs
    monkeypatch.setattr("tradebot.trading.runner.compute_target", fake_target)
    r = Runner(st, NS(fetch=fetch, now=now), exe, rec)
    return st, ex, exe, rec, r


def test_signal_change_with_order_is_carried_in_trade_not_standalone(tmp_path, monkeypatch):
    K = "Regime switch (CHOP)@6h"
    st, ex, exe, rec, r = _runner(tmp_path, monkeypatch, [(0.0, {K: 0.0}), (0.5, {K: 1.0})])
    run(r.cycle("бар"))
    rec.items.clear()
    run(r.cycle("бар"))
    assert rec.kinds() == ["open"]                                         # ни «Сигнал: +0 -> +1», ни других служебных
    assert tx.strip_html(M.build(rec.data("open")[0])).count("Причина: Regime switch (CHOP) · 6h: вне рынка → лонг") == 1
    assert st.trades(1)[0]["reason"] == "бар, Regime switch (CHOP) · 6h: вне рынка → лонг"      # группа статистики «бар» сохранена
    assert not any("Сигнал " in t for t in rec.texts())


def test_signal_change_without_order_is_short_standalone(tmp_path, monkeypatch):
    K1, K2 = "SMA cross@4h", "Regime switch (CHOP)@6h"
    st, ex, exe, rec, r = _runner(tmp_path, monkeypatch, [(0.0, {K1: 1.0, K2: -1.0}), (0.0, {K1: -1.0, K2: 1.0})])
    run(r.cycle("бар"))
    rec.items.clear()
    run(r.cycle("бар"))                                                    # ноги скомпенсировали друг друга: цель та же, ордера нет
    assert rec.kinds() == ["signal", "signal"] or rec.kinds() == ["signal"]
    h = M.signal_msg(rec.data("signal")[0])
    t = tx.strip_html(h)
    assert "SMA cross · 4h: лонг → шорт" in t and "Цель: вне рынка" in t and "позицию менять не нужно" in t
    assert not ex.orders and "🔴" not in h and "🟢" not in h
    assert rec.items[0][0] == "signal" and st.get("target_exposure") == 0.0


def test_signal_standalone_when_trading_disabled_killed_or_latched(tmp_path, monkeypatch):
    K = "SMA cross@4h"
    st, ex, exe, rec, r = _runner(tmp_path, monkeypatch, [(0.0, {K: 0.0}), (1.0, {K: 1.0}), (1.0, {K: 1.0}), (0.0, {K: 0.0})])
    run(r.cycle("бар"))
    st.set_setting("enabled", False)
    rec.items.clear()
    run(r.cycle("бар"))
    (d,) = rec.data("signal")
    assert d["note"] == "торговля выключена, ордера не отправляю" and d["target"] == 1.0
    assert "Цель: ЛОНГ 100%" in tx.strip_html(M.signal_msg(d)) and not ex.orders
    st.set_setting("enabled", True)
    st.set("killed", True)
    rec.items.clear()
    run(r.cycle("бар"))                                                    # сигнал не менялся: сообщений нет
    assert rec.items == []


def test_signals_not_logged_as_old_style_text(tmp_path, monkeypatch):
    K = "SMA cross@4h"
    st, ex, exe, rec, r = _runner(tmp_path, monkeypatch, [(0.0, {K: 0.0}), (0.0, {K: 0.0})])
    run(r.cycle("бар"))
    run(r.cycle("бар"))
    assert rec.items == []                                                 # тихий бар: ничего не отправляем


# ---------- шум удалён ----------
def test_noise_messages_are_gone(tmp_path, monkeypatch):
    K = "SMA cross@4h"
    st, ex, exe, rec, r = _runner(tmp_path, monkeypatch, [(0.0, {K: 0.0})])

    async def late_now():
        return pd.Timestamp("2026-03-01 06:25:00", tz="UTC")
    r.feed.now = late_now
    run(r.cycle("старт/перезапуск"))                                       # опоздание 25 мин
    ex.external_trade(0.003)                                               # позиция «от старой версии»
    st.set("acted_exposure", 0.5)
    st.set("acted_qty", None)
    st.set("target_exposure", 0.5)
    run(exe.reconcile(0.5, BAR))                                           # принятие размера
    for bad in ("опозданием", "принят как удерживаемый", "PAPER", "защёлка снята"):
        assert not rec.has(bad), bad
    assert rec.items == []
    # режим без ордеров: ни «PAPER: на бирже плечо …», ни другого
    st2, ex2, exe2, rec2 = mk(tmp_path, "pp.db", paper=True)
    assert rec2.items == [] and exe2.trade_ok is False


# ---------- нет «среды», красный/зелёный только не для стороны ----------
def _all_texts(tmp_path):
    out = []
    long_ = {"kind": "open", "side": "long", "qty": 0.003, "price": 81550.1, "leverage": 2, "equity": 163.44, "stop": 65240.0, "reason": "бар"}
    short = {**long_, "side": "short", "stop": 97860.0}
    closed = {"kind": "close", "side": "long", "qty": 0.003, "entry": 81550.1, "exit": 82300.0, "pnl": 1.76, "fees": 0.49, "funding": 0.0,
              "exact": True, "pct": 0.011, "pct_est": False, "roe": 0.014, "held_s": 8100, "reason": "бар", "equity": 165.2, "reds": 0}
    out += [M.open_msg(long_), M.open_msg(short), M.close_msg(closed), M.close_msg({**closed, "pnl": -2.0, "side": "short", "exact": False}),
            M.increase_msg({"side": "long", "delta": 0.002, "price": 82000.0, "total_qty": 0.005, "avg_entry": 81800.0, "leverage": 2,
                            "equity": 163.0, "reason": "бар"}),
            M.reduce_msg({"side": "short", "delta": 0.002, "price": 82000.0, "total_qty": 0.001, "pnl": -0.5, "exact": True, "reason": "бар",
                          "equity": 163.0}),
            M.stopout_msg({"side": "long", "qty": 0.003, "entry": 81550.1, "exit": 65240.0, "pnl": -32.1, "pct": -0.196, "latched": True}),
            M.stopout_msg({"side": "long", "qty": 0.003, "entry": 81550.1, "pnl": None, "latched": False}),
            M.kill_msg({"reason": "просадка", "closed": True, "pnl": -5.0, "pct": -0.03, "equity": 100.0}),
            M.panic_msg({"closed": False}),
            M.signal_msg({"changes": [{"label": "SMA cross@4h", "prev": 1.0, "new": -1.0}], "target": -0.5, "note": "x"}),
            M.daily_msg({"equity": 165.0, "base": 163.0, "trips_n": 1, "trips_pnl": 1.0, "trips_fees": 0.4, "pos": {"side": "short", "qty": 0.01, "upnl": -1.0}, "dd": 0.01}),
            M.startup_msg({"enabled": True, "pos": None, "equity": 163.0, "env": "mainnet", "paper": False}),
            M.shutdown_msg({"pos": {"side": "long", "qty": 0.003}, "stop": 65000.0}),
            *M.position_lines({"side": "short", "qty": 0.003, "entry": 81550.1, "mark": 81000.0, "upnl": 1.6}, 2, 163.44, 97860.0)]
    out += [M.w_no_keys(), M.w_setup_failed("x"), M.w_setup_mismatch({"leverage": 1, "isolated": True, "one_way": True}, 2), M.w_key_check_failed(ValueError()),
            M.w_key_read_only(), M.w_key_withdraw(), M.w_order_ambiguous(), M.w_order_unconfirmed(), M.w_rearm_failed("x"), M.w_stop_failed(),
            M.w_not_flat("long", 0.003), M.w_orders_cancelled(2), M.w_balance_unavailable("x"), M.w_exchange_error("x"), M.w_close_failed("x"),
            M.w_weights(["a"]), M.w_feed_down("x"), M.w_oi_late(), M.w_tf_stale("6h", "x"), M.w_no_signal({}), M.w_cycle_failed(KeyError("x")),
            M.w_monitor_failed(5, "x")]
    return out


def test_no_environment_words_and_no_red_green_in_builders(tmp_path):
    for t in _all_texts(tmp_path):
        assert not ENV_WORDS.search(t), t
        assert "🔴" not in t and "🟢" not in t, t
    # красный/зелёный кружок вообще не используется в итоговых текстах сделок
    for t in _all_texts(tmp_path)[:12]:
        assert "🔴" not in t and "🟢" not in t


def test_no_environment_words_on_any_screen_when_configured_properly(tmp_path):
    st = Store(tmp_path / "s.db")
    exe = Executor(DryRunExchange(equity=163.44), st, lambda *a: None, "mainnet")
    c = Controller(st, exe, NS(trigger=lambda: None), "mainnet")
    st.set("equity", 163.44)
    st.set("position", {"side": "short", "qty": 0.003, "entry": 81550.1, "upnl": 1.0, "mark": 81200.0})
    st.set("target_exposure", -0.5)
    st.log_signals("2026-01-01T00:00:00+00:00", {"SMA cross@4h": 1.0, "Regime switch (CHOP)@6h": -1.0}, 0.0)
    st.add_trade("sell", 0.003, 81550.1, "бар", "o1")
    st.add_trade("buy", 0.003, 81000.0, "бар, X: шорт → вне рынка", "o2")
    st.add_event("info", "Бот запущен ✅ · счёт 163.44 $")
    for d in ("h", "pos", "sg", "tx:0", "ev:0", "set", "lv", "sl", "rk", "lg", "lg:0", "lt", "tr:on", "lv:s:3", "pk", "cl", "pn",
              "lg:0:w:25", "lg:2:t", "lg:eq", "sl:s:5", "rk:dd:s:20"):
        s = asyncio.run(c.handle(kb.parse_cb(d)))
        assert not ENV_WORDS.search(s.text), (d, s.text)
        assert "🔴" not in s.text, d                                       # красный кружок нигде в пользовательских экранах
    for d in ("h", "pos", "sg", "tx:0"):
        t = asyncio.run(c.handle(kb.parse_cb(d))).text
        assert "🔴" not in t and "🟢" not in t, d
    ob = __import__("tradebot.telegram.handlers", fromlist=["ONBOARDING"]).ONBOARDING
    assert not ENV_WORDS.search(ob)
    assert all(not ENV_WORDS.search(b.text) for d in ("h", "pos", "set") for row in asyncio.run(c.handle(kb.parse_cb(d))).markup.inline_keyboard for b in row)


def test_home_shows_one_warning_line_only_when_misconfigured(tmp_path):
    for env, paper, want in (("mainnet", False, None), ("testnet", False, "Тестовая сеть"), ("mainnet", True, "Режим без ордеров"),
                             ("testnet", True, "Режим без ордеров / тестовая сеть")):
        st = Store(tmp_path / f"{env}{paper}.db", paper=paper)
        exe = Executor(DryRunExchange(), st, lambda *a: None, env, paper=paper)
        c = Controller(st, exe, NS(trigger=lambda: None), env)
        t = asyncio.run(c.handle(("h",))).text
        assert not ENV_WORDS.search(t)
        if want is None:
            assert "⚠️ Тест" not in t and "⚠️ Режим" not in t
        else:
            assert tx.strip_html(t).count(f"⚠️ {want}") == 1 and not ENV_WORDS.search(t)


# ---------- экраны «Сделки» и «События» ----------
def test_trades_screen_shows_round_trips_with_dollars_and_total(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    c = Controller(st, exe, NS(trigger=lambda: None), "mainnet")
    run(exe.reconcile(1.0, BAR, "бар, Regime switch (CHOP) · 6h: вне рынка → лонг"))
    ex.mark = 82300.0
    run(exe.reconcile(0.0, BAR, "бар, Regime switch (CHOP) · 6h: лонг → вне рынка"))
    pnl_ = rec.data("close")[0]["pnl"]
    s = asyncio.run(c.handle(("tx", "0")))
    t = tx.strip_html(s.text)
    assert f"За 7 дней: +{pnl_:.2f} $ · 1 сделка" in t
    assert f"✅ +{pnl_:.2f} $ (+1.1% счёта) · ⬆️ ЛОНГ 0.003 BTC" in t and "81 550.1 → 82 300.0" in t
    assert "Покупка" not in t and "Продажа" not in t                        # не сырые исполнения
    assert "Regime switch (CHOP) · 6h: лонг → вне рынка" in t
    assert [b.text for b in s.markup.inline_keyboard[0]][1] == "1/1"


def test_trades_screen_estimates_history_and_marks_unknown_stop(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    c = Controller(st, exe, NS(trigger=lambda: None), "mainnet")
    # история до этой версии: вход/выход только в таблице исполнений
    st.add_trade("buy", 0.01, 100000.0, "бар", "h1")
    st.add_trade("sell", 0.01, 101000.0, "бар", "h2")
    t = tx.strip_html(asyncio.run(c.handle(("tx", "0"))).text)
    est = 0.01 * 1000 - 0.01 * 100000 * 0.001 - 0.01 * 101000 * 0.001
    assert f"✅ ≈ +{est:.2f} $" in t and "За 7 дней: +" in t
    # стоп-аут без суммы
    st.add_roundtrip(ts_open=st.all_trades()[-1]["ts"], ts_close=datetime.now(timezone.utc).isoformat(timespec="seconds"), side="long",
                     qty=0.003, entry=81550.1, exit=None, pnl=None, fees=None, funding=None, equity_open=163.0, eq_est=0, approx=1,
                     kind="stop", reason="закрыто биржей", order_id=None)
    t = tx.strip_html(asyncio.run(c.handle(("tx", "0"))).text)
    assert "🛑 сумма уточняется" in t


def test_events_screen_hides_noise(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    c = Controller(st, exe, NS(trigger=lambda: None), "mainnet")
    for t in ("Действую с опозданием на 25 мин (после перезапуска/паузы).", "Размер текущей позиции 0.003 BTC принят как удерживаемый",
              "PAPER: на бирже плечо 1x", "Сверка: защёлка", "Открыт ЛОНГ · важное"):
        st.add_event("info", t)
    t = tx.strip_html(asyncio.run(c.handle(("ev", "0"))).text)
    assert "Открыт ЛОНГ · важное" in t and "опозданием" not in t and "принят как" not in t and "PAPER" not in t and "Сверка:" not in t


# ---------- pnl.py: чистая бухгалтерия ----------
def test_pnl_replay_average_cost_partial_and_flip():
    f = lambda ts, side, q, px, oid: {"ts": f"2026-01-01T{ts}:00+00:00", "side": side, "qty": q, "price": px, "reason": "r", "order_id": oid}
    fills = [f("10:00", "buy", 0.002, 100000, "a"), f("10:10", "buy", 0.002, 110000, "b"),       # средняя 105000
             f("11:00", "sell", 0.001, 115000, "c"),                                               # частичное сокращение
             f("12:00", "sell", 0.006, 100000, "d"),                                               # закрыть 0.003 + открыть шорт 0.003
             f("13:00", "buy", 0.003, 90000, "e")]
    steps, closed, open_trip = pnl.replay(fills, fee_rate=0.001)
    assert len(closed) == 2 and open_trip is None
    t1, t2 = closed
    assert t1["side"] == "long" and t1["entry"] == pytest.approx(105000) and t1["qty"] == pytest.approx(0.004)
    assert t1["gross"] == pytest.approx(0.001 * 10000 + 0.003 * -5000)
    assert t1["close_order_id"] == "d" and t1["exit"] == pytest.approx((0.001 * 115000 + 0.003 * 100000) / 0.004)
    assert t2["side"] == "short" and t2["entry"] == 100000 and t2["gross"] == pytest.approx(0.003 * 10000)
    assert t2["pnl"] == pytest.approx(t2["gross"] - 0.003 * 100000 * 0.001 - 0.003 * 90000 * 0.001)
    c = pnl.step_for(steps, "c")
    assert c["closed_qty"] == pytest.approx(0.001) and c["net"] == pytest.approx(10 - 0.001 * 105000 * 0.001 - 0.001 * 115000 * 0.001)
    # сумма оценок закрытых частей = P/L сделки
    nets = sum(pnl.step_for(steps, o)["net"] for o in ("c", "d") if True) - pnl.step_for(steps, "d")["fee_close"] * 0   # d закрывает обе стороны
    assert t1["pnl"] == pytest.approx(pnl.step_for(steps, "c")["net"] + (pnl.step_for(steps, "d")["gross"]
                                       - 0.003 * 105000 * 0.001 - 0.003 * 100000 * 0.001))
    ot = pnl.replay(fills[:2])[2]
    assert ot["open"] and ot["qty"] == pytest.approx(0.004) and ot["entry"] == pytest.approx(105000)


def test_pnl_record_parts_and_normalize():
    raw = {"symbol": "BTCUSDT", "orderType": "Market", "leverage": "2", "updatedTime": "1700000001000", "side": "Sell", "orderId": "X1",
           "closedPnl": "2.31", "openFee": "0.2445", "closeFee": "0.2469", "avgEntryPrice": "81550.1", "qty": "0.003",
           "closedSize": "0.003", "avgExitPrice": "82300.0", "createdTime": "1700000000000", "cumEntryValue": "244.65",
           "cumExitValue": "246.9", "execType": "Trade", "fillCount": "1"}
    rec = pnl.normalize_record(raw)
    assert rec["order_id"] == "X1" and rec["side"] == "sell" and rec["closed_pnl"] == 2.31 and rec["leverage"] == 2.0 and rec["fills"] == 1
    p = pnl.record_parts(rec)
    gross = 0.003 * (82300.0 - 81550.1)
    assert p["net"] == 2.31 and p["fees"] == pytest.approx(0.4914) and p["funding"] == pytest.approx(2.31 - (gross - 0.4914), abs=1e-9)
    assert pnl.normalize_record({})["closed_pnl"] == 0.0                       # мусор не роняет


# ---------- биржа: closed_pnl ----------
def _client(handler):
    c = BybitClient("https://x", "K", "S", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    c._synced = 1e18
    return c


def test_bybit_closed_pnl_by_order_id_and_by_time():
    seen = []
    rows = [{"orderId": "NEW", "updatedTime": "2000", "closedPnl": "-32.1", "side": "Sell", "avgEntryPrice": "81550.1",
             "avgExitPrice": "65240", "closedSize": "0.003", "qty": "0.003", "openFee": "0.24", "closeFee": "0.2", "execType": "Trade"},
            {"orderId": "OLD", "updatedTime": "1000", "closedPnl": "1.5", "side": "Sell"}]

    def handler(req):
        seen.append((req.method, req.url.path, dict(req.url.params)))
        return httpx.Response(200, json={"retCode": 0, "result": {"list": rows}})
    c = _client(handler)
    r = run(c.closed_pnl("OLD"))
    assert r["order_id"] == "OLD" and r["closed_pnl"] == 1.5
    assert run(c.closed_pnl("NOPE")) is None
    r = run(c.closed_pnl(None, 1500))
    assert r["order_id"] == "NEW" and r["closed_pnl"] == -32.1
    assert run(c.closed_pnl(None, 5000)) is None
    assert all(m == "GET" and p == "/v5/position/closed-pnl" for m, p, _ in seen)
    assert seen[0][2] == {"category": "linear", "symbol": "BTCUSDT", "limit": "10"}


def test_readonly_client_forwards_closed_pnl_get_only():
    seen = []

    def handler(req):
        seen.append(req.method)
        return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"orderId": "A", "updatedTime": "5", "closedPnl": "1"}]}})
    ro = ReadOnlyClient("https://x", "K", "S", transport=httpx.MockTransport(handler))
    ro._c._synced = 1e18
    assert run(ro.closed_pnl("A"))["closed_pnl"] == 1.0 and seen == ["GET"]


def test_dryrun_closed_pnl_simulates_net_pnl_minus_fees():
    ex = DryRunExchange(equity=5000, mark=100_000, fee_rate=0.002)

    async def go():
        await ex.market("buy", 0.01, False, "L1")
        ex.mark = 101_000
        await ex.market("sell", 0.004, True, "L2")
        await ex.market("sell", 0.006, True, "L3")
        return await ex.closed_pnl("o2"), await ex.closed_pnl("o3"), await ex.closed_pnl("nope"), await ex.closed_pnl(None, 0)
    a, b, none, last = run(go())
    assert a["closed_pnl"] == pytest.approx(0.004 * 1000 - 0.004 * 100000 * 0.002 - 0.004 * 101000 * 0.002)
    assert b["closed_pnl"] == pytest.approx(0.006 * 1000 - 0.006 * 100000 * 0.002 - 0.006 * 101000 * 0.002) and none is None
    assert last["order_id"] == "o3" and a["side"] == "sell" and a["avg_entry"] == 100000 and a["avg_exit"] == 101000
    assert ex.cash == pytest.approx(5000 + 10 - ex.fees_paid)            # итог кошелька = сумма записей


# ---------- хранилище ----------
def test_store_roundtrips_and_merge_cut(tmp_path):
    st = Store(tmp_path / "rt.db")
    st.add_trade("buy", 0.01, 100000.0, "бар", "h1")
    st.add_trade("sell", 0.01, 101000.0, "бар", "h2")
    st._x("UPDATE trades SET ts='2026-01-01T10:00:00+00:00'")               # история — давно
    assert len(st.closed_trips()) == 1 and st.closed_trips()[0]["approx"] == 1
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rid = st.add_roundtrip(ts_open=now, ts_close=now, side="short", qty=0.01, entry=1.0, exit=2.0, pnl=-1.0, fees=0.1, funding=0.0,
                           equity_open=100.0, eq_est=0, approx=0, kind="close", reason="r", order_id="X")
    trips = st.closed_trips()
    assert len(trips) == 2 and trips[0]["order_id"] == "X"                   # новые сверху, история слита
    st.update_roundtrip(rid, pnl=-2.0)
    assert st.closed_trips()[0]["pnl"] == -2.0
    with pytest.raises(ValueError):
        st.add_roundtrip(bogus=1)
    # сделка без строки в таблице НЕ пропадает (даже если закрыта позже других записей) ...
    st.add_trade("buy", 0.01, 100.0, "бар", "n1")
    st.add_trade("sell", 0.01, 101.0, "бар", "n2")
    assert len(st.closed_trips()) == 3
    # ... а когда строка для неё появилась (по order_id закрывающего ордера), реконструкция не дублируется
    st.add_roundtrip(ts_open=now, ts_close=now, side="long", qty=0.01, entry=100.0, exit=101.0, pnl=0.5, fees=0.0, funding=0.0,
                     equity_open=100.0, eq_est=0, approx=0, kind="close", reason="r", order_id="n2")
    trips = st.closed_trips()
    assert len(trips) == 3 and sum(1 for t in trips if t["order_id"] == "n2") == 1


def test_store_legacy_db_gets_roundtrips_table_additively(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    db = sqlite3.connect(p)
    db.executescript("CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT NOT NULL); CREATE TABLE trades(id INTEGER PRIMARY KEY, ts TEXT, side TEXT, "
                     "qty REAL, price REAL, reason TEXT, order_id TEXT);")
    db.commit()
    db.close()
    st = Store(p)
    assert st.roundtrip_rows() == [] and st.add_trade("buy", 1, 1, "r", "o") >= 1


# ---------- устойчивость ----------
def test_bookkeeping_failure_never_blocks_trading(tmp_path, monkeypatch):
    st, ex, exe, rec = mk(tmp_path)
    monkeypatch.setattr(exe, "_book", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    assert run(exe.reconcile(1.0, BAR)) == "выполнено" and ex.qty > 0 and ex.stop is not None


def test_notifier_builds_structured_events_and_logs_plain_text(tmp_path):
    st = Store(tmp_path / "n.db")
    n = Notifier(st, 1, "mainnet", False)
    d = {"kind": "close", "side": "long", "qty": 0.003, "entry": 81550.1, "exit": 82300.0, "pnl": 2.31, "fees": 0.49, "funding": 0.0,
         "exact": True, "pct": 0.014, "pct_est": False, "roe": 0.028, "held_s": 600, "reason": HOSTILE, "equity": 165.0, "reds": 0}
    M.emit(n, "trade", d)
    M.emit(n, "trade", d)                                                   # одинаковые сделки не склеиваются дедупом
    assert n.queue.qsize() == 2
    level, html = n.queue.get_nowait()
    assert level == "trade" and html.startswith("✅ <b>Закрыт ЛОНГ: +2.31 $</b>") and "&lt;script&gt;" in html and "<script>" not in html
    assert_html(html)
    ev = st.events(5)[0]
    assert ev["text"].startswith("✅ Закрыт ЛОНГ: +2.31 $") and "<" not in ev["text"].replace("<script>alert(\"x\")</script> & <b>unclosed", "")
    n.queue.get_nowait()
    n("trade", "x", {"kind": "unknown_kind"})                               # неизвестный вид -> показываем plain-текст, а не теряем
    assert n.queue.get_nowait()[1] == "💱 x"


# ---------- ревью: порядок монитора (refresh_cache -> check_stopout), снимок позиции ----------
def _monitor_stopout(tmp_path, side_target, name, far_exit=None):
    """Как runner.tick(): кэш обновляется ПЕРВЫМ (позиции уже нет -> position/pos_stop = None), затем check_stopout."""
    st, ex, exe, rec = mk(tmp_path, name)
    run(exe.reconcile(side_target, BAR))
    run(exe.refresh_cache())                          # позиция видна монитору
    last = st.get("pos_last")                         # снимок открытой позиции сохранён (стоп подтверждён биржей)
    assert last and last["side"] == ("long" if side_target > 0 else "short") and last["qty"] == pytest.approx(0.003) and last["stop"]
    rec.items.clear()
    if far_exit is not None:
        ex.stop = far_exit                            # биржа закрыла не по стопу бота
    ex.stop_out()
    run(exe.refresh_cache())                          # реальный порядок тика: кэш обнулён ДО check_stopout
    assert st.get("position") is None and st.get("pos_stop") is None
    run(exe.check_stopout())
    return st, ex, exe, rec


@pytest.mark.parametrize("target,side", [(1.0, "long"), (-1.0, "short")])
def test_monitor_order_stopout_keeps_position_snapshot(tmp_path, target, side):
    st, ex, exe, rec = _monitor_stopout(tmp_path, target, f"m{side}.db")
    (d,) = rec.data("stopout")
    assert d["side"] == side and d["qty"] == pytest.approx(0.003) and d["entry"] == pytest.approx(81550.1)
    assert d["exit_kind"] == "stop" and d["pnl"] is not None and d["pnl"] < -30
    t = tx.strip_html(M.stopout_msg(d))
    assert f"{M.word(side)} 0.003 BTC" in t and "NONE" not in t.upper()
    (row,) = st.roundtrip_rows()
    assert row["side"] == side and row["qty"] == pytest.approx(0.003) and row["entry"] == pytest.approx(81550.1)
    assert st.get("pos_last") is None                   # закрытие объявлено и записано -> снимок очищен
    c = Controller(st, exe, NS(trigger=lambda: None), "mainnet")
    scr = tx.strip_html(asyncio.run(c.handle(("tx", "0"))).text)
    assert f"{M.arrow(side)} {M.word(side)} 0.003 BTC" in scr and "NONE" not in scr.upper() and "▫️" not in scr
    assert "сумма уточняется" not in scr


def test_monitor_order_external_close_far_from_stop(tmp_path):
    st, ex, exe, rec = _monitor_stopout(tmp_path, 1.0, "ext.db", far_exit=90000.0)
    (d,) = rec.data("stopout")
    assert d["exit_kind"] == "external" and d["pnl"] > 0 and d["exit"] == pytest.approx(90000.0)
    assert tx.strip_html(M.stopout_msg(d)).startswith("🛑 Позиция закрыта на бирже (не ботом): +")
    (row,) = st.roundtrip_rows()
    assert row["kind"] == "external" and row["side"] == "long" and row["pnl"] == pytest.approx(d["pnl"])


def test_stopout_without_any_snapshot_infers_side_from_record(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    run(exe.reconcile(-1.0, BAR))
    st.set("pos_last", None)
    st.set("position", None)                           # снимка нет вовсе (например, БД очищена)
    ex.stop_out()
    run(exe.check_stopout())
    (d,) = rec.data("stopout")
    assert d["side"] == "short" and st.roundtrip_rows()[0]["side"] == "short"


def test_trades_screen_never_prints_none_for_unknown_side(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    c = Controller(st, exe, NS(trigger=lambda: None), "mainnet")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st.add_roundtrip(ts_open=now, ts_close=now, side=None, qty=None, entry=None, exit=None, pnl=None, fees=None, funding=None,
                     equity_open=None, eq_est=1, approx=1, kind="stop", reason="закрыто биржей", order_id=None)
    st.add_roundtrip(ts_open=now, ts_close=now, side=None, qty=None, entry=None, exit=None, pnl=-5.0, fees=None, funding=None,
                     equity_open=100.0, eq_est=0, approx=0, kind="stop", reason="закрыто биржей", order_id="Z")
    t = tx.strip_html(asyncio.run(c.handle(("tx", "0"))).text)
    assert "NONE" not in t.upper() and "▫️" not in t and "сторона неизвестна" in t and "0 BTC" not in t


# ---------- ревью: close_all фиксирует состояние ДО оповещения ----------
def test_close_all_bookkeeping_happens_before_announce(tmp_path):
    for mode in ("raise", "hang"):
        st, ex, exe, rec = mk(tmp_path, f"ca_{mode}.db")
        st.set("target_exposure", 0.5)
        run(exe.reconcile(0.5, BAR))
        assert st.get("acted_exposure") == 0.5

        async def bad(quiet=False, mode=mode):
            if mode == "raise":
                raise RuntimeError("announce crashed")
            await asyncio.sleep(3600)
        exe._announce = bad

        async def go(exe=exe, mode=mode):
            if mode == "raise":
                with pytest.raises(RuntimeError):
                    await exe.close_all()
            else:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(exe.close_all(), 0.3)
        run(go())
        assert ex.qty == 0
        assert st.get("acted_exposure") is None and st.get("acted_qty") is None            # не остались acted_*
        assert st.get("latch") is True and st.get("latch_exposure") == 0.5, mode           # и защёлка на месте


def test_close_all_failure_does_not_set_latch_and_reports_error(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    st.set("target_exposure", 0.5)
    run(exe.reconcile(0.5, BAR))

    async def boom(*a, **k):
        from tradebot.trading.exchange import ExchangeError
        raise ExchangeError("net")
    ex.market = boom
    assert run(exe.close_all()).startswith("ошибка") and not st.get("latch") and st.get("acted_exposure") == 0.5


# ---------- ревью: _ensure_trip учитывает разрывы от стоп-аутов ----------
def test_ensure_trip_uses_stop_breaks(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    st.add_trade("buy", 0.003, 90000.0, "бар", "t1")           # старый вход, потом позицию закрыла биржа мимо ленты
    st.add_trade("buy", 0.003, 81550.1, "бар", "t2")           # новый вход
    st._x("UPDATE trades SET ts='2026-01-01T10:00:00+00:00' WHERE order_id='t1'")
    st._x("UPDATE trades SET ts='2026-01-03T10:00:00+00:00' WHERE order_id='t2'")
    st._x("INSERT INTO events(ts,level,text) VALUES('2026-01-02T10:00:00+00:00','error','Позиция исчезла (стоп/ликвидация) · x')")
    st.add_equity(150.0, "2026-01-01T09:00:00+00:00")
    st.add_equity(163.0, "2026-01-03T09:00:00+00:00")
    ex.external_trade(0.003)
    run(exe.refresh_cache())
    assert st.get("pos_entry_ts") == "2026-01-03T10:00:00+00:00"       # без разрывов было бы 2026-01-01 (смешали бы две позиции)
    assert st.get("pos_entry_equity") == 163.0 and st.get("pos_trip")["reds"] == 0


# ---------- ревью: уточнение не считается вторым разрывом; несколько ожидающих стоп-аутов ----------
def test_followup_has_no_break_marker_and_reentry_is_safe(tmp_path):
    st = Store(tmp_path / "fu.db")
    st.set_setting("enabled", True)
    st.set_setting("leverage", 2)
    st.set_setting("stop_pct", 0.2)
    ex = DryRunExchange(equity=163.44, mark=81550.1)
    n = Notifier(st, 1, "mainnet", False)
    exe = Executor(ex, st, n, "mainnet")
    exe.poll_delay = 0
    exe.pnl_delays = (0.0, 0.0, 0.0)
    run(exe.setup())
    run(exe.reconcile(1.0, BAR))
    run(exe.refresh_cache())
    ex.closed_pnl_off = True
    ex.stop_out()
    run(exe.reconcile(1.0, BAR))                                    # стоп-аут без записи
    assert len(st.stop_breaks()) == 1
    run(exe.reconcile(-1.0, BAR))                                   # цель сменилась: вход в шорт (повторный вход)
    assert ex.qty < 0
    entry_ts = st.get("pos_entry_ts")
    ex.closed_pnl_off = False
    run(exe.check_stopout())                                        # запись нашлась: уточнение
    texts = [e["text"] for e in st.events(50)]
    follow = [t for t in texts if "Уточнение" in t]
    assert len(follow) == 1 and "озиция исчезла" not in follow[0]
    assert len(st.stop_breaks()) == 1                               # второго разрыва нет
    assert st.get("pos_entry_ts") == entry_ts and ex.qty < 0       # новая позиция не «закрыта» задним числом
    assert [r["pnl"] is not None for r in st.roundtrip_rows()] == [True] and not st.get("pending_stopouts")


def test_two_pending_stopouts_do_not_overwrite_each_other(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    ex.closed_pnl_off = True
    run(exe.reconcile(1.0, BAR))
    run(exe.refresh_cache())
    ex.stop_out()
    run(exe.reconcile(1.0, BAR))                                    # №1 без записи
    run(exe.reconcile(-1.0, BAR))
    run(exe.refresh_cache())                                        # вход в шорт
    ex.stop_out()
    run(exe.reconcile(-1.0, BAR))                                   # №2 без записи
    pend = st.get("pending_stopouts")
    assert len(pend) == 2 and pend[0]["rt_id"] != pend[1]["rt_id"] and len(st.roundtrip_rows()) == 2
    assert [p["data"]["side"] for p in pend] == ["long", "short"]
    ex.closed_pnl_off = False
    rec.items.clear()
    run(exe.check_stopout())
    fu = rec.data("stopout")
    assert len(fu) == 1 and fu[0]["followup"] and fu[0]["side"] == "short"      # досланы те, чью запись можно однозначно найти
    left = st.get("pending_stopouts")
    assert len(left) == 1 and left[0]["data"]["side"] == "long"                  # первый не потерян и не получил чужую запись


# ---------- ревью: неоднозначный ордер с изменившейся позицией считается отправленным ----------
def test_ambiguous_order_with_changed_position_counts_as_sent(tmp_path):
    st, ex, exe, rec = mk(tmp_path)
    exe.retry_wait = 0

    async def hidden(link):
        return None                                                   # биржа ордер «не показывает»
    ex.order_by_link = hidden
    ex.fail_next = "after"                                             # ордер исполнился, ответ потерян
    sent0 = exe.sent
    run(exe._send("buy", 0.003, False, BAR, "бар", 0.0))
    assert ex.qty == pytest.approx(0.003) and exe.sent == sent0 + 1
    assert any("Ответ на ордер не получен" in t for t in rec.texts())
