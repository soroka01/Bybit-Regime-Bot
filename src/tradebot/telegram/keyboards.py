"""Inline-клавиатуры и строгая серверная валидация callback_data (белый список шаблонов).

КАРТА ЭКРАНОВ (callback -> экран). Все экраны редактируют ОДНО сообщение-панель; «[авто]» = автообновление каждые 3 с.

  h            Главная [авто]  (дашборд: торговля, счёт, позиция, цель, сигналы, след. бар, пульс)
  |-- tr:on / tr:on:y / tr:off      включить (2 шага, подтверждение 60 с) / выключить
  |-- ke / ke:y                     снять блокировку kill-switch (2 шага)
  |-- lt                            «Защёлка»: что это
  |-- pos [авто]                    Позиция
  |     `-- cl / cl:y               закрыть позицию (2 шага)
  |-- sg [авто]                     Сигналы по ногам + последние бары
  |-- tx:<стр 0-9>                  Сделки: закрытые сделки с P/L в $ (постранично)
  |-- ev:<стр 0-9>                  События (постранично)
  |-- lg                            Стратегии: список (одна кнопка на стратегию; работающие выше неактивных)
  |     |-- ad                      «Добавить стратегию»: как прислать текст с сайта + ожидающие предложения
  |     |     `-- ad:<id>           черновик: проверка окна, вес (пресеты в пределах свободного места)
  |     |           |-- ad:<id>:w:<0..100> / ...:y   добавить с весом (2 шага; 0 — без веса, без подтверждения)
  |     |           |-- ad:<id>:eq / ...:y           добавить и выровнять веса поровну со всеми работающими
  |     |           `-- ad:<id>:x                    отклонить
  |     |-- lg:<i>                  экран одной стратегии (i = индекс в Store.leg_cfg(), 0-9)
  |     |     |-- lg:<i>:t / lg:<i>:t:y            включить/выключить (2 шага)
  |     |     |-- lg:<i>:w:<0..100> / ...:y        вес в процентах (абсолютное значение; ±10% тоже абсолютные)
  |     |     `-- lg:<i>:rm / lg:<i>:rm:y          удалить стратегию (всегда с подтверждением)
  |     `-- lg:eq / lg:eq:y         «Поровну»: выровнять веса работающих стратегий (2 шага)
  |-- set                           Настройки (бот торгует ВСЕМ балансом счёта)
  |     |-- lv                      Плечо
  |     |     `-- lv:s:<1..MAX> / lv:s:<n>:y         пресет (2 шага)
  |     |-- sl                      Стоп
  |     |     |-- sl:s:<3|5|8|10|15>                 пресет
  |     |     `-- sl:a:<p|m>1                        шаг ±1%
  |     |-- rk                      Риск-лимиты
  |     |     |-- rk:dd:s:<10..40> / rk:da:s:<3..20> пресеты (просадка / за день)
  |     |     `-- rk:dd:<p|m>5 / rk:da:<p|m>1        шаги
  |     `-- pk / pk:y               сбросить пик (2 шага)
  |-- pn / pn:y                     ПАНИКА (2 шага)
  `-- np                            из уведомления о сделке: открыть «Позиция» в панели

  Суффикс «:y» = подтверждение. Подтверждение живёт 60 с (серверная память), иначе «Подтверждение устарело».
  Подтверждение требуют также любые изменения lg / sl / rk и добавление стратегии с весом > 0.
  Кнопки, которых нет в белом списке (в том числе из очень старых сообщений), панель встречает общей подсказкой
  «Неизвестная кнопка — обновил панель».
  Кнопка «Открыть» (ad:<id>) в уведомлении «предложение с сайта» работает и из этого сообщения: панель сама покажет черновик.
"""
from __future__ import annotations

import re

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup

from tradebot.trading.config import MAX_LEVERAGE

STOP_PRESETS = (3, 5, 8, 10, 15)
DD_PRESETS = (10, 15, 20, 25, 30, 40)
DAILY_PRESETS = (3, 5, 10, 15, 20)
PAGE_SIZE = 6
MAX_PAGE = 9


def _alt(vals) -> str:
    return "(?:" + "|".join(str(v) for v in vals) + ")"


