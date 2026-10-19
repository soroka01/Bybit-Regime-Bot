"""Форматирование чисел/времени и короткие русские тексты для Telegram-интерфейса."""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def tzinfo(name: str):
    """ZoneInfo по имени; если базы часовых поясов нет — UTC (для Europe/Moscow фиксированный UTC+3)."""
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=3)) if name == "Europe/Moscow" else UTC


def n(x: float | None, d: int = 2) -> str:
    """Число с пробелами в тысячах: 12 345.60"""
    if x is None:
        return "—"
    return f"{x:,.{d}f}".replace(",", " ")


def sn(x: float | None, d: int = 2) -> str:
    """Со знаком: +1 234.50"""
    return "—" if x is None else f"{x:+,.{d}f}".replace(",", " ")


def pct(x: float | None, d: int = 1, sign: bool = False) -> str:
    if x is None:
        return "—"
    return f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"


def qty(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") if x else "0"


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def age(d: datetime | None, now: datetime) -> str:
    """Относительное время: «5 мин назад». Минутная гранулярность — чтобы текст не менялся каждую секунду."""
    if d is None:
        return "нет данных"
    s = (now - d).total_seconds()
    if s < 60:
        return "менее минуты назад"
    if s < 3600:
        return f"{int(s // 60)} мин назад"
    if s < 86400:
        return f"{int(s // 3600)} ч назад"
    return f"{int(s // 86400)} д назад"


def local(d: datetime | None, tz, now: datetime | None = None) -> str:
    """Местное время: «14:05» сегодня, иначе «03.02 14:05»."""
    if d is None:
        return "—"
    ld = d.astimezone(tz)
    if now is not None and ld.date() == now.astimezone(tz).date():
        return ld.strftime("%H:%M")
    return ld.strftime("%d.%m %H:%M")


def minutes_to_next_bar(now: datetime) -> int:
    """Минут до следующего закрытия часовой свечи (+10 с, когда просыпается раннер)."""
    base = (now - timedelta(seconds=10)).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1, seconds=10)
    return max(0, int((base - now).total_seconds() // 60) + 1)


def sig_icon(v: float | None) -> str:
    """Иконка сигнала/цели: нейтральная стрелка стороны (⬆️ лонг, ⬇️ шорт), ➖ вне рынка, ▫️ нет данных. Красный/зелёный
    кружок для стороны НЕ используется: красный «продажа» путают с шортом, когда закрывается лонг."""
    return "▫️" if v is None else ("➖" if abs(v) < 1e-9 else ("⬆️" if v > 0 else "⬇️"))


def sig_word(v: float | None) -> str:
    return "нет данных" if v is None else ("вне рынка" if abs(v) < 1e-9 else ("лонг" if v > 0 else "шорт"))


STRATEGY_DESC = {
    "SMA cross": "тренд: быстрая скользящая выше медленной — лонг, ниже — шорт",
    "OI flush": "лонг после резкого падения open interest и цены (сброс плеча)",
    "Regime switch (CHOP)": "переключатель режима по Choppiness Index: тренд (EMA 12/100) при низком CHOP, возврат к средней "
                            "(Боллинджер) при высоком, между порогами — вне рынка; лонг и шорт",
}


# ---------- HTML (parse_mode=HTML): всё динамическое — только через esc() ----------
SEP = "─" * 16
ELLIPSIS = "…"


def esc(x) -> str:
    """Экранирование данных (причины, тексты событий, ошибки, названия) для Telegram HTML."""
    return html.escape(str(x), quote=False)


def b(x) -> str:
    return f"<b>{esc(x)}</b>"


def code(x) -> str:
    return f"<code>{esc(x)}</code>"


def it(x) -> str:
    return f"<i>{esc(x)}</i>"


def bq(inner_html: str) -> str:
    """Цитата; аргумент — уже безопасный HTML (собранный через esc/b/code)."""
    return f"<blockquote>{inner_html}</blockquote>"


def oneline(x, limit: int | None = None) -> str:
    """Сжимает переносы/пробелы и (до экранирования!) обрезает: теги не режутся, т.к. их ещё нет."""
    t = " ".join(str(x).split())
    return t if limit is None or len(t) <= limit else t[:limit - 1] + ELLIPSIS


def trunc(x, limit: int) -> str:
    t = str(x)
    return t if len(t) <= limit else t[:limit - 1] + ELLIPSIS


_TOKEN = re.compile(r"<(/?)([A-Za-z]+)[^>]*>|&[#A-Za-z0-9]+;|[^<&]+|[<&]")


def fit_html(text: str, limit: int = 4000) -> str:
    """Обрезает HTML-текст до limit символов так, чтобы не резать тег/сущность пополам; открытые теги закрываются."""
    if len(text) <= limit:
        return text
    out: list[str] = []
    stack: list[str] = []
    used = 0
    for m in _TOKEN.finditer(text):
        tok, name = m.group(0), m.group(2)
        if name:
            if m.group(1):
                new = stack[:-1] if stack and stack[-1] == name else stack
            else:
                new = stack + [name]
        else:
            new = stack
        reserve = len(ELLIPSIS) + sum(len(t) + 3 for t in new)
        if used + len(tok) + reserve > limit:
            if not name and not tok.startswith(("&", "<")):   # обычный текст можно обрезать по символам
                room = limit - used - len(ELLIPSIS) - sum(len(t) + 3 for t in stack)
                if room > 0:
                    out.append(tok[:room])
            break
        out.append(tok)
        used += len(tok)
        stack = new
    return "".join(out) + ELLIPSIS + "".join(f"</{t}>" for t in reversed(stack))


def strip_html(text: str) -> str:
    """Текст без тегов (для повторной отправки как plain при ошибке разбора сущностей)."""
    return html.unescape(re.sub(r"<[^>]*>", "", text))
