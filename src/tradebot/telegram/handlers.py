"""Логика меню (Controller — без привязки к aiogram, тестируется с фейками). Controller собирается из миксинов:
экраны (screens_main / screens_settings / screens_legs), расчёт изменений ног (leg_changes), подтверждения (confirm),
добавление/удаление стратегий (legflow); роутер aiogram — router.py. Здесь: состояние, диспетчер callback и действия.
Все экраны рисуются в одно сообщение-панель (см. panel.py) в формате Telegram HTML; карта экранов — в keyboards.py.
Правило безопасности текста: любые динамические данные (причины, события, ошибки, названия) — только через tx.esc/b/code/it."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from tradebot.telegram import keyboards as kb
from tradebot.telegram import messages as M
from tradebot.telegram import texts as tx
from tradebot.telegram.common import (ENV_MSG_STALE, ONBOARDING, PRESET_WEIGHTS, RESERVE_PCT, RETIRED_CAP_MSG,  # noqa: F401  (реэкспорт)
                                      STALE_BUTTON_MSG, TOAST_MAX, WORK_PCT, P, kb_quote)
from tradebot.telegram.confirm import BACK, CONFIRM_TTL, GUARDED, Confirm  # noqa: F401  (реэкспорт)
from tradebot.telegram.leg_changes import LegChange, LegChanges  # noqa: F401  (реэкспорт)
from tradebot.telegram.legflow import LegFlow, is_active  # noqa: F401  (реэкспорт)
from tradebot.telegram.panel import Panel, Screen  # noqa: F401  (реэкспорт)
from tradebot.telegram.router import build_router  # noqa: F401  (реэкспорт)
from tradebot.telegram.screens_legs import ScreensLegs
from tradebot.telegram.screens_main import LEVEL_ICON, NOISE_EVENTS, TRADES_DAYS, ScreensMain, _deals  # noqa: F401  (реэкспорт)
from tradebot.telegram.screens_settings import ScreensSettings
from tradebot.telegram.texts import esc, it
from tradebot.trading import risk
from tradebot.trading.config import DASHBOARD_URL, MAX_LEVERAGE
from tradebot.trading.executor import SIZING_EQUITY_FRACTION, Executor  # noqa: F401  (реэкспорт)
from tradebot.trading.runner import Runner
from tradebot.trading.state import NO_ORDERS_MSG, Store

log = logging.getLogger("tg")


class Controller(ScreensMain, ScreensSettings, ScreensLegs, LegChanges, Confirm, LegFlow):
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
