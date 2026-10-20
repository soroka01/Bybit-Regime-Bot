"""Подтверждения опасных действий: тексты («что изменится»), одноразовый «взвод» с TTL и проверка необходимости. Миксин Confirm."""
from __future__ import annotations

from tradebot.telegram import keyboards as kb
from tradebot.telegram import texts as tx
from tradebot.telegram.common import ENV_MSG_DONE, ENV_MSG_STALE, RESERVE_PCT, WORK_PCT, P
from tradebot.telegram.panel import Screen
from tradebot.telegram.texts import code, esc, it
from tradebot.trading.state import RANGES

CONFIRM_TTL = 60.0
DONE_TTL = 5.0     # столько секунд после успешного подтверждения повторный тап отвечает «Уже выполнено», а не «устарело»
GUARDED = {"lg", "sl", "rk"}  # на mainnet любые изменения требуют подтверждения
BACK = {"lg": "lg", "sl": "sl", "rk": "rk", "tr": "h", "lv": "lv", "pk": "set", "cl": "pos", "pn": "h", "ke": "h"}


class Confirm:
    """Миксин: нужны self.st, self.env, self.mono, self.pending, _stop_risk_lines, _plan_legs, _target, _leg_index, _add_confirm_lines."""

    def _arm(self, key: str, bound: float | tuple | None = None) -> None:
        """Взвести подтверждение на CONFIRM_TTL. bound — абсолютное значение, посчитанное сейчас (для шагов «±1%»): применится
        именно оно, а не заново пересчитанное от настроек, которые могли измениться до нажатия «Подтвердить»."""
        now = self.mono()
        self.pending = {k: t for k, t in self.pending.items() if t > now}
        self.pending[key] = now + CONFIRM_TTL
        self.done.pop(key, None)
        self.bound = {k: v for k, v in self.bound.items() if k in self.pending}
        if bound is None:
            self.bound.pop(key, None)
        else:
            self.bound[key] = bound

    def is_armed(self, key: str) -> bool:
        """Подтверждение взведено и не истекло (без «погашения»)."""
        return self.pending.get(key, 0.0) > self.mono()

    def _consume(self, key: str) -> bool:
        t = self.pending.pop(key, 0.0)
        ok = t > self.mono()
        if ok:
            now = self.mono()
            self.done = {k: u for k, u in self.done.items() if u > now}
            self.done[key] = now + DONE_TTL
        return ok

    def stale_toast(self, key: str) -> str:
        """Текст при непогашаемом подтверждении: только что выполнено (двойной тап) или действительно устарело."""
        return ENV_MSG_DONE if self.done.get(key, 0.0) > self.mono() else ENV_MSG_STALE

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

    @staticmethod
    def _in_range(key: str, label: str, v: float) -> float:
        """Новое значение настройки в допустимом диапазоне (state.RANGES) — иначе понятная ошибка ДО подтверждения, а не сырой
        текст валидации после него."""
        lo, hi = RANGES[key]
        if not (lo - 1e-9 <= v <= hi + 1e-9):
            raise ValueError(f"{label} должен быть от {lo * 100:.0f}% до {hi * 100:.0f}% (получилось бы {v * 100:.0f}%)")
        return v

    @classmethod
    def _stop_new(cls, p: tuple[str, ...], s: dict) -> float:
        v = int(p[2]) / 100 if p[1] == "s" else round(s["stop_pct"] + (0.01 if p[2] == "p1" else -0.01), 4)
        return cls._in_range("stop_pct", "Стоп", v)

    @classmethod
    def _risk_new(cls, p: tuple[str, ...], s: dict) -> tuple[str, float]:
        key = "kill_dd" if p[1] == "dd" else "kill_daily"
        label = "Лимит просадки" if key == "kill_dd" else "Лимит убытка за сутки"
        if p[2] == "s":
            return key, cls._in_range(key, label, int(p[3]) / 100)
        step = 0.05 if p[1] == "dd" else 0.01
        return key, cls._in_range(key, label, round(s[key] + (step if p[2][0] == "p" else -step), 4))
