"""Экраны «Главная», «Позиция», «Сигналы», «Сделки», «События» (только чтение). Миксин ScreensMain для Controller."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from tradebot.core.portfolio import Leg
from tradebot.telegram import keyboards as kb
from tradebot.telegram import messages as M
from tradebot.telegram import texts as tx
from tradebot.telegram.common import P, VIEW_HOME, VIEW_POS, VIEW_SIGNALS
from tradebot.telegram.legflow import is_active
from tradebot.telegram.notify import ICONS
from tradebot.telegram.panel import Screen
from tradebot.telegram.texts import b, code, esc, it

TRADES_DAYS = 7                  # окно итога на экране «Сделки»
NOISE_EVENTS = ("Действую с опозданием", "Размер текущей позиции", "PAPER:", "Целевая экспозиция изменилась — защёлка снята",
                "Сверка:")       # служебные строки старых версий: на экране «События» скрыты


def _deals(n: int) -> str:
    """Склонение: 1 сделка, 2-4 сделки, 5+ сделок."""
    return "сделка" if n % 10 == 1 and n % 100 != 11 else ("сделки" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "сделок")


class ScreensMain:
    """Миксин: нужны self.st, self.clock, self.tz, self.compose/head."""

    # ---------- главный экран ----------
    def home(self, toast: str | None = None) -> Screen:
        st, s, now = self.st, self.st.settings(), self.clock()
        killed, eq, peak, day0 = st.get("killed", False), st.get("equity"), st.get("peak_equity"), st.get("day_start_equity")
        pos, tgt = st.get("position"), st.get("target_exposure")
        if killed:
            status = "🛑 <b>Стоп-кран</b> — торговля заблокирована"
        else:
            status = "▶️ <b>Торговля включена</b>" if s["enabled"] else "⏸ <b>Торговля выключена</b>"
        top = [status]
        if killed:
            top.append(f"🛑 Причина: {esc(st.get('kill_reason'))}")
        if st.get("latch"):
            top.append(it("🔒 Защёлка: после стопа/ручного закрытия жду смены сигнала"))
        money = [f"💼 Счёт: {code(tx.n(eq))} $" if eq is not None else "💼 Счёт: —"]
        if eq is not None:
            bits = []
            if day0:
                d = eq - day0
                bits.append(f"сегодня {code(M.usd(d, 2, True))} ({code(M.pct(d / day0, 2))})")
            if peak and peak > 0:
                bits.append(f"от пика {code(M.pct(-(peak - eq) / peak) if eq < peak else '0%')}")
            if bits:
                money.append("    " + " · ".join(bits))
        trade = self._pos_lines(pos)
        trade.append(f"🎯 Цель: {code(M.target_words(tgt))}" if tgt is not None else "🎯 Цель: считаю…")
        sg_lines = ["📡 <b>Сигналы</b>"] + self._signal_lines()
        hb = tx.parse_iso(st.get("heartbeat"))
        stale = hb is None or (now - hb).total_seconds() > 180
        foot = [f"⏱ Следующий бар через {code(tx.minutes_to_next_bar(now))} мин",
                f"{'⚠️' if stale else '💓'} Данные биржи: {esc(tx.age(hb, now))}"]
        text = self.compose(self.head(), top, money, trade, sg_lines, foot)
        return Screen(text, kb.home_menu(bool(s["enabled"]), bool(killed), bool(st.get("latch"))),
                      toast, live=True, view=VIEW_HOME)

    def _signal_lines(self) -> list[str]:
        """Строки сигналов ТОЛЬКО работающих ног; неактивные (выключены или вес 0) — одной строкой-ссылкой на «Стратегии»."""
        cfg, sigs = self.st.leg_cfg(), self.st.latest_signals()
        lines = [self._leg_line(leg, w, en, sigs.get(leg.key, (None,))[0]) for leg, w, en in cfg if is_active(w, en)]
        idle = sum(1 for _, w, en in cfg if not is_active(w, en))
        if not lines:
            lines.append(it("нет работающих стратегий — добавьте или дайте вес в «🧩 Стратегии»"))
        if idle:
            lines.append(it(f"⏸ неактивных: {idle} (в «🧩 Стратегии»)"))
        return lines

    def _pos_lines(self, pos: dict | None) -> list[str]:
        """Открытая позиция: сторона словами (стрелка, не цвет), размер, стоимость с плечом, свои деньги и доля счёта,
        вход/текущая, нереализованный P/L в $ и % счёта, биржевой стоп и расстояние до него."""
        st = self.st
        stop = st.get("pos_stop")
        if pos and not stop:
            sp = st.settings()["stop_pct"]
            stop = pos["entry"] * (1 - sp if pos["side"] == "long" else 1 + sp)      # ожидаемый, пока биржа не подтвердила
        return M.position_lines(pos, float(st.settings()["leverage"]), st.get("equity"), stop)

    @staticmethod
    def _pnl_lines(eq: float | None, day_start: float | None, base: float | None) -> list[str]:
        """P/L по изменению счёта (включает комиссии, фандинг и нереализованный результат): за сегодня и от базовой точки.
        Без цветных значков: знак виден в числе."""
        out = []
        for label, ref, suffix in (("сегодня", day_start, ""),
                                   ("всего", base, f" {it('(от ' + tx.n(base) + ' $)')}" if base else "")):
            if eq is None or not ref:
                out.append(f"P/L {label}: —")
            else:
                d = eq - ref
                out.append(f"P/L {label}: {code(M.usd(d, 2, True))} ({code(M.pct(d / ref, 2))}){suffix}")
        return out

    @staticmethod
    def _leg_line(leg: Leg, w: float, en: bool, sg: float | None) -> str:
        """Строка «сигнал» для Главной/Сигналов: точка, имя · tf, слово сигнала, вес."""
        name = f"{esc(leg.strategy)} · {esc(leg.tf)}"
        if not en:
            return f"⚪ {name} — выключена"
        if w <= 0:  # вес 0: нога включена, но бот её не считает — «нет данных» вводило бы в заблуждение
            return f"⏸ {name} — не торгуется"
        return f"{tx.sig_icon(sg)} {name} — {tx.sig_word(sg)} · вес {P(w)}"

    # ---------- подэкраны ----------
    async def position_screen(self) -> Screen:
        st, pos = self.st, self.st.get("position")
        pnl = self._pnl_lines(st.get("equity"), st.get("day_start_equity"), st.get("baseline_equity"))
        secs = [self._pos_lines(pos), pnl]
        if pos:
            secs.append(it("Стоп на бирже ставится после каждой сделки."))
        return Screen(self.compose(self.head("Позиция"), *secs), kb.position_menu(bool(pos)),
                      live=True, view=VIEW_POS)

    def signals_screen(self) -> Screen:
        st = self.st
        legs = self._signal_lines()
        tgt = st.get("target_exposure")
        legs.append(f"🎯 Цель: {code(M.target_words(tgt))}" if tgt is not None else "🎯 Цель: считаю…")
        bars = ["🕒 <b>Последние бары</b> " + it("(UTC · местное)")]
        rows = st.recent_signals(8)
        for bar, key, sg, target in rows:
            d = tx.parse_iso(bar)
            bars.append(f"{code(f'{d:%d.%m %H:%M} · {tx.local(d, self.tz)}')} {esc(key)}: {tx.sig_icon(sg)} {tx.sig_word(sg)}")
        if not rows:
            bars.append(it("пока нет"))
        return Screen(self.compose(self.head("Сигналы"), legs, bars), kb.info_menu("h", "sg"), live=True, view=VIEW_SIGNALS)

    def trades_screen(self, page: int) -> Screen:
        """Закрытые сделки (круг: вход -> выход), а не отдельные исполнения: P/L в $ и % счёта на входе. Сверху — итог за 7 дней."""
        trips, now = self.st.closed_trips(), self.clock()
        pages = max(1, math.ceil(len(trips) / kb.PAGE_SIZE))
        page = max(0, min(page, pages - 1))
        since = (now - timedelta(days=TRADES_DAYS)).isoformat(timespec="seconds")
        week = [t for t in trips if (t["ts_close"] or "") >= since]
        known = [t["pnl"] for t in week if t.get("pnl") is not None]
        head = [f"За {TRADES_DAYS} дней: {b(M.usd(sum(known), 2, True)) if known else '—'} · {len(week)} {_deals(len(week))}"]
        L = []
        for t in trips[page * kb.PAGE_SIZE:(page + 1) * kb.PAGE_SIZE]:
            L += self._trip_lines(t, now)
        if not trips:
            L.append(it("Закрытых сделок пока нет"))
        return Screen(self.compose(self.head("Сделки", extra=f" · стр. {page + 1}/{pages}"), head, L), kb.pager_menu("tx", page, pages))

    def _trip_lines(self, t: dict, now: datetime) -> list[str]:
        d_close, d_open = tx.parse_iso(t["ts_close"]), tx.parse_iso(t["ts_open"])
        pnl, approx = t.get("pnl"), bool(t.get("approx"))
        mark = "≈ " if approx and pnl is not None else ""
        if pnl is None:
            head = "🛑 <b>сумма уточняется</b>"
        else:
            eq0 = t.get("equity_open")
            share = f" ({code(mark + M.pct(pnl / eq0, 1))} счёта)" if eq0 and eq0 > 0 else ""
            head = f"{M.pnl_icon(pnl)} <b>{mark}{esc(M.usd(pnl, 2, True))}</b>{share}"
        qty = f" {code(tx.qty(t['qty']))} BTC" if t.get("qty") else ""
        side = f"{M.arrow(t['side'])} {M.word(t['side'])}{qty}" if t.get("side") in M.WORD else f"сторона неизвестна{qty}"
        px = (f"{code(tx.n(t['entry'], 1))} → {code(tx.n(t['exit'], 1))}" if t.get("exit") and t.get("entry")
              else f"вход {code(tx.n(t['entry'], 1))}" if t.get("entry") else "цены неизвестны")
        held = M.dur((d_close - d_open).total_seconds()) if d_close and d_open else None
        why = tx.oneline(M.reason_human(t.get("reason")), 60) if t.get("reason") else ""
        return [f"{head} · {side}",
                f"    {px}" + (f" · {esc(held)}" if held else ""),
                f"    {code(tx.local(d_close, self.tz, now))} · {it(why)}" if why else f"    {code(tx.local(d_close, self.tz, now))}"]

    def events_screen(self, page: int) -> Screen:
        rows = [r for r in self.st.events(1000) if not r["text"].startswith(NOISE_EVENTS)]
        pages, now = max(1, math.ceil(len(rows) / kb.PAGE_SIZE)), self.clock()
        page = max(0, min(page, pages - 1))
        L = []
        for r in rows[page * kb.PAGE_SIZE:(page + 1) * kb.PAGE_SIZE]:
            d = tx.parse_iso(r["ts"])
            L.append(f"{ICONS.get(r['level'], '•')} {code(tx.local(d, self.tz, now))} · {esc(tx.age(d, now))}")
            L.append(f"    {esc(tx.oneline(r['text'], 140))}")
        if not rows:
            L.append(it("Событий пока нет"))
        return Screen(self.compose(self.head("События", extra=f" · стр. {page + 1}/{pages}"), L), kb.pager_menu("ev", page, pages))
