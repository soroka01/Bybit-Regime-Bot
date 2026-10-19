"""Логика меню (Controller — без привязки к aiogram, тестируется с фейками) и роутер aiogram.
Все экраны рисуются в одно сообщение-панель (см. panel.py) в формате Telegram HTML; карта экранов — в keyboards.py.
Правило безопасности текста: любые динамические данные (причины, события, ошибки, названия) — только через tx.esc/b/code/it."""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from tradebot.core.legspec import params_text
from tradebot.core.portfolio import Leg
from tradebot.telegram import keyboards as kb
from tradebot.telegram import messages as M
from tradebot.telegram import texts as tx
from tradebot.telegram.legflow import LegFlow, is_active
from tradebot.telegram.texts import b, code, esc, it
from tradebot.telegram.panel import Panel, Screen
from tradebot.trading import risk
from tradebot.trading.config import DASHBOARD_URL, MAX_LEVERAGE
from tradebot.trading.executor import SIZING_EQUITY_FRACTION, Executor
from tradebot.trading.runner import Runner
from tradebot.trading.state import NO_ORDERS_MSG, Store

log = logging.getLogger("tg")
CONFIRM_TTL = 60.0
ENV_MSG_STALE = "Подтверждение устарело"
GUARDED = {"lg", "sl", "rk"}  # на mainnet любые изменения требуют подтверждения
BACK = {"lg": "lg", "sl": "sl", "rk": "rk", "tr": "h", "lv": "lv", "pk": "set", "cl": "pos", "pn": "h", "ke": "h"}
LEVEL_ICON = {"info": "ℹ️", "trade": "💱", "warn": "⚠️", "error": "🚨", "signal": "📈", "daily": "📅"}
TRADES_DAYS = 7                  # окно итога на экране «Сделки»
STALE_BUTTON_MSG = "Эта кнопка больше не используется — обновил панель"
NOISE_EVENTS = ("Действую с опозданием", "Размер текущей позиции", "PAPER:", "Целевая экспозиция изменилась — защёлка снята",
                "Сверка:")       # служебные строки старых версий: на экране «События» скрыты
PRESET_WEIGHTS = (0, 25, 50, 75, 100)
TOAST_MAX = 200  # лимит Telegram на текст answerCallbackQuery
RESERVE_PCT = round((1 - SIZING_EQUITY_FRACTION) * 100)       # технический резерв, %: 3
WORK_PCT = round(SIZING_EQUITY_FRACTION * 100)                # в работе, %: 97
RETIRED_CAP_MSG = "Капитал больше не настраивается: бот торгует всем балансом счёта. Панель обновлена."
ONBOARDING = (
    "👋 <b>Бот автоматической торговли BTCUSDT</b> (фьючерс Bybit)\n\n"
    "1️⃣ Каждый час считает сигналы стратегий и определяет целевую позицию.\n"
    "2️⃣ Если торговля включена — приводит позицию на бирже к цели; размер считается от всего баланса счёта "
    f"(технический резерв {RESERVE_PCT}%), на бирже всегда стоит защитный стоп.\n"
    "3️⃣ Всё управление — кнопками в одном сообщении-панели; любое важное действие подтверждается."
)


def _deals(n: int) -> str:
    """Склонение: 1 сделка, 2-4 сделки, 5+ сделок."""
    return "сделка" if n % 10 == 1 and n % 100 != 11 else ("сделки" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "сделок")


def P(x: float) -> str:
    """Доля -> целые проценты: 0.5 -> «50%»."""
    return f"{round(x * 100)}%"


@dataclass
class LegChange:
    """Одно изменение ноги: было (w0, en0) -> станет (w1, en1)."""
    i: int
    leg: Leg
    w0: float
    en0: bool
    w1: float
    en1: bool

    @property
    def noop(self) -> bool:
        return self.en0 == self.en1 and abs(self.w0 - self.w1) < 1e-9

    @property
    def delta(self) -> float:
        """Вклад в сумму весов включённых: нужен для порядка применения (сначала уменьшения)."""
        return (self.w1 if self.en1 else 0.0) - (self.w0 if self.en0 else 0.0)


