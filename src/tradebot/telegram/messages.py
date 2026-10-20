"""ЕДИНЫЙ модуль текстов уведомлений и экранных строк Telegram: чистые функции (данные -> HTML/текст), без сети и БД.

Правила (заказ владельца):
* ни слова про среду/режим биржи (mainnet/testnet/paper): если процесс запущен НЕ так, как надо (без права на ордера или не на
  боевой бирже) — одна заметная строка config_warning(), больше нигде;
* сторона сделки — только словами и нейтральными стрелками (⬆️ ЛОНГ / ⬇️ ШОРТ); ✅/❌ — ТОЛЬКО знак реализованного P/L;
* деньги — в долларах (USDT == $), точные цифры биржи без «≈»; оценка по сделкам помечается «≈»/«(оценка)».
Уведомление исполнителя — это словарь `data` с ключом kind (open | increase | reduce | close | stopout | kill | panic | signal |
daily | startup | shutdown); Notifier строит по нему HTML через build(), а обычным получателям (Sink в тестах, журнал событий)
уходит plain() — тот же текст без тегов. Все динамические строки экранируются (tx.esc), структуру задают только эти функции."""
from __future__ import annotations

import inspect

from tradebot.telegram import texts as tx
from tradebot.telegram.texts import sig_icon, sig_word  # noqa: F401  (реэкспорт: единые слова/иконки сигнала)

MINUS = "−"
ARROW = {"long": "⬆️", "short": "⬇️"}
WORD = {"long": "ЛОНГ", "short": "ШОРТ"}
NO_ORDERS = "режим без ордеров"
TESTNET_WORDS = "тестовая сеть"


# ---------- числа ----------
def _z(x: float, d: int) -> bool:
    return round(abs(x), d) == 0


def usd(x: float | None, d: int = 2, sign: bool = False) -> str:
    """«244.65 $»; sign=True -> «+2.31 $» / «−32.10 $» (настоящий минус). Ноль — без знака."""
    if x is None:
        return "—"
    if _z(x, d):
        return f"{0:.{d}f} $"
    s = (MINUS if x < 0 else ("+" if sign else ""))
    return f"{s}{tx.n(abs(x), d)} $"


def pct(x: float | None, d: int = 1, sign: bool = True) -> str:
    """Доля -> проценты: 0.014 -> «+1.4%», −0.196 -> «−19.6%»; хвост «.0» отрезается (−20%)."""
    if x is None:
        return "—"
    v = round(x * 100, d)
    if v == 0:
        return "0%"
    t = f"{abs(v):.{d}f}"
    t = t.rstrip("0").rstrip(".") if "." in t else t
    return f"{MINUS if v < 0 else ('+' if sign else '')}{t}%"


def share(x: float | None) -> str:
    """Доля счёта без знака: 0.748 -> «75%», 0.052 -> «5.2%»."""
    if x is None:
        return "—"
    return f"{round(x * 100)}%" if x >= 0.095 else pct(x, 1, sign=False)


def lev_s(lev: float | None) -> str:
    return "—" if not lev else f"{lev:g}x"


