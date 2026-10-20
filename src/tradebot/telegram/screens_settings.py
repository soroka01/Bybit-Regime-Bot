"""Экраны «Настройки», «Плечо», «Стоп», «Риск-лимиты». Миксин ScreensSettings для Controller."""
from __future__ import annotations

from tradebot.telegram import keyboards as kb
from tradebot.telegram import texts as tx
from tradebot.telegram.common import RESERVE_PCT, WORK_PCT, P
from tradebot.telegram.panel import Screen
from tradebot.telegram.texts import code, it
from tradebot.trading import risk
from tradebot.trading.config import MAX_LEVERAGE


class ScreensSettings:
    """Миксин: нужны self.st, self.compose/head, self._max_exposure (определён здесь)."""

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

    def _settings_labels(self) -> tuple[str, str, str]:
        s = self.st.settings()
        return (f"{s['leverage']:g}x", f"{s['stop_pct'] * 100:.0f}%", f"{s['kill_dd'] * 100:.0f}% / {s['kill_daily'] * 100:.0f}%")

    def settings_screen(self, toast: str | None = None) -> Screen:
        l, sp, r = self._settings_labels()
        eq = self.st.get("equity")
        acct = "💼 Торгуется весь баланс счёта" + (f": {code(tx.n(eq) + ' USDT')}" if eq is not None else "")
        card = [acct, it(f"в работе {WORK_PCT}%, технический резерв {RESERVE_PCT}%: комиссии и дрейф цены"),
                f"⚖️ Плечо: {code(l)}", f"🛡 Стоп: {code(sp)}",
                f"🚨 Риск-лимиты: {code(r)} " + it("(просадка / за сутки)"),
                f"🧩 Стратегии: сумма весов {code(P(self._max_exposure()))}"]
        return Screen(self.compose(self.head("Настройки"), card, self._stop_risk_lines(), it("Нажмите параметр, чтобы изменить.")),
                      kb.settings_menu(l, sp, r), toast)

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