class Controller(LegFlow):
    confirm_ttl = CONFIRM_TTL

    def __init__(self, store: Store, executor: Executor, runner: Runner, env_name: str, tz_name: str = "Europe/Moscow",
                 clock=None, mono=None):
        self.st, self.ex, self.runner, self.env = store, executor, runner, env_name
        self.tz = tx.tzinfo(tz_name)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.mono = mono or time.monotonic
        self.pending: dict[str, float] = {}  # ключ действия -> срок годности подтверждения
        self._lim: tuple[float, tuple[float, float, float | None] | None] | None = None
        self._init_legflow()

    # ---------- общее ----------
    @property
    def paper(self) -> bool:
        return self.st.paper

    def config_warning(self) -> str | None:
        """Единственное упоминание режима запуска: только если он НЕ боевой (без права на ордера / не основная биржа)."""
        return M.config_warning(self.env, self.paper)

    def head(self, *path: str, extra: str = "") -> str:
        """Заголовок: жирная «хлебная крошка», под ней тонкая линия."""
        crumb = " › ".join(("Главная",) + path)
        if path == () and not extra:
            crumb = "🏠 Главная"
        return f"<b>{esc(crumb)}</b>{esc(extra)}\n{tx.SEP}"

    @staticmethod
    def compose(head: str, *sections: list[str] | str) -> str:
        """Заголовок + секции, разделённые пустой строкой (пустые секции пропускаются)."""
        body = ["\n".join(s) if isinstance(s, list) else s for s in sections]
        return head + "\n" + "\n\n".join(x for x in body if x)

    async def limits(self) -> tuple[float, float, float | None] | None:
        """(minOrderQty, цена, баланс для расчёта размера или None) с короткой кэшированной выборкой; None, если
        биржа недоступна. Баланс — Exchange.sizing_balance() (тот же, от которого бот считает размер)."""
        now = self.mono()
        if self._lim and now - self._lim[0] < 30 and self._lim[1]:
            return self._lim[1]
        try:
            inst, price = await self.ex.ex.instrument(), await self.ex.ex.mark_price()
            try:
                bal = float(await self.ex.ex.sizing_balance())
                bal = bal if bal > 0 else None
            except Exception:
                bal = None
            self._lim = (now, (inst.min_qty, price, bal))
        except Exception:
            self._lim = (now, None)
        return self._lim[1]

    def _stop_risk_lines(self, s: dict | None = None) -> list[str]:
        """Предупреждение «стоп × плечо × веса»: на всём счёте один сработавший стоп уже может съесть лимит kill-switch."""
        s = s or self.st.settings()
        w = self._max_exposure()
        br = risk.stop_risk_breach(s["stop_pct"], s["leverage"], w, s["kill_dd"], s["kill_daily"])
        if br is None:
            return []
        loss, lim, name = br
        stop, lev, loss_t, lim_t = f"{s['stop_pct'] * 100:.0f}%", f"{s['leverage']:g}x", f"{loss * 100:.0f}%", f"{lim * 100:.0f}%"
        return [f"⚠️ <b>Стоп × плечо × веса:</b> стоп {code(stop)} при плече {code(lev)} и весах {code(P(w))} = около "
                f"{code(loss_t)} счёта за одну сделку — это не меньше лимита {name} {code(lim_t)}: "
                f"сначала сработает биржевой стоп, затем kill-switch."]

    def _arm(self, key: str) -> None:
        now = self.mono()
        self.pending = {k: t for k, t in self.pending.items() if t > now}
        self.pending[key] = now + CONFIRM_TTL

    def _consume(self, key: str) -> bool:
        t = self.pending.pop(key, 0.0)
        return t > self.mono()

    # ---------- главный экран ----------
    def home(self, toast: str | None = None) -> Screen:
        st, s, now = self.st, self.st.settings(), self.clock()
        killed, eq, peak, day0 = st.get("killed", False), st.get("equity"), st.get("peak_equity"), st.get("day_start_equity")
        pos, tgt = st.get("position"), st.get("target_exposure")
        if self.paper:
            status = "🚫 <b>Торговля заблокирована</b> — ордера не отправляются"
        elif killed:
            status = "🛑 <b>Стоп-кран</b> — торговля заблокирована"
        else:
            status = "▶️ <b>Торговля включена</b>" if s["enabled"] else "⏸ <b>Торговля выключена</b>"
        warn = self.config_warning()
        top = ([f"<b>{esc(warn)}</b>"] if warn else []) + [status]
        if killed:
            top.append(f"🛑 Причина: {esc(st.get('kill_reason'))}")
        if st.get("latch") and not self.paper:
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
        return Screen(text, kb.home_menu(bool(s["enabled"]), bool(killed), self.paper, bool(st.get("latch"))),
                      toast, live=True, view=("h",))

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
        warn = self.config_warning()
        if warn:
            secs.insert(0, f"<b>{esc(warn)}</b>")
        return Screen(self.compose(self.head("Позиция"), *secs), kb.position_menu(bool(pos) and not self.paper),
                      live=True, view=("pos",))

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
        return Screen(self.compose(self.head("Сигналы"), legs, bars), kb.info_menu("h", "sg"), live=True, view=("sg",))

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
            L.append(f"{LEVEL_ICON.get(r['level'], '•')} {code(tx.local(d, self.tz, now))} · {esc(tx.age(d, now))}")
            L.append(f"    {esc(tx.oneline(r['text'], 140))}")
        if not rows:
            L.append(it("Событий пока нет"))
        return Screen(self.compose(self.head("События", extra=f" · стр. {page + 1}/{pages}"), L), kb.pager_menu("ev", page, pages))

    def _settings_labels(self) -> tuple[str, str, str]:
        s = self.st.settings()
        return (f"{s['leverage']:g}x", f"{s['stop_pct'] * 100:.0f}%", f"{s['kill_dd'] * 100:.0f}% / {s['kill_daily'] * 100:.0f}%")

    def settings_screen(self) -> Screen:
        l, sp, r = self._settings_labels()
        eq = self.st.get("equity")
        acct = "💼 Торгуется весь баланс счёта" + (f": {code(tx.n(eq) + ' USDT')}" if eq is not None else "")
        card = [acct, it(f"в работе {WORK_PCT}%, технический резерв {RESERVE_PCT}%: комиссии и дрейф цены"),
                f"⚖️ Плечо: {code(l)}", f"🛡 Стоп: {code(sp)}",
                f"🚨 Риск-лимиты: {code(r)} " + it("(просадка / за сутки)"),
                f"🧩 Стратегии: сумма весов {code(P(self._max_exposure()))}"]
        return Screen(self.compose(self.head("Настройки"), card, self._stop_risk_lines(), it("Нажмите параметр, чтобы изменить.")),
                      kb.settings_menu(l, sp, r))

    def _max_exposure(self) -> float:
        return sum(w for _, w, en in self.st.leg_cfg() if en)

    def leverage_screen(self, toast: str | None = None) -> Screen:
        s = self.st.settings()
        lev, sp = s["leverage"], s["stop_pct"] * 100
        main = [f"⚖️ Плечо: {code(f'{lev:g}x')} " + it(f"(максимум {MAX_LEVERAGE}x, жёсткий предел)")]
        w = self._max_exposure()
        move = f"{round(lev * w, 1):g}%"
        info = [f"Движение цены на 1% меняет счёт примерно на {code(move)} " + it(f"(плечо × сумма весов {P(w)})"),
                f"Ликвидация примерно при {code(f'−{100 / lev:.0f}%')} цены; стоп {sp:.0f}% срабатывает раньше."]
        if sp >= 100 / lev * 0.8:
            info.append("⚠️ <b>Стоп слишком далёк для этого плеча</b> — уменьшите стоп.")
        return Screen(self.compose(self.head("Настройки", "Плечо"), main, info, self._stop_risk_lines(s),
                                   it("Смена плеча подтверждается. Размер позиции пересчитается от баланса счёта.")),
                      kb.leverage_menu(lev), toast)

    def stop_screen(self, toast: str | None = None) -> Screen:
        s = self.st.settings()
        sp, lev = s["stop_pct"] * 100, s["leverage"]
        main = [f"🛡 Стоп: {code(f'{sp:.0f}%')} от цены входа " + it("(на бирже)")]
        w = self._max_exposure()
        loss = f"{sp * lev * w:.0f}%"
        info = [f"При плече {lev:g}x и весах {code(P(w))} убыток по стопу ≈{code(loss)} счёта.",
                it("Срабатывает на бирже сам, даже если бот выключен.")]
        return Screen(self.compose(self.head("Настройки", "Стоп"), main, info, self._stop_risk_lines(s)),
                      kb.stop_menu(round(sp)), toast)

    def risk_screen(self, toast: str | None = None) -> Screen:
        s = self.st.settings()
        dd, da = s["kill_dd"] * 100, s["kill_daily"] * 100
        main = [f"📉 Просадка от пика: {code(f'{dd:.0f}%')}", f"📆 Убыток за сутки (UTC): {code(f'{da:.0f}%')}"]
        info = [it("При достижении любого лимита бот закроет позицию и выключит торговлю (стоп-кран)."),
                it("Считается от equity всего счёта Bybit (пик и начало суток). После пополнения или вывода денег "
                   "нажмите «Сбросить пик».")]
        return Screen(self.compose(self.head("Настройки", "Риск-лимиты"), main, info, self._stop_risk_lines(s)),
                      kb.risk_menu(round(dd), round(da)), toast)

    # ---------- стратегии ----------
    def _target(self, cfg: list[tuple[Leg, float, bool]], extra: dict[str, float | None] | None = None) -> float | None:
        """Цель = Σ сигнал × вес по включённым ногам с весом > 0; None, если сигнал какой-то из них неизвестен.
        extra — сигналы, которых ещё нет в журнале (новая нога: берётся из проверки окна)."""
        sigs, t = self.st.latest_signals(), 0.0
        for leg, w, en in cfg:
            if not en or w <= 0:
                continue
            sg = extra[leg.key] if extra and leg.key in extra else sigs.get(leg.key, (None,))[0]
            if sg is None:
                return None
            t += sg * w
        return t

    def _leg_idx(self, raw: str) -> int:
        i = int(raw)
        if i >= len(self.st.leg_cfg()):
            raise ValueError("нет такой стратегии")
        return i

    @staticmethod
    def _small_order(s: dict, lim, w: float) -> float | None:
        """Максимальная позиция ноги (BTC) от ТЕКУЩЕГО баланса счёта, если она меньше минимального ордера биржи; иначе None.
        Баланс неизвестен (биржа не ответила) — предупреждения нет."""
        if not lim or lim[2] is None or w <= 0:
            return None
        order = lim[2] * SIZING_EQUITY_FRACTION * s["leverage"] * w / lim[1]
        return order if order < lim[0] else None

    async def legs_screen(self, toast: str | None = None) -> Screen:
        st, s = self.st, self.st.settings()
        cfg, sigs, lim = st.leg_cfg(), st.latest_signals(), await self.limits()
        L, idle, warn = [], [], []
        for leg, w, en in cfg:
            sg = sigs.get(leg.key, (None,))[0]
            if is_active(w, en):
                L.append(f"{kb.leg_icon(en, w)} <b>{esc(leg.strategy)}</b> · {esc(leg.tf)}")
                L.append(f"    вес {code(P(w))} · сигнал: {tx.sig_icon(sg)} {tx.sig_word(sg)}")
            else:
                idle.append(f"{kb.leg_icon(en, w)} {esc(leg.strategy)} · {esc(leg.tf)} — {'выключена' if not en else 'не торгуется (вес 0%)'}")
            if en and (o := self._small_order(s, lim, w)) is not None:
                warn.append(f"⚠️ {esc(leg.strategy)} · {esc(leg.tf)}: на счёте {tx.n(lim[2], 0)} USDT (в работе {WORK_PCT}%) "
                            f"позиция {code(tx.qty(o))} BTC меньше минимального ордера {code(tx.qty(lim[0]))} BTC")
        if not L:
            L.append(it("Нет работающих стратегий — цель 0, бот вне рынка."))
        total = self._max_exposure()
        free = max(0.0, 1.0 - total)
        intro = it("Вес — доля счёта, которой управляет стратегия. Сумма весов ≤ 100%.")
        summary = [f"📊 Распределено {code(P(total))} · свободно {code(P(free))}"]
        parts: list = [intro, L]
        if idle:
            parts.append([f"💤 <b>Неактивные ({len(idle)})</b>"] + idle)
        parts += [summary, warn, [it("➕ Добавить: пришлите сюда текст с сайта («Копировать для бота») или нажмите «В бота» на сайте.")]]
        return Screen(self.compose(self.head("Стратегии"), *parts),
                      kb.legs_menu([(f"{l.strategy} · {l.tf}", w, en) for l, w, en in cfg]), toast)

    async def leg_screen(self, i: int, toast: str | None = None) -> Screen:
        st, s = self.st, self.st.settings()
        cfg, sigs, lim = st.leg_cfg(), st.latest_signals(), await self.limits()
        if not 0 <= i < len(cfg):
            raise ValueError("нет такой стратегии")
        leg, w, en = cfg[i]
        sg = sigs.get(leg.key, (None,))[0]
        name = f"{leg.strategy} · {leg.tf}"
        others = sum(w2 for j, (_, w2, en2) in enumerate(cfg) if j != i and en2)
        cap_pct = max(0, math.floor((1.0 - others) * 100 + 1e-6))      # максимум веса этой ноги
        cur = round(w * 100)
        total = self._max_exposure()
        desc = self._desc(leg)
        if not en:
            status = f"⚪ <b>Выключена</b> · вес {code(P(w))}"
            signal = it("Сигнал не учитывается, пока стратегия выключена.")
        elif w <= 0:
            status = f"⏸ <b>Включена, но не торгуется</b> · вес {code(P(w))}"
            signal = it("Сигнал не считается, пока вес 0%.")
        else:
            status = f"🟢 <b>Включена</b> · вес {code(P(w))}"
            signal = (f"📡 Сигнал: {tx.sig_icon(sg)} {tx.sig_word(sg)}" +
                      (f" · вклад в цель {code(tx.sn(sg * w))}" if sg is not None else ""))
        room = [f"📏 Можно до {code(f'{cap_pct}%')} " + it(f"(в портфеле свободно {P(max(0.0, 1.0 - total))})")]
        warn = []
        if en and (o := self._small_order(s, lim, w)) is not None:
            warn.append(f"⚠️ На счёте {tx.n(lim[2], 0)} USDT (в работе {WORK_PCT}%) позиция этой стратегии {code(tx.qty(o))} BTC "
                        f"меньше минимального ордера {code(tx.qty(lim[0]))} BTC")
        can_toggle = en or others + w <= 1.0 + 1e-9
        if not en and not can_toggle:
            over = math.ceil((others + w - 1.0) * 100 - 1e-6)
            warn.append(it(f"Чтобы включить, уменьшите вес других стратегий на {over}%."))
        if en and w <= 0 and cap_pct <= 0:
            warn.append(it("Чтобы дать этой стратегии вес, сначала уменьшите вес другой: сумма не может быть больше 100%."))
        presets = sorted({p for p in PRESET_WEIGHTS if p <= cap_pct} | {cur})
        menu = kb.leg_menu(i, cur, en, presets, can_minus=cur >= 10, can_plus=cur + 10 <= cap_pct, can_toggle=can_toggle)
        secs = [[f"⚙️ {code(params_text(leg.params))} · {'только лонг' if leg.long_only else 'лонг и шорт'}"], kb_quote(desc), [status, signal], room, warn]
        return Screen(self.compose(self.head("Стратегии", name), *secs), menu, toast)

    # --- расчёт изменений ног (общий для подтверждения и применения) ---
    def _leg_index(self, p: tuple[str, ...]) -> int | None:
        """Индекс ноги из callback (новый lg:<i>… и старый lg:t:<i>/lg:w:<i>:<p|m>); None для lg / lg:eq."""
        if len(p) < 2 or p[0] != "lg" or p[1] == "eq":
            return None
        return self._leg_idx(p[2] if p[1] in ("t", "w") else p[1])

    @staticmethod
    def _is_leg_change(p: tuple[str, ...]) -> bool:
        if p[0] != "lg" or len(p) < 2:
            return False
        return p[1] in ("eq", "t", "w") or (len(p) >= 3 and p[2] in ("t", "w"))

    def _leg_changes(self, p: tuple[str, ...]) -> list[LegChange]:
        """Что изменится по callback (без :y). Проверяет индекс и границы веса, но не сумму."""
        cfg = self.st.leg_cfg()
        if p[1] == "eq":
            act = [(i, l, w, en) for i, (l, w, en) in enumerate(cfg) if en and w > 0]
            if not act:
                raise ValueError("нет включённых стратегий с весом больше 0% — сначала задайте вес")
            each = math.floor(100 / len(act) + 1e-9) / 100          # целые проценты вниз: сумма ≤ 100%
            return [LegChange(i, l, w, en, each, en) for i, l, w, en in act]
        i = self._leg_index(p)
        leg, w, en = cfg[i]
        if p[1] == "t":                                              # старый lg:t:<i>
            return [LegChange(i, leg, w, en, w, not en)]
        if p[1] == "w":                                              # старый lg:w:<i>:<p|m>
            return [LegChange(i, leg, w, en, round(w + (0.1 if p[3] == "p" else -0.1), 2), en)]
        if p[2] == "t":
            return [LegChange(i, leg, w, en, w, not en)]
        return [LegChange(i, leg, w, en, int(p[3]) / 100, en)]       # lg:<i>:w:<pct>

    def _plan_legs(self, p: tuple[str, ...]) -> list[LegChange]:
        """_leg_changes + проверка правил state.set_leg с понятным сообщением (что уменьшить)."""
        changes = self._leg_changes(p)
        for c in changes:
            if c.w1 < -1e-9:
                raise ValueError("вес не может быть меньше 0%")
            if c.w1 > 1.0 + 1e-9:
                raise ValueError("вес не может быть больше 100%")
        cfg = self.st.leg_cfg()
        new = {c.leg.key: (c.w1, c.en1) for c in changes}
        total = sum(new.get(l.key, (w, en))[0] for l, w, en in cfg if new.get(l.key, (w, en))[1])
        if total > 1.0 + 1e-9:
            over = math.ceil((total - 1.0) * 100 - 1e-6)
            c = changes[0]
            others = sum(w for l, w, en in cfg if en and l.key != c.leg.key)
            cap = max(0, math.floor((1.0 - others) * 100 + 1e-6))
            nm = f"{c.leg.strategy} · {c.leg.tf}"
            if not c.en0 and c.en1:
                raise ValueError(f"сумма весов стала бы {total:.0%} (максимум 100%). Чтобы включить «{nm}», "
                                 f"уменьшите вес других стратегий на {over}%")
            raise ValueError(f"сумма весов стала бы {total:.0%} (максимум 100%). Уменьшите вес других стратегий на "
                             f"{over}% или задайте «{nm}» не больше {cap}%")
        return changes

    def _apply_legs(self, changes: list[LegChange]) -> None:
        """Сначала уменьшения суммы, потом увеличения: промежуточная сумма никогда не превышает 100%."""
        for c in sorted(changes, key=lambda c: c.delta):
            if c.noop:
                continue
            self.st.set_leg(c.leg.key, weight=None if abs(c.w0 - c.w1) < 1e-9 else c.w1,
                            enabled=None if c.en0 == c.en1 else c.en1)

    # ---------- подтверждения ----------
    def _confirm_lines(self, p: tuple[str, ...]) -> list[str]:
        s = self.st.settings()
        a = p[0]
        if a == "tr":
            eq = self.st.get("equity")
            acct = ("весь счёт" + (" " + code(tx.n(eq) + " USDT") if eq is not None else "") +
                    f" (в работе {WORK_PCT}%, технический резерв {RESERVE_PCT}%)")
            return ["<b>Включить торговлю?</b>", "Бот начнёт открывать ордера:",
                    "• " + acct + " · плечо " + code(f"{s['leverage']:g}x") +
                    " · стоп " + code(f"{s['stop_pct'] * 100:.0f}%"), "• Торговля: ⏸ выключена → ▶️ включена",
                    *self._stop_risk_lines(s), it("Позиция будет сразу сверена с целью; размер считается от баланса счёта.")]
        if a == "lv":
            return ["<b>Сменить плечо?</b>", f"• Плечо <b>{s['leverage']:g}x → {esc(p[2])}x</b>",
                    it("Размер позиции пересчитается от баланса счёта при ближайшей сверке.")]
        if a == "pk":
            return ["<b>Сбросить пик equity?</b>", "• Пик, база дня и база P/L станут равны текущему equity",
                    it("Отсчёт просадки и дневного убытка начнётся заново (нужно после пополнения счёта).")]
        if a == "cl":
            return ["<b>Закрыть позицию рыночным ордером?</b>", "• Торговля останется включённой",
                    it("Повторный вход заблокирован до смены сигнала.")]
        if a == "pn":
            return ["🛑 <b>ПАНИКА</b>", "• Позиция будет закрыта рыночным ордером", "• Торговля будет выключена"]
        if a == "ke":
            return ["<b>Снять блокировку?</b>", f"Причина: {esc(self.st.get('kill_reason'))}", "• Торговля включится снова",
                    it("Если лимиты всё ещё нарушены, сначала сбросьте пик.")]
        if a == "sl":
            return ["<b>Изменить стоп?</b>",
                    f"• Стоп <b>{s['stop_pct'] * 100:.0f}% → {self._stop_new(p, s) * 100:.0f}%</b>",
                    it("Защитный стоп на бирже будет переставлен при ближайшей сверке.")]
        if a == "rk":
            key, new = self._risk_new(p, s)
            label = "Просадка от пика" if key == "kill_dd" else "Убыток за сутки"
            return ["<b>Изменить риск-лимит?</b>", f"• {label} <b>{s[key] * 100:.0f}% → {new * 100:.0f}%</b>"]
        if a == "lg":
            return self._leg_confirm_lines(p)
        if a == "ad":
            return self._add_confirm_lines(p)
        return ["<b>Подтвердите изменение</b>", esc(" ".join(p))]

    def _leg_confirm_lines(self, p: tuple[str, ...]) -> list[str]:
        changes = self._plan_legs(p)
        cfg = self.st.leg_cfg()
        new = {c.leg.key: (c.w1, c.en1) for c in changes}
        cfg2 = [(l, *new.get(l.key, (w, en))) for l, w, en in cfg]
        if p[1] == "eq":
            title = "Выровнять веса поровну?"
        elif len(changes) == 1 and changes[0].en0 != changes[0].en1:
            title = "Включить стратегию?" if changes[0].en1 else "Выключить стратегию?"
        else:
            title = "Изменить вес стратегии?"
        L = [f"<b>{title}</b>"]
        for c in changes:
            bits = []
            if abs(c.w0 - c.w1) > 1e-9:
                bits.append(f"<b>вес {P(c.w0)} → {P(c.w1)}</b>")
            if c.en0 != c.en1:
                bits.append("<b>включена</b>" if c.en1 else "<b>выключена</b>")
            L.append(f"{kb.leg_icon(c.en1, c.w1)} {esc(c.leg.strategy)} · {esc(c.leg.tf)}: " + (", ".join(bits) or "без изменений"))
        t0 = sum(w for _, w, en in cfg if en)
        t1 = sum(w for _, w, en in cfg2 if en)
        L.append(f"• Сумма весов <b>{P(t0)} → {P(t1)}</b>")
        g0, g1 = self._target(cfg), self._target(cfg2)
        L.append(f"• Цель сейчас <b>{tx.sn(g0)} → {tx.sn(g1)}</b>" if g0 is not None and g1 is not None
                 else "• Цель: " + it("пересчитается"))
        L.append(it("Позиция пересчитается при ближайшей сверке."))
        return L

    def _cancel_target(self, p: tuple[str, ...]) -> str:
        if p[0] == "lg":
            try:
                i = self._leg_index(p)
            except ValueError:
                i = None
            return "lg" if i is None else f"lg:{i}"
        if p[0] == "ad":
            return f"ad:{p[1]}"
        return BACK[p[0]]

    def _confirm_screen(self, key: str, p: tuple[str, ...]) -> Screen:
        text = self.compose(self.head("Подтверждение"), self._confirm_lines(p), it(f"(действует {CONFIRM_TTL:.0f} с)"))
        return Screen(text, kb.confirm(key + ":y", self._cancel_target(p)))

    def _needs_confirm(self, p: tuple[str, ...]) -> bool:
        a = p[0]
        if a in ("pk", "cl", "pn", "ke") and len(p) == 1:
            return True
        if a == "tr" and p[1] == "on" and len(p) == 2:
            return True
        if a == "lv" and len(p) == 3:
            return True
        if a == "lg":
            return self.env == "mainnet" and self._is_leg_change(p)
        if a == "ad":
            return self._ad_needs_confirm(p)
        return self.env == "mainnet" and a in GUARDED and len(p) >= 3

    # ---------- диспетчер ----------
    async def handle(self, parts: tuple[str, ...]) -> Screen:
        """parts — результат parse_cb (белый список). Любая ошибка валидации -> экран + toast «Отклонено»."""
        try:
            return await self._handle(parts)
        except ValueError as e:
            try:
                if parts[0] == "lg":
                    i = self._leg_index(parts[:-1] if parts[-1] == "y" else parts)
                    s = await (self.legs_screen() if i is None else self.leg_screen(i))
                elif parts[0] == "ad":
                    s = self.add_screen()
                else:
                    s = self.home()
            except ValueError:
                s = self.home()
            s.toast = tx.trunc(f"Отклонено: {e}", TOAST_MAX)
            s.alert = True
            return s

    async def _handle(self, p: tuple[str, ...]) -> Screen:
        st, a = self.st, p[0]
        if self.paper and (p[:2] == ("tr", "on") or a == "ke"):
            raise ValueError(NO_ORDERS_MSG)
        if self._is_rm(p):
            return await self._handle_rm(p)
        if a == "ad" and len(p) >= 3 and p[2] in ("w", "eq") and p[-1] != "y":
            await self.ensure_check(int(p[1]))          # текст подтверждения берёт сигнал и окно из проверки
        if p[-1] == "y":
            key = ":".join(p[:-1])
            if not self._consume(key):
                return self.home(ENV_MSG_STALE)
            return await self._dispatch(p[:-1])
        if self._needs_confirm(p):
            if a == "tr" and st.get("killed", False):
                raise ValueError("сработал kill-switch: используйте «Снять блокировку»")
            if a == "ke" and not st.get("killed", False):
                return self.home("Блокировка не активна")
            if a == "lg" and all(c.noop for c in self._plan_legs(p)):   # нечего подтверждать
                return await self._leg_result(p, "Без изменений")
            key = ":".join(p)
            screen = self._confirm_screen(key, p)                        # сначала проверка/текст, потом «взвод» подтверждения
            self._arm(key)
            return screen
        return await self._dispatch(p)

    async def _leg_result(self, p: tuple[str, ...], toast: str) -> Screen:
        i = self._leg_index(p)
        return await (self.legs_screen(toast) if i is None else self.leg_screen(i, toast))

    @staticmethod
    def _stop_new(p: tuple[str, ...], s: dict) -> float:
        return int(p[2]) / 100 if p[1] == "s" else round(s["stop_pct"] + (0.01 if p[2] == "p1" else -0.01), 4)

    @staticmethod
    def _risk_new(p: tuple[str, ...], s: dict) -> tuple[str, float]:
        key = "kill_dd" if p[1] == "dd" else "kill_daily"
        if p[2] == "s":
            return key, int(p[3]) / 100
        step = 0.05 if p[1] == "dd" else 0.01
        return key, round(s[key] + (step if p[2][0] == "p" else -step), 4)

    async def _dispatch(self, p: tuple[str, ...]) -> Screen:
        st, a = self.st, p[0]
        s = st.settings()
        if a == "h":
            return self.home()
        if a == "pos":
            return await self.position_screen()
        if a == "np":  # из уведомления: показать позицию в панели
            return await self.position_screen()
        if a == "sg":
            return self.signals_screen()
        if a == "tx":
            return self.trades_screen(int(p[1]))
        if a == "ev":
            return self.events_screen(int(p[1]))
        if a == "set":
            return self.settings_screen()
        if a == "lt":
            body = [it("Позиция закрылась по стопу (или вручную), а сигнал не менялся. Чтобы не входить обратно в то же "
                       "движение, бот ждёт, пока целевая позиция изменится."), "Защёлка снимется сама."]
            return Screen(self.compose(self.head("Защёлка"), body), kb.info_menu("h", "lt"))
        if a == "tr":  # tr:off или подтверждённое tr:on
            if p[1] == "off":
                st.set_setting("enabled", False)
                return self.home("Торговля выключена (открытая позиция остаётся под биржевым стопом)")
            if st.get("killed", False):
                raise ValueError("сработал kill-switch: используйте «Снять блокировку»")
            st.set_setting("enabled", True)
            st.clear_acted()                      # размер заново от свежего баланса счёта
            self.runner.trigger()
            return self.home("Торговля включена")
        if a == "ad":
            return await self._dispatch_ad(p)
        if a == "lg":
            if len(p) == 1:
                return await self.legs_screen()
            if len(p) == 2 and p[1].isdigit():
                return await self.leg_screen(self._leg_idx(p[1]))
            changes = self._plan_legs(p)
            if all(c.noop for c in changes):
                return await self._leg_result(p, "Без изменений")
            self._apply_legs(changes)
            self.runner.trigger()
            return await self._leg_result(p, "Сохранено; пересчёт цели запущен")
        if a == "lv":
            if len(p) == 1:
                return self.leverage_screen()
            n = int(p[2])
            if not 1 <= n <= MAX_LEVERAGE:
                raise ValueError("плечо вне диапазона")
            # одна операция под торговым lock: сохранить -> сбросить acted_* -> применить на бирже; не вышло — откат к прежнему плечу
            ok = await self.ex.set_leverage(n)
            self.runner.trigger()
            return self.leverage_screen("Плечо изменено" if ok else "Биржа не подтвердила новое плечо — оставлено прежнее")
        if a == "sl":
            if len(p) == 1:
                return self.stop_screen()
            st.set_setting("stop_pct", self._stop_new(p, s))
            self.runner.trigger()
            return self.stop_screen("Сохранено")
        if a == "rk":
            if len(p) == 1:
                return self.risk_screen()
            key, new = self._risk_new(p, s)
            st.set_setting(key, new)
            return self.risk_screen("Сохранено")
        if a == "pk":
            eq = await self.ex.ex.equity()
            st.set("peak_equity", eq)
            st.set("day_start_equity", eq)
            st.set("baseline_equity", eq)
            st.add_event("info", f"Пик эквити сброшен на {eq:.2f}")
            return self.settings_screen_toast("Пик сброшен")
        if a == "cl":
            r = await self.ex.close_all("закрытие из Telegram")
            return self.home(r)
        if a == "pn":
            r = await self.ex.panic()
            return self.home(f"Паника: {r}")
        if a == "ke":
            return await self._reenable()
        raise ValueError("неизвестное действие")

    def settings_screen_toast(self, toast: str) -> Screen:
        s = self.settings_screen()
        s.toast = toast
        return s

    async def _reenable(self) -> Screen:
        st = self.st
        s, eq = st.settings(), await self.ex.ex.equity()
        reason = risk.check_kill(eq, st.get("peak_equity"), eq, s["kill_dd"], s["kill_daily"])
        if reason:
            raise ValueError(f"лимит всё ещё нарушен ({reason}); сначала «Сбросить пик» в Настройках")
        st.set("day_start_equity", eq)
        st.set("baseline_equity", eq)
        st.set("killed", False)
        st.set("kill_reason", None)
        st.set_setting("enabled", True)
        st.clear_acted()
        self.runner.trigger()
        return self.home("Торговля включена после kill-switch")


