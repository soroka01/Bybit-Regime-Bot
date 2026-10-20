// Вкладка «Бот (live)»: «Закрытые сделки» (итоги окна 30 дн. из /api/stats) и откат к списку ордеров снимка.
import type { LiveSnapshot, StatsResponse } from "../types";
import { h } from "../ui/dom";
import { datetime, num, parseTime, spctU, susd, toneOf, usd } from "../ui/fmt";
import { emptyState, glyph } from "../ui/kit";
import { orderBadge, sideBadge } from "../ui/side";
import { closedKey, isBuy, positionView, upnlAccountFrac, type LiveTrade } from "./liveCalc";
import { tk } from "./liveTick";
import { ddmm, fmtDuration, hhmm, isoUtc, periodSummary, resultText, resultTone } from "./statsLogic";
import { reasonCell } from "./statsView";

const dfShort = new Intl.DateTimeFormat("ru-RU", { timeZone: "UTC", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

/** Что показывает блок сделок: ждём первый ответ /api/stats, закрытые сделки готовы, либо откат к списку ордеров (старый бэкенд / ошибка / нет статистики). */
export type ClosedState =
  | { kind: "loading" }
  | { kind: "ok"; res: StatsResponse }
  | { kind: "fallback"; why: "old_backend" | "error" | "unavailable"; message?: string };

/** Подсказка над списком ордеров, когда закрытые сделки получить нельзя. */
export function fallbackHint(st: Extract<ClosedState, { kind: "fallback" }>): string {
  if (st.why === "old_backend") return "Закрытые сделки с итогом в долларах появятся после перезапуска бота (run.bat): сервер дашборда запущен на старой версии. Пока показаны отдельные ордера.";
  if (st.why === "unavailable") return `Итоги закрытых сделок сейчас недоступны${st.message ? `: ${st.message}` : ""}. Пока показаны отдельные ордера.`;
  return `Не удалось загрузить закрытые сделки${st.message ? ` (${st.message})` : ""}. Пока показаны отдельные ордера; повтор через минуту.`;
}

/** Список отдельных ордеров (fills) — прежняя таблица «Последние сделки»; hint — пояснение, почему показаны ордера, а не итоги. */
export function tradesCard(trades: readonly LiveTrade[] | undefined, limit: number, onMore: () => void, hint: string | null = null): HTMLElement {
  const all = [...(trades ?? [])].sort((a, b) => +(parseTime(b.ts) ?? 0) - +(parseTime(a.ts) ?? 0));
  const shown = all.slice(0, limit);
  const body = shown.length ? h("div", { class: "table-wrap lv-trades-wrap", tabindex: 0, role: "region", "aria-label": "Ордера бота" },
    h("table", { class: "data lv-trades" },
      h("thead", {}, h("tr", {}, ...[["Время", true, ""], ["Ордер", true, ""], ["Объём", false, ""], ["Цена", false, ""], ["Номинал", false, "lv-notional"], ["Причина", true, ""]].map(([t, l, c]) => h("th", { class: `${l ? "left" : ""} ${c}`.trim(), scope: "col" }, h("span", { class: "th-txt", text: String(t) }))))),
      h("tbody", {}, ...shown.map((t) => {
        const d = parseTime(t.ts);
        return h("tr", {},
          h("td", { class: "left", title: datetime(t.ts) }, h("time", { dateTime: d?.toISOString() ?? "" }, tk("age", t.ts, h("span")), h("span", { class: "lv-t-abs muted", text: d ? ` · ${dfShort.format(d).replace(",", "")}` : "" }))),
          h("td", { class: "left" }, orderBadge(isBuy(t.side))),
          h("td", { text: num(t.qty, 4) }), h("td", { text: num(t.price, 2) }), h("td", { class: "lv-notional", text: usd(t.qty * t.price, 2) }),
          h("td", { class: "left lv-reason", text: t.reason || "—" }));
      }))))
    : emptyState("Сделок пока нет", "Сделки появятся здесь после первого входа бота в рынок.", null);
  return h("section", { class: "card lv-trades-card", "aria-label": "Сделки" },
    h("header", { class: "card-head" }, h("h3", { text: "Последние сделки" }), h("span", { class: "muted small", text: all.length ? `${shown.length} из ${all.length}` : "" })),
    hint ? h("p", { class: "notice lv-trades-hint", role: "note" }, h("span", { text: hint })) : null,
    body,
    all.length > shown.length ? h("button", { class: "btn ghost sm lv-more", type: "button", text: `Показать ещё ${Math.min(20, all.length - shown.length)}`, onClick: onMore }) : null);
}

export const CLOSED_RANGE_LABEL = "За 30 дн.";

/** Скелетон блока «Закрытые сделки» до первого ответа. */
export function closedTradesLoading(): HTMLElement {
  return h("section", { class: "card lv-trades-card lv-closed", "aria-label": "Закрытые сделки", "aria-busy": "true" },
    h("header", { class: "card-head" }, h("h3", { text: "Закрытые сделки" })),
    h("div", { class: "skeleton skel-line", style: "height:34px;margin-bottom:8px" }), h("div", { class: "skeleton skel-line", style: "height:120px" }));
}

export interface ClosedOpts { limit: number; onMore(): void; fresh: ReadonlySet<string> }

const signedM = (v: number, strong = false): HTMLElement => {
  const t = toneOf(v, 0.005);
  return h("b", { class: `st-m ${t}${strong ? " strong" : ""}` }, t ? h("span", { class: "g", "aria-hidden": "true", text: glyph(t) + " " }) : null, susd(v));
};

/** Шапка: «За 30 дн.: закрыто N · плюс +A $ · минус −B $ · итого ±C $» (+ ROI счёта за окно — отдельной приглушённой подписью). */
export function closedStrip(res: StatsResponse): HTMLElement | null {
  const sm = periodSummary(res);
  if (!sm) return null;
  const dot = () => h("span", { class: "lv-dot muted", "aria-hidden": "true", text: "·" });
  const roi = res.equity?.wallet_return_pct;
  return h("div", { class: "lv-strip", role: "group", "aria-label": "Итоги закрытых сделок за 30 дней" },
    h("span", { class: "lv-strip-l", text: `${CLOSED_RANGE_LABEL}:` }),
    h("span", {}, "закрыто ", h("b", { text: String(sm.count) })), dot(),
    h("span", { class: "lv-strip-p" }, "плюс ", signedM(sm.profit)), dot(),
    h("span", { class: "lv-strip-m" }, "минус ", signedM(sm.loss)), dot(),
    h("span", { class: "lv-strip-t" }, "итого ", signedM(sm.net, true)),
    roi != null ? h("span", { class: "lv-strip-roi muted small", title: "Только за 30 дней (не за всё время): изменение кошелька за 30 дней / кошелёк на начале окна (включает вводы и выводы). Не равно сумме сделок: учитывает открытую позицию и движения вне бота." }, `ROI счёта за 30 дн. ${spctU(roi, 2)}`) : null);
}

/** Строка открытой позиции — всегда первая: нереализованный результат (ещё не зафиксирован). */
function openRow(s: LiveSnapshot, res: StatsResponse): HTMLElement | null {
  const v = positionView(s.position, s.settings);
  if (!v) return null;
  const t = toneOf(v.upnl, 0.005);
  const frac = upnlAccountFrac(v.upnl, s.equity);
  const opened = res.open_position?.opened_ts ?? null;
  return h("tr", { class: "lv-open-row", "data-open": "1" },
    h("td", { class: "left" }, h("span", { class: "badge info lv-open-tag", text: "ОТКРЫТА", title: "Позиция ещё не закрыта: результат нереализованный и может измениться" }),
      opened ? h("div", { class: "lv-t-abs muted small" }, "с ", `${ddmm(opened)} ${hhmm(opened)} UTC`) : null),
    h("td", { class: "left" }, sideBadge(v.long ? "long" : "short")),
    h("td", { class: "lv-c-qty", text: num(v.qty, 4) }),
    h("td", { class: "lv-c-px", text: `${num(v.entry, 2)} → ${num(v.mark, 2)}`, title: "Вход → текущая цена (mark)" }),
    h("td", { class: `lv-res ${t}` }, h("div", { class: "lv-res-usd" }, t ? h("span", { class: "g", "aria-hidden": "true", text: glyph(t) + " " }) : null, susd(v.upnl)),
      h("div", { class: "lv-res-pct muted small", text: frac != null ? `${spctU(frac * 100, 2)} счёта · не зафиксирован` : "не зафиксирован" })),
    h("td", { class: "lv-c-hold" }, opened ? tk("dur", opened * 1000, h("span")) : "—"),
    h("td", { class: "left lv-reason muted", text: "позиция ещё открыта" }));
}

const tkAge = (iso: string): HTMLElement => tk("age", iso, h("span"));

/** «Закрытые сделки»: итоговые суммы окна, открытая позиция сверху и закрытые round trips (новые за последний опрос подсвечены). */
export function closedTradesCard(s: LiveSnapshot, res: StatsResponse, o: ClosedOpts): HTMLElement {
  const all = [...(res.trades?.rows ?? [])].sort((a, b) => b.exit_ts - a.exit_ts || b.n - a.n);
  const shown = all.slice(0, o.limit);
  const open = openRow(s, res);
  const strip = closedStrip(res);
  const total = res.trades?.total ?? all.length;
  const head = h("header", { class: "card-head" }, h("h3", { text: "Закрытые сделки" }),
    h("span", { class: "muted small", text: all.length ? `${shown.length} из ${all.length}${res.trades?.truncated ? ` (всего в окне ${total})` : ""}` : "" }));
  if (!shown.length && !open) {
    return h("section", { class: "card lv-trades-card lv-closed", "aria-label": "Закрытые сделки" }, head, strip,
      emptyState("Закрытых сделок за 30 дней нет", "Когда бот закроет позицию, здесь появится итог в долларах и в процентах счёта.", null));
  }
  const th = (t: string, left = false, c = "", tip = "") => h("th", { class: `${left ? "left" : ""} ${c}`.trim(), scope: "col", title: tip }, h("span", { class: "th-txt", text: t }));
  const body = h("div", { class: "table-wrap lv-trades-wrap", tabindex: 0, role: "region", "aria-label": "Закрытые сделки бота" },
    h("table", { class: "data lv-trades lv-closed-t" },
      h("thead", {}, h("tr", {},
        th("Закрыта (UTC)", true), th("Сторона", true), th("Объём", false, "lv-c-qty"), th("Вход → выход", false, "lv-c-px", "Средняя цена входа → цена выхода, USDT"),
        th("Итог, $ · % счёта", false, "lv-res-h", "Итог сделки в долларах (после комиссий и фандинга; по данным Bybit, а без них — оценка бота) и в процентах кошелька на входе"),
        th("Держалась", false, "lv-c-hold"), th("Причина", true))),
      h("tbody", {}, open, ...shown.map((r) => {
        const t = resultTone(r);
        const rt = resultText(r.net, r.ret_account_pct);
        const key = closedKey(r);
        const fresh = o.fresh.has(key);
        return h("tr", { class: `${t === "pos" ? "tr-win" : t === "neg" ? "tr-loss" : ""}${fresh ? " tr-new" : ""}`, "data-key": key },
          h("td", { class: "left", title: `${isoUtc(r.exit_ts)} (UTC)` },
            h("time", { dateTime: isoUtc(r.exit_ts) }, tkAge(isoUtc(r.exit_ts)), h("span", { class: "lv-t-abs muted", text: ` · ${ddmm(r.exit_ts)} ${hhmm(r.exit_ts)}` })),
            fresh ? h("span", { class: "badge ok lv-new-tag", text: "новая" }) : null),
          h("td", { class: "left" }, sideBadge(r.side)),
          h("td", { class: "lv-c-qty", text: num(r.qty, 4) }),
          h("td", { class: "lv-c-px", text: `${num(r.entry_price, 2)} → ${num(r.exit_price, 2)}` }),
          h("td", { class: `lv-res ${t}` }, h("div", { class: "lv-res-usd" }, t ? h("span", { class: "g", "aria-hidden": "true", text: glyph(t) + " " }) : null, rt.usd),
            h("div", { class: "lv-res-pct muted small", title: r.ret_account_pct == null ? "Нет точки капитала на входе в сделку — процент от счёта посчитать нельзя" : `Кошелёк на входе ≈ ${usd(r.equity_at_entry, 2)}`, text: rt.pct })),
          h("td", { class: "lv-c-hold", text: fmtDuration(r.hold_s) }),
          h("td", { class: "left lv-reason" }, reasonCell(r)));
      }))));
  return h("section", { class: "card lv-trades-card lv-closed", "aria-label": "Закрытые сделки" }, head, strip,
    body,
    all.length > shown.length ? h("button", { class: "btn ghost sm lv-more", type: "button", text: `Показать ещё ${Math.min(20, all.length - shown.length)}`, onClick: o.onMore }) : null,
    h("p", { class: "muted small lv-closed-note" }, "Итог — по данным Bybit; «оценка» — рассчитано ботом, точнее после обновления фандинга; «реконструкция» — закрытие по стопу на бирже, цена выхода оценена. Проценты разных сделок не складываются: суммы — в долларах."));
}