def dur(seconds: float | None) -> str:
    """Длительность: «45 с», «12 мин», «2 ч 15 мин», «1 д 3 ч»."""
    if seconds is None:
        return "—"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} с"
    if s < 3600:
        return f"{s // 60} мин"
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return f"{h} ч {m} мин" if m else f"{h} ч"
    d, h = divmod(s // 3600, 24)
    return f"{d} д {h} ч" if h else f"{d} д"


def pnl_icon(x: float | None) -> str:
    """✅ прибыль / ❌ убыток — ЕДИНСТВЕННОЕ место, где знак окрашен; ноль и неизвестное — нейтрально."""
    if x is None or _z(x, 2):
        return "➖"
    return "✅" if x > 0 else "❌"


def arrow(side: str) -> str:
    return ARROW.get(side, "▫️")


def word(side: str) -> str:
    return WORD.get(side, str(side).upper())


def target_words(t: float | None) -> str:
    """Человеческая цель: «вне рынка» / «ЛОНГ 100%» / «ШОРТ 50%»."""
    if t is None:
        return "считаю…"
    if abs(t) < 1e-9:
        return "вне рынка"
    return f"{'ЛОНГ' if t > 0 else 'ШОРТ'} {round(abs(t) * 100)}%"


def leg_label(key: str) -> str:
    """Ключ ноги «Regime switch (CHOP)@6h» -> «Regime switch (CHOP) · 6h»."""
    return key.replace("@", " · ")


def sig_change(label: str, prev: float | None, new: float | None) -> str:
    return f"{leg_label(label)}: {sig_word(prev)} → {sig_word(new)}"


def reason_human(raw: str | None) -> str:
    """Причина сделки для людей. Формат хранимой причины «<повод>[, <подробности>]»: повод — бар | старт/перезапуск |
    внеплановый пересчёт | закрытие вручную | …; подробности (смена сигнала) важнее повода."""
    r = (raw or "").strip()
    if not r:
        return "—"
    head, _, tail = r.partition(",")
    head, tail = head.strip(), tail.strip()
    if tail.startswith("с опозданием"):
        tail = ""
    if tail:
        return tail
    return {"бар": "плановая сверка по закрытию бара", "старт/перезапуск": "сверка при запуске бота",
            "внеплановый пересчёт": "пересчёт после смены настроек", "сигнал": "смена сигнала"}.get(head, head[:1].upper() + head[1:])


def stored_reason(why: str, changes: list[dict] | None) -> str:
    """Причина для журнала сделок/исполнителя: «<повод>, <смена сигнала>» (группировка статистики идёт по тексту до запятой)."""
    ch = "; ".join(sig_change(c["label"], c.get("prev"), c.get("new")) for c in (changes or []))
    return f"{why}, {ch}" if ch else why


# ---------- блоки ----------
def _sidehead(side: str) -> str:
    return f"{arrow(side)} {word(side)}"


def _acct(equity: float | None) -> str:
    return f"Счёт {tx.code(tx.n(equity))} $" if equity else ""


def _lines(*xs: str | None) -> str:
    return "\n".join(x for x in xs if x)


def own_money(qty: float, price: float, lev: float, equity: float | None) -> tuple[float, float, float | None]:
    """(позиция в $, свои деньги = позиция/плечо, доля счёта)."""
    notional = qty * price
    margin = notional / lev if lev else notional
    return notional, margin, (margin / equity if equity and equity > 0 else None)


def _stop_line(side: str, price: float, stop: float | None) -> str:
    if not stop:
        return "⚠️ Стоп на бирже ещё не подтверждён — проверяю"
    return f"Стоп {tx.code(tx.n(stop, 1))} ({tx.code(pct((stop - price) / price, 1))})"


# ---------- сделки ----------
def open_msg(d: dict) -> str:
    notional, margin, sh = own_money(d["qty"], d["price"], d["leverage"], d.get("equity"))
    return _lines(
        f"{arrow(d['side'])} <b>Открыт {word(d['side'])}</b>",
        f"Вход {tx.code(tx.n(d['price'], 1))} · {tx.code(tx.qty(d['qty']))} BTC",
        f"Позиция {tx.code(tx.n(notional))} $ с плечом {tx.code(lev_s(d['leverage']))}",
        f"Своих денег {tx.code(tx.n(margin))} $" + (f" ({tx.code(share(sh))} счёта)" if sh is not None else ""),
        _stop_line(d["side"], d["price"], d.get("stop")),
        f"Причина: {tx.esc(reason_human(d.get('reason')))}",
        _acct(d.get("equity")))


def increase_msg(d: dict) -> str:
    notional, margin, sh = own_money(d["total_qty"], d.get("avg_entry") or d["price"], d["leverage"], d.get("equity"))
    return _lines(
        f"{arrow(d['side'])} <b>Увеличена позиция {word(d['side'])}</b>",
        f"Добавлено {tx.code('+' + tx.qty(d['delta']))} BTC по {tx.code(tx.n(d['price'], 1))}",
        f"Теперь {tx.code(tx.qty(d['total_qty']))} BTC · {tx.code(tx.n(notional))} $ с плечом {tx.code(lev_s(d['leverage']))}",
        f"Своих денег {tx.code(tx.n(margin))} $" + (f" ({tx.code(share(sh))} счёта)" if sh is not None else ""),
        f"Причина: {tx.esc(reason_human(d.get('reason')))}",
        _acct(d.get("equity")))


def _approx(exact: bool) -> str:
    return "" if exact else "≈ "


def reduce_msg(d: dict) -> str:
    ex = d.get("exact", True)
    pnl = d.get("pnl")
    return _lines(
        f"{arrow(d['side'])} <b>Уменьшена позиция {word(d['side'])}</b>",
        f"Убрано {tx.code(MINUS + tx.qty(d['delta']))} BTC по {tx.code(tx.n(d['price'], 1))}",
        f"Осталось {tx.code(tx.qty(d['total_qty']))} BTC",
        f"{pnl_icon(pnl)} Зафиксировано {tx.b(_approx(ex) + usd(pnl, 2, True))} " + tx.it("(прибыль)" if (pnl or 0) > 0.004 else
                                                                                 "(убыток)" if (pnl or 0) < -0.004 else "(ноль)"),
        None if ex else tx.it("≈ оценка по сделкам; точная цифра биржи не получена"),
        f"Причина: {tx.esc(reason_human(d.get('reason')))}",
        _acct(d.get("equity")))


def close_msg(d: dict) -> str:
    """Закрытие позиции целиком: заголовок — реализованный P/L сделки в $ (комиссии и фандинг учтены)."""
    ex = d.get("exact", True)
    pnl = d["pnl"]
    p = d.get("pct")
    roe = d.get("roe")
    sub = []
    if p is not None:
        sub.append(f"{tx.code(_approx(ex) + pct(p, 1))} от счёта" + (" (оценка)" if d.get("pct_est") else ""))
    if roe is not None:
        sub.append(f"на свои деньги {tx.code(_approx(ex) + pct(roe, 1))}")
    fees = d.get("fees")
    fund = d.get("funding")
    cost = []
    if fees is not None:
        cost.append(f"Комиссии {tx.code(usd(fees))}")
    if fund is not None:
        cost.append(f"фандинг {tx.code(usd(fund, 2, True))}")
    return _lines(
        f"{pnl_icon(pnl)} <b>Закрыт {word(d['side'])}: {_approx(ex)}{usd(pnl, 2, True)}</b>",
        " · ".join(sub) or None,
        f"Вход {tx.code(tx.n(d['entry'], 1))} → выход {tx.code(tx.n(d['exit'], 1))} · {tx.code(tx.qty(d['qty']))} BTC",
        f"Держали {tx.code(dur(d['held_s']))}" if d.get("held_s") is not None else None,
        (" · ".join(cost) if cost else None),
        f"Включая частичные закрытия: {d['reds']}" if d.get("reds") else None,
        None if ex else tx.it("≈ оценка по сделкам (комиссия 0.1%); точная цифра биржи пока не получена"),
        f"Причина: {tx.esc(reason_human(d.get('reason')))}",
        _acct(d.get("equity")))


def _exit_title(kind: str) -> str:
    return {"liquidation": "Ликвидация", "external": "Позиция закрыта на бирже (не ботом)"}.get(kind, "Сработал стоп-лосс")


def stopout_msg(d: dict) -> str:
    """Закрытие биржей: стоп-лосс / ликвидация / ручное закрытие в приложении. pnl=None — запись биржи ещё не найдена."""
    pnl = d.get("pnl")
    ex = d.get("exact", True)
    p = d.get("pct")
    head = _exit_title(d.get("exit_kind", "stop"))
    if d.get("followup"):
        head = "Уточнение: " + head[:1].lower() + head[1:]
    if pnl is None:
        title = f"🛑 <b>{head}</b> — сумма уточняется"
    else:
        title = f"🛑 <b>{head}: {_approx(ex)}{usd(pnl, 2, True)}</b>" + (
            f" ({_approx(ex)}{pct(p, 1)} счёта{', оценка' if d.get('pct_est') else ''})" if p is not None else "")
    if d.get("latched"):
        tail = "Заново не вхожу, пока сигнал не изменится."
    elif d.get("followup"):
        tail = None
    else:
        tail = "Цель уже изменилась — решение по новой цели ниже."
    pos = f"{_sidehead(d['side'])} {tx.code(tx.qty(d['qty']))} BTC" if d.get("side") and d.get("qty") else None
    if pos and d.get("entry") and d.get("exit"):
        pos += f" · вход {tx.code(tx.n(d['entry'], 1))} → выход {tx.code(tx.n(d['exit'], 1))}"
    elif pos and d.get("entry"):
        pos += f" · вход {tx.code(tx.n(d['entry'], 1))}"
    cost = None
    if d.get("fees") is not None:
        cost = f"Комиссии {tx.code(usd(d['fees']))}" + (f" · фандинг {tx.code(usd(d['funding'], 2, True))}" if d.get("funding") is not None else "")
    return _lines(title, pos, cost, tail,
                  None if ex or pnl is None else tx.it("≈ оценка по сделкам; точная цифра биржи не получена"),
                  _acct(d.get("equity")))


def _closed_line(d: dict) -> str | None:
    """Строка «Позиция закрыта: ✅ +1.2 $ (+0.7% счёта)» для kill/panic; None — закрывать было нечего."""
    if not d.get("closed"):
        return None
    pnl = d.get("pnl")
    if pnl is None:
        return "Позиция закрыта."
    ex = d.get("exact", True)
    return (f"Позиция закрыта: {pnl_icon(pnl)} {tx.b(_approx(ex) + usd(pnl, 2, True))}" +
            (f" ({tx.code(_approx(ex) + pct(d['pct'], 1))} счёта)" if d.get("pct") is not None else ""))


def kill_msg(d: dict) -> str:
    return _lines(f"🛑 <b>Kill-switch: {tx.esc(d.get('reason') or 'лимит риска')}</b>",
                  _closed_line(d) or ("Открытой позиции не было." if not d.get("failed") else None),
                  "⚠️ Закрыть позицию не удалось — проверьте на бирже!" if d.get("failed") else None,
                  "Торговля выключена. Включить можно только вручную, с подтверждением.",
                  _acct(d.get("equity")))


def panic_msg(d: dict) -> str:
    return _lines("🛑 <b>Паника: торговля выключена</b>",
                  _closed_line(d) or ("Открытой позиции не было." if not d.get("failed") else None),
                  "⚠️ Закрыть позицию не удалось — проверьте на бирже!" if d.get("failed") else None,
                  _acct(d.get("equity")))


# ---------- сигналы ----------
NO_ORDER_NOTES = {
    "торговля выключена": "торговля выключена, ордера не отправляю",
    "kill-switch": "kill-switch: ордера не отправляю",
    "защёлка: жду смены сигнала": "защёлка после стопа: жду другого сигнала",
    "без изменений": "позицию менять не нужно",
    "выполнено": "позицию менять не нужно",
}


def signal_msg(d: dict) -> str:
    """Отдельное (короткое) сообщение о смене сигнала, когда ордера не последовало."""
    ch = d.get("changes") or []
    return _lines(*(f"📈 <b>{tx.esc(sig_change(c['label'], c.get('prev'), c.get('new')))}</b>" for c in ch),
                  f"Цель: {tx.code(target_words(d.get('target')))}" + (f" · {tx.esc(d['note'])}" if d.get("note") else ""))


# ---------- итоги суток ----------
def daily_msg(d: dict) -> str:
    eq = d.get("equity")
    base = d.get("base")
    day = None if (eq is None or not base) else eq - base
    lines = ["📅 <b>Итоги суток</b>"]
    row = f"Счёт {tx.code(tx.n(eq))} $" if eq is not None else "Счёт —"
    if day is not None:
        row += f" · за сутки {tx.code(usd(day, 2, True))} ({tx.code(pct(day / base, 1))})"
    lines.append(row)
    n = d.get("trips_n", 0)
    if n:
        t = f"Закрыто сделок: {tx.code(n)} · итого {tx.b(usd(d.get('trips_pnl'), 2, True))}"
        if d.get("trips_fees"):
            t += f" (комиссии {tx.code(usd(d['trips_fees']))})"
        lines.append(t)
    else:
        lines.append("Закрытых сделок нет")
    pos = d.get("pos")
    if pos:
        lines.append(f"Позиция: {_sidehead(pos['side'])} {tx.code(tx.qty(pos['qty']))} BTC · нереализовано {tx.code(usd(pos['upnl'], 2, True))}")
    else:
        lines.append("Позиции нет")
    if d.get("dd") is not None:
        lines.append(f"От пика счёта: {tx.code(pct(-abs(d['dd']), 1) if d['dd'] else '0%')}")
    return "\n".join(lines)


# ---------- запуск / остановка ----------
def config_warning(env: str | None, paper: bool) -> str | None:
    """Единственное место, где упоминается режим запуска: только если он НЕ боевой. None — всё настроено как надо."""
    parts = []
    if paper:
        parts.append(NO_ORDERS)
    if env and env != "mainnet":
        parts.append(TESTNET_WORDS)
    return ("⚠️ " + " / ".join(parts)[:1].upper() + " / ".join(parts)[1:]) if parts else None


def pos_brief(pos: dict | None) -> str:
    if not pos:
        return "позиции нет"
    s = f"позиция {word(pos['side'])} {tx.qty(pos['qty'])} BTC"
    return s + (f" ({usd(pos['upnl'], 2, True)})" if pos.get("upnl") is not None else "")


def startup_msg(d: dict) -> str:
    """«Бот запущен ✅ · счёт 163.44 $ · торговля включена · позиция ЛОНГ 0.003 BTC (+1.06 $)» (+ строка-предупреждение)."""
    bits = ["<b>Бот запущен ✅</b>"]
    if d.get("equity") is not None:
        bits.append(f"счёт {tx.code(tx.n(d['equity']))} $")
    if d.get("no_keys"):
        bits.append("API-ключи не заданы: торговля невозможна")
    elif d.get("killed"):
        bits.append("kill-switch: торговля заблокирована")
    else:
        bits.append("торговля включена" if d.get("enabled") else "торговля выключена")
    bits.append(tx.esc(pos_brief(d.get("pos"))) if d.get("pos_known", True) else "позицию проверить не удалось")
    return _lines(" · ".join(bits), config_warning(d.get("env"), bool(d.get("paper"))))


def shutdown_msg(d: dict) -> str:
    """Бот остановлен · позиция … — про стоп говорим только если он подтверждён чтением с биржи."""
    pos = d.get("pos")
    if d.get("unknown"):
        tail = "не удалось проверить позицию и стоп на бирже — проверьте вручную"
    elif not pos:
        tail = "позиции нет"
    elif d.get("stop") is None:
        tail = f"⚠️ позиция {word(pos['side'])} {tx.qty(pos['qty'])} BTC БЕЗ биржевого стопа!"
    else:
        tail = f"позиция {word(pos['side'])} {tx.qty(pos['qty'])} BTC под биржевым стопом {tx.n(d['stop'], 1)}"
    return f"<b>Бот остановлен</b> · {tx.esc(tail)}"


# ---------- строки позиции для экранов ----------
def position_lines(pos: dict | None, lev: float, equity: float | None, stop: float | None) -> list[str]:
    """Строки открытой позиции для Главной и «Позиции»: сторона словами, размер, стоимость с плечом, свои деньги и доля счёта,
    вход/текущая, нереализованный P/L в $ и % счёта, биржевой стоп и расстояние до него."""
    if not pos:
        return ["📍 Позиция: нет"]
    notional, margin, sh = own_money(pos["qty"], pos["entry"], lev, equity)
    upnl = pos.get("upnl") or 0.0
    out = [f"📍 {arrow(pos['side'])} <b>{word(pos['side'])}</b> {tx.code(tx.qty(pos['qty']))} BTC",
           f"    Позиция {tx.code(tx.n(notional))} $ с плечом {tx.code(lev_s(lev))}",
           f"    Своих денег {tx.code(tx.n(margin))} $" + (f" ({tx.code(share(sh))} счёта)" if sh is not None else ""),
           f"    Вход {tx.code(tx.n(pos['entry'], 1))} · сейчас {tx.code(tx.n(pos['mark'], 1))}",
           f"    Нереализовано {tx.code(usd(upnl, 2, True))}" + (f" ({tx.code(pct(upnl / equity, 2))} счёта)" if equity and equity > 0 else "")]
    if stop:
        out.append(f"    Стоп {tx.code(tx.n(stop, 1))} ({tx.code(pct((stop - pos['entry']) / pos['entry'], 1))})")
    return out


# ---------- обычные предупреждения (plain-текст; Notifier экранирует сам) ----------
def w_setup_failed(e) -> str:
    return f"Не удалось настроить аккаунт: {e}. Торговля заблокирована."


def w_setup_mismatch(rb: dict, lev: float) -> str:
    yn = lambda v: "да" if v else "нет"          # noqa: E731
    return (f"Настройки на бирже не совпали с нужными: плечо {rb.get('leverage', 0):g}x (нужно {lev:g}x), изолированная маржа — "
            f"{yn(rb.get('isolated'))}, one-way — {yn(rb.get('one_way'))}. Торговля заблокирована.")


def w_key_check_failed(e) -> str:
    return f"Не удалось проверить права API-ключа ({type(e).__name__}) — продолжаю."


def w_key_read_only() -> str:
    return ("API-ключ только на чтение — ордера невозможны. В Bybit → API выдайте ключу Read-Write (Contract Orders и Positions, "
            "без Withdraw) и перезапустите бота. Торговля заблокирована.")


def w_key_withdraw() -> str:
    return ("У API-ключа включено право вывода средств (Withdraw) — торговому ключу оно не нужно. Уберите его в Bybit → API "
            "и перезапустите бота. Торговля заблокирована.")


def w_order_ambiguous() -> str:
    return "Ответ на ордер не получен, но позиция изменилась — считаю ордер исполненным."


def w_order_unconfirmed() -> str:
    return "Исполнение ордера не подтвердилось — сверю позицию с биржей."


def w_rearm_failed(e) -> str:
    return f"Не удалось проверить или поставить стоп на бирже: {e}"


def w_stop_failed() -> str:
    return "Не удалось поставить стоп на бирже — закрываю позицию и выключаю торговлю."


def w_not_flat(side: str, qty: float) -> str:
    return f"Позиция не закрылась ({word(side)} {tx.qty(qty)} BTC)! Проверьте на бирже вручную."


def w_orders_cancelled(n: int) -> str:
    return f"Отменил висящие ордера: {n}."


def w_balance_unavailable(e) -> str:
    return f"Баланс счёта недоступен ({e}) — ордеров в этом цикле нет, повторю при следующей сверке."


def w_exchange_error(e) -> str:
    return f"Ошибка биржи при сверке: {e}"


def w_close_failed(e) -> str:
    return f"Не удалось закрыть позицию: {e}"


def w_weights(weights: list[str]) -> str:
    return "Веса стратегий уменьшены, чтобы сумма не превышала 100%: " + "; ".join(weights)


def w_feed_down(e) -> str:
    return f"Нет данных рынка, бар пропущен — позицию не меняю: {e}"


def w_oi_late() -> str:
    return "Данные OI не вышли вовремя — OI-стратегии держат прежний сигнал."


def w_tf_stale(tf: str, why: str) -> str:
    return f"Данные {tf} недоступны ({why}) — стратегии на {tf} держат прежний сигнал."


def w_no_signal(sigs) -> str:
    return f"Сигнал не посчитан ({sigs}) — цель не меняю, ордеров нет."


def w_cycle_failed(e) -> str:
    return f"Сбой пересчёта сигналов: {str(e) or type(e).__name__}"


def w_monitor_failed(n: int, e) -> str:
    return f"Мониторинг: {n} сбоев подряд ({str(e) or type(e).__name__})"


# ---------- сборка по data ----------
BUILDERS = {"open": open_msg, "increase": increase_msg, "reduce": reduce_msg, "close": close_msg, "stopout": stopout_msg,
            "kill": kill_msg, "panic": panic_msg, "signal": signal_msg, "daily": daily_msg, "startup": startup_msg,
            "shutdown": shutdown_msg}


def build(d: dict) -> str:
    """HTML сообщения по data['kind']; KeyError — неизвестный вид (вызывающий откатывается на обычный текст)."""
    return BUILDERS[d["kind"]](d)


def plain(d: dict) -> str:
    """Тот же текст без тегов, строки через « · » — для журнала событий и обычных получателей уведомлений. Для закрытия биржей
    начинается с «Позиция исчезла (стоп/ликвидация)» — по этой подстроке веб-статистика находит такие события."""
    t = " · ".join(x for x in tx.strip_html(build(d)).split("\n") if x)
    if d.get("kind") == "stopout" and not d.get("followup"):
        # подстрока «Позиция исчезла» — маркер закрытия биржей для веб-статистики и closed_trips; уточнение (followup) её НЕ
        # содержит, иначе оно стало бы вторым разрывом на более позднем времени (и могло бы «закрыть» вновь открытую позицию)
        t = "Позиция исчезла (стоп/ликвидация) · " + t
    return t


def accepts_data(fn) -> bool:
    """Умеет ли получатель уведомлений третий аргумент data (Notifier и lambda *a — да; Sink(level, text) — нет)."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    ps = list(sig.parameters.values())
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in ps):
        return True
    return len([p for p in ps if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]) >= 3


def emit(notify, level: str, data: dict) -> None:
    """Структурное уведомление: получателю с data — (level, plain, data), обычному Sink — (level, plain)."""
    text = plain(data)
    if accepts_data(notify):
        notify(level, text, data)
    else:
        notify(level, text)