_LEV = f"[1-{MAX_LEVERAGE}]"
_Y = r"(?::y)?"
PATTERNS = [re.compile(p) for p in (
    r"h", r"pos", r"np", r"lt", r"sg", r"set", rf"tx:[0-{MAX_PAGE}]", rf"ev:[0-{MAX_PAGE}]",
    r"tr:on", r"tr:on:y", r"tr:off", r"ke", r"ke:y",
    r"lg", r"lg:[0-9]", rf"lg:[0-9]:t{_Y}", rf"lg:[0-9]:w:(?:100|[1-9]?[0-9]){_Y}", rf"lg:eq{_Y}", rf"lg:[0-9]:rm{_Y}",
    r"ad", r"ad:[0-9]{1,6}", r"ad:[0-9]{1,6}:x", rf"ad:[0-9]{{1,6}}:w:(?:100|[1-9]?[0-9]){_Y}", rf"ad:[0-9]{{1,6}}:eq{_Y}",
    r"lv", rf"lv:s:{_LEV}", rf"lv:s:{_LEV}:y",
    r"sl", rf"sl:s:{_alt(STOP_PRESETS)}{_Y}", rf"sl:a:[pm]1{_Y}",
    r"rk", rf"rk:dd:s:{_alt(DD_PRESETS)}{_Y}", rf"rk:da:s:{_alt(DAILY_PRESETS)}{_Y}",
    rf"rk:dd:[pm]5{_Y}", rf"rk:da:[pm]1{_Y}",
    r"pk", r"pk:y", r"cl", r"cl:y", r"pn", r"pn:y",
)]


def parse_cb(data: str | None) -> tuple[str, ...] | None:
    """Разбор callback_data; None, если не из белого списка (или слишком длинная)."""
    if not isinstance(data, str) or len(data) > 64 or not data.isascii():
        return None
    return tuple(data.split(":")) if any(p.fullmatch(data) for p in PATTERNS) else None


def b(text: str, data: str) -> B:
    assert parse_cb(data) is not None, data  # опечатки ловим сразу
    return B(text=text, callback_data=data)


