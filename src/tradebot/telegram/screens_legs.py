"""Экраны «Стратегии» (список) и карточка ноги. Миксин ScreensLegs для Controller."""
from __future__ import annotations

import math

from tradebot.core.legspec import params_text
from tradebot.telegram import keyboards as kb
from tradebot.telegram import texts as tx
from tradebot.telegram.common import PRESET_WEIGHTS, WORK_PCT, P, kb_quote
from tradebot.telegram.legflow import is_active
from tradebot.telegram.panel import Screen
from tradebot.telegram.texts import code, esc, it
from tradebot.trading.executor import SIZING_EQUITY_FRACTION


class ScreensLegs:
    """Миксин: нужны self.st, self.limits(), self._desc, self._max_exposure, self.compose/head."""

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