def kb_quote(desc: str) -> str:
    """Описание стратегии в <blockquote>; пустое — пропускается."""
    return tx.bq(esc(desc)) if desc else ""


def build_router(ctl: Controller, panel: Panel) -> Router:
    r = Router()

    async def open_panel(m: Message, onboarding: bool) -> None:
        panel.touch()
        if onboarding and not ctl.st.get("onboarded", False):
            warn = ctl.config_warning()
            hint = "Торговля выключена — включите её кнопкой, когда будете готовы."
            await m.answer(f"{ONBOARDING}\n\n" + (f"<b>{esc(warn)}</b>\n" if warn else "") + f"<i>{hint}</i>", parse_mode="HTML")
            ctl.st.set("onboarded", True)
        await panel.open(m.bot, m.chat.id, ctl.home())

    @r.message(Command("start"))
    async def start(m: Message):
        await open_panel(m, True)

    @r.message(Command("menu"))
    async def menu(m: Message):
        await open_panel(m, False)

    @r.message(F.text.contains("Leg("))
    async def leg_text(m: Message):
        """Текст «Копировать для бота» с сайта: разбираем, проверяем окно, показываем карточку в панели."""
        panel.touch()
        try:
            ids, errors = ctl.propose_from_text(m.text or "")
        except ValueError as e:
            ids, errors = [], [str(e)]
        try:
            await m.delete()                 # панель одна: присланный текст в чате не оставляем
        except Exception:
            pass
        if not ids:
            await panel.show(m.bot, m.chat.id, ctl.add_screen(error="; ".join(errors)))
            return
        await panel.show(m.bot, m.chat.id, ctl.checking_screen(len(ids)))
        try:
            for pid in ids:
                await ctl.ensure_check(pid)
            s = await ctl.draft_screen(ids[0]) if len(ids) == 1 else ctl.add_screen(toast=f"Предложений: {len(ids)}")
            if errors:
                s.toast = tx.trunc("Не принято: " + "; ".join(errors), TOAST_MAX)
        except Exception as e:
            log.exception("leg_text failed")
            s = ctl.add_screen(error=f"не удалось подготовить карточку: {type(e).__name__}")
        await panel.show(m.bot, m.chat.id, s)

    @r.callback_query()
    async def cb(q: CallbackQuery):
        chat_id = q.message.chat.id if q.message else q.from_user.id
        msg_id = q.message.message_id if q.message else 0
        panel.touch()
        parts = kb.parse_cb(q.data)
        stale = not panel.is_panel(chat_id, msg_id)
        notice = stale and parts is not None and parts[0] == "ad" and len(parts) == 2     # «Открыть» под уведомлением с сайта
        try:
            if parts is None or (stale and parts != ("np",) and not notice):
                s = await ctl.handle(("h",))
                toast = "Неизвестная кнопка — обновил панель" if parts is None else "Это старое меню — обновил панель"
                if parts is None and isinstance(q.data, str) and q.data.split(":")[0] == "cap":
                    toast = RETIRED_CAP_MSG       # кнопка из старого сообщения: настройка капитала удалена
                elif parts is None and q.data == "pp":
                    toast = STALE_BUTTON_MSG      # кнопка удалённого экрана о режиме запуска
            else:
                s = await ctl.handle(parts)
                toast = s.toast
        except Exception as e:
            log.exception("handler failed")
            await q.answer(f"Ошибка: {type(e).__name__}", show_alert=True)
            return
        await q.answer(toast, show_alert=s.alert)
        await panel.show(q.bot, chat_id, s)
        if notice and q.message:
            try:
                await q.message.delete()     # уведомление своё дело сделало: карточка уже в панели
            except Exception:
                pass

    return r