def _kb(rows: list[list[B]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def nav(back: str, refresh: str) -> list[B]:
    """Единая нижняя строка подэкрана: назад / главная / обновить."""
    return [b("⬅️ Назад", back), b("🏠 Главная", "h"), b("↻ Обновить", refresh)]


def _mark(label: str, current: bool) -> str:
    return f"✅ {label}" if current else label


def _chunks(items: list[B], k: int) -> list[list[B]]:
    return [items[i:i + k] for i in range(0, len(items), k)]


# ---------- главный экран ----------
def home_menu(enabled: bool, killed: bool, latch: bool) -> InlineKeyboardMarkup:
    if killed:
        first = b("🔓 Снять блокировку", "ke")
    elif enabled:
        first = b("⏸ Выключить торговлю", "tr:off")
    else:
        first = b("▶️ Включить торговлю", "tr:on")
    rows = [[first]]
    if latch:
        rows.append([b("🔒 Защёлка: что это?", "lt")])
    rows += [
        [b("📊 Позиция", "pos"), b("🧭 Сигналы", "sg")],
        [b("📜 Сделки", "tx:0"), b("🧾 События", "ev:0")],
        [b("🧩 Стратегии", "lg"), b("⚙️ Настройки", "set")],
        [b("↻ Обновить", "h")],
        [b("🛑 Паника", "pn")],
    ]
    return _kb(rows)


def settings_menu(lev: str, stop: str, risk: str) -> InlineKeyboardMarkup:
    return _kb([[b(f"⚖️ Плечо: {lev}", "lv")], [b(f"🛡 Стоп: {stop}", "sl")],
                [b(f"🚨 Риск-лимиты: {risk}", "rk")], [b("🔄 Сбросить пик", "pk")], nav("h", "set")])


def confirm(yes: str, no: str) -> InlineKeyboardMarkup:
    return _kb([[b("✅ Подтвердить", yes), b("❌ Отмена", no)]])


def leg_icon(enabled: bool, weight: float) -> str:
    """🟢 работает, ⏸ включена, но вес 0 (не торгуется), ⚪ выключена."""
    return "⚪" if not enabled else ("⏸" if weight <= 0 else "🟢")


def legs_menu(legs: list[tuple[str, float, bool]]) -> InlineKeyboardMarkup:
    """Одна кнопка на стратегию -> её экран (работающие выше неактивных, индекс в callback — позиция в Store.leg_cfg());
    ниже «Добавить» и «Поровну»."""
    rows = []
    for i, (label, w, en) in sorted(enumerate(legs), key=lambda t: not (t[1][2] and t[1][1] > 0)):
        tail = "выкл" if not en else f"{w:.0%}"
        rows.append([b(f"{leg_icon(en, w)} {label} — {tail}", f"lg:{i}")])
    return _kb(rows + [[b("➕ Добавить стратегию", "ad")], [b("⚖️ Поровну", "lg:eq")], nav("h", "lg")])


def leg_menu(i: int, cur_pct: int, enabled: bool, presets: list[int], can_minus: bool, can_plus: bool,
             can_toggle: bool) -> InlineKeyboardMarkup:
    """Экран одной стратегии: пресеты веса (только допустимые + текущий), шаги ±10%, вкл/выкл, навигация."""
    pre = [b(_mark(f"{v}%", v == cur_pct), f"lg:{i}:w:{v}") for v in presets]
    steps = ([b("−10%", f"lg:{i}:w:{cur_pct - 10}")] if can_minus else []) + (
             [b("+10%", f"lg:{i}:w:{cur_pct + 10}")] if can_plus else [])
    tog = [b("⏸ Выключить" if enabled else "▶️ Включить", f"lg:{i}:t")] if can_toggle else []
    return _kb([pre, steps, tog, [b("🗑 Удалить", f"lg:{i}:rm")], nav("lg", f"lg:{i}")])


def add_menu(pending: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Экран «Добавить стратегию»: ожидающие предложения (с сайта или из присланного текста) + навигация."""
    return _kb([[b(f"🧩 {label}", f"ad:{pid}")] for pid, label in pending] + [nav("lg", "ad")])


def draft_menu(pid: int, presets: list[int], equal: bool, can_add: bool, retry: bool = False, current: int | None = None,
               exists: bool = False) -> InlineKeyboardMarkup:
    """Черновик новой стратегии: пресеты веса (только помещающиеся в свободное место; текущий вес заменяемой ноги отмечен ✅),
    «Поровну», «Без веса» (для заменяемой ноги с ненулевым весом её нет: этот вес — отмеченный пресет), «Отклонить».
    exists — нога с таким ключом уже есть (замена параметров). can_add=False (проверка не пройдена) — остаётся «Отклонить», а
    «Проверить снова» — только если проверку не удалось выполнить (нет данных), а не если стратегия забракована."""
    if not can_add:
        return _kb([[b("🔄 Проверить снова", f"ad:{pid}")] if retry else [], [b("❌ Отклонить", f"ad:{pid}:x")], nav("ad", f"ad:{pid}")])
    pre = [b(_mark(f"{v}%", v == current), f"ad:{pid}:w:{v}") for v in presets if v > 0]
    return _kb(_chunks(pre, 4) + ([[b("⚖️ Поровну со всеми", f"ad:{pid}:eq")]] if equal else []) +
               ([] if exists and current else [[b("⏸ Заменить без веса" if exists else "⏸ Добавить без веса", f"ad:{pid}:w:0")]]) +
               [[b("❌ Отклонить", f"ad:{pid}:x")], nav("ad", f"ad:{pid}")])


def notice_menu(pid: int) -> InlineKeyboardMarkup:
    """Кнопка под уведомлением «предложение с сайта»: открывает черновик в панели."""
    return _kb([[b("🧩 Открыть", f"ad:{pid}")]])


def leverage_menu(current: float) -> InlineKeyboardMarkup:
    row = [b(_mark(f"{k}x", abs(k - current) < 1e-9), f"lv:s:{k}") for k in range(1, MAX_LEVERAGE + 1)]
    return _kb([row, nav("set", "lv")])


def stop_menu(current_pct: int) -> InlineKeyboardMarkup:
    pre = [b(_mark(f"{v}%", v == current_pct), f"sl:s:{v}") for v in STOP_PRESETS]
    return _kb([pre, [b("−1%", "sl:a:m1"), b("+1%", "sl:a:p1")], nav("set", "sl")])


def risk_menu(dd_pct: int, daily_pct: int) -> InlineKeyboardMarkup:
    dd = [b(_mark(f"📉 {v}%", v == dd_pct), f"rk:dd:s:{v}") for v in DD_PRESETS]
    da = [b(_mark(f"📆 {v}%", v == daily_pct), f"rk:da:s:{v}") for v in DAILY_PRESETS]
    return _kb(_chunks(dd, 3) + _chunks(da, 3) + [
        [b("📉 −5%", "rk:dd:m5"), b("📉 +5%", "rk:dd:p5")],
        [b("📆 −1%", "rk:da:m1"), b("📆 +1%", "rk:da:p1")], nav("set", "rk")])


def position_menu(has_pos: bool) -> InlineKeyboardMarkup:
    return _kb([[b("✖️ Закрыть позицию", "cl")] if has_pos else [], nav("h", "pos")])


def info_menu(back: str, refresh: str) -> InlineKeyboardMarkup:
    return _kb([nav(back, refresh)])


def pager_menu(prefix: str, page: int, pages: int) -> InlineKeyboardMarkup:
    """prefix — tx|ev; ссылки на соседние страницы ограничены границами (0..pages-1)."""
    prev_p, next_p = max(0, page - 1), min(pages - 1, page + 1)
    return _kb([[b("◀", f"{prefix}:{prev_p}"), b(f"{page + 1}/{pages}", f"{prefix}:{page}"), b("▶", f"{prefix}:{next_p}")],
                nav("h", f"{prefix}:{page}")])


def trade_notice_menu() -> InlineKeyboardMarkup:
    return _kb([[b("📊 Позиция", "np")]])
