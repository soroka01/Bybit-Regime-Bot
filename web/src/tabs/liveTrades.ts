// Вкладка «Бот (live)»: «Закрытые сделки» (итоги окна 30 дн. из /api/stats) и откат к списку ордеров снимка.
import { t } from "../i18n";
import { reasonLabel } from "../i18n/storedText";
import type { LiveSnapshot, StatsResponse } from "../types";
import { h } from "../ui/dom";
import { datetime, num, parseTime, spctU, susd, toneOf, usd } from "../ui/fmt";
import { emptyState, glyph } from "../ui/kit";
import { orderBadge, sideBadge } from "../ui/side";
import { closedKey, isBuy, positionView, upnlAccountFrac, type LiveTrade } from "./liveCalc";
import { tk } from "./liveTick";
import { ddmm, fmtDuration, hhmm, isoUtc } from "../ui/fmtTime";
import { periodSummary, resultText, resultTone } from "./statsMoney";
import { reasonCell } from "./statsView";

/** Что показывает блок сделок: ждём первый ответ /api/stats, закрытые сделки готовы, либо откат к списку ордеров (ошибка / нет статистики). */
export type ClosedState =
  | { kind: "loading" }
  | { kind: "ok"; res: StatsResponse }
  | { kind: "fallback"; why: "error" | "unavailable"; message?: string };

/** Подсказка над списком ордеров, когда закрытые сделки получить нельзя. */
export function fallbackHint(st: Extract<ClosedState, { kind: "fallback" }>): string {
  if (st.why === "unavailable") return t("live.fb_unavail", { msg: st.message ? `: ${st.message}` : "" });
  return t("live.fb_error", { msg: st.message ? ` (${st.message})` : "" });
}

/** Список отдельных ордеров (fills) — прежняя таблица «Последние сделки»; hint — пояснение, почему показаны ордера, а не итоги. */
export function tradesCard(trades: readonly LiveTrade[] | undefined, limit: number, onMore: () => void, hint: string | null = null): HTMLElement {
  const all = [...(trades ?? [])].sort((a, b) => +(parseTime(b.ts) ?? 0) - +(parseTime(a.ts) ?? 0));
  const shown = all.slice(0, limit);
  const body = shown.length ? h("div", { class: "table-wrap lv-trades-wrap", tabindex: 0, role: "region", "aria-label": t("live.orders_aria") },
    h("table", { class: "data lv-trades" },
      h("thead", {}, h("tr", {}, ...([[t("live.h_time"), true, ""], [t("live.h_order"), true, ""], [t("live.h_qty"), false, ""], [t("live.h_price"), false, ""], [t("live.h_notional"), false, "lv-notional"], [t("live.h_reason"), true, ""]] as [string, boolean, string][]).map(([label, l, c]) => h("th", { class: `${l ? "left" : ""} ${c}`.trim(), scope: "col" }, h("span", { class: "th-txt", text: label }))))),
      h("tbody", {}, ...shown.map((tr) => {
        const d = parseTime(tr.ts);
        return h("tr", {},
          h("td", { class: "left", title: datetime(tr.ts) }, h("time", { dateTime: d?.toISOString() ?? "" }, tk("age", tr.ts, h("span")), h("span", { class: "lv-t-abs muted", text: d ? ` · ${ddmm(+d / 1000)} ${hhmm(+d / 1000)}` : "" }))),
          h("td", { class: "left" }, orderBadge(isBuy(tr.side))),
          h("td", { text: num(tr.qty, 4) }), h("td", { text: num(tr.price, 2) }), h("td", { class: "lv-notional", text: usd(tr.qty * tr.price, 2) }),
          h("td", { class: "left lv-reason", text: reasonLabel(tr.reason) || "—" }));
      }))))
    : emptyState(t("live.trades_none_t"), t("live.trades_none_h"), null);
  return h("section", { class: "card lv-trades-card", "aria-label": t("live.trades") },
    h("header", { class: "card-head" }, h("h3", { text: t("live.trades_last") }), h("span", { class: "muted small", text: all.length ? t("live.n_of", { a: shown.length, b: all.length }) : "" })),
    hint ? h("p", { class: "notice lv-trades-hint", role: "note" }, h("span", { text: hint })) : null,
    body,
    all.length > shown.length ? h("button", { class: "btn ghost sm lv-more", type: "button", text: t("live.show_more", { n: Math.min(20, all.length - shown.length) }), onClick: onMore }) : null);
}

/** Скелетон блока «Закрытые сделки» до первого ответа. */
export function closedTradesLoading(): HTMLElement {
  return h("section", { class: "card lv-trades-card lv-closed", "aria-label": t("live.closed"), "aria-busy": "true" },
    h("header", { class: "card-head" }, h("h3", { text: t("live.closed") })),
    h("div", { class: "skeleton skel-line", style: "height:34px;margin-bottom:8px" }), h("div", { class: "skeleton skel-line", style: "height:120px" }));
}

interface ClosedOpts { limit: number; onMore(): void; fresh: ReadonlySet<string> }

const signedM = (v: number, strong = false): HTMLElement => {
  const tone = toneOf(v, 0.005);
  return h("b", { class: `st-m ${tone}${strong ? " strong" : ""}` }, tone ? h("span", { class: "g", "aria-hidden": "true", text: glyph(tone) + " " }) : null, susd(v));
};

/** Шапка: «За 30 дн.: закрыто N · плюс +A $ · минус −B $ · итого ±C $» (+ ROI счёта за окно — отдельной приглушённой подписью). */
function closedStrip(res: StatsResponse): HTMLElement | null {
  const sm = periodSummary(res);
  if (!sm) return null;
  const dot = () => h("span", { class: "lv-dot muted", "aria-hidden": "true", text: "·" });
  const roi = res.equity?.wallet_return_pct;
  return h("div", { class: "lv-strip", role: "group", "aria-label": t("live.strip_aria") },
    h("span", { class: "lv-strip-l", text: `${t("live.range30")}:` }),
    h("span", {}, `${t("live.s_closed")} `, h("b", { text: String(sm.count) })), dot(),
    h("span", { class: "lv-strip-p" }, `${t("live.s_profit")} `, signedM(sm.profit)), dot(),
    h("span", { class: "lv-strip-m" }, `${t("live.s_loss")} `, signedM(sm.loss)), dot(),
    h("span", { class: "lv-strip-t" }, `${t("live.s_net")} `, signedM(sm.net, true)),
    roi != null ? h("span", { class: "lv-strip-roi muted small", title: t("live.roi_tip") }, t("live.roi", { v: spctU(roi, 2) })) : null);
}

/** Строка открытой позиции — всегда первая: нереализованный результат (ещё не зафиксирован). */
function openRow(s: LiveSnapshot, res: StatsResponse): HTMLElement | null {
  const v = positionView(s.position, s.settings);
  if (!v) return null;
  const tone = toneOf(v.upnl, 0.005);
  const frac = upnlAccountFrac(v.upnl, s.equity);
  const opened = res.open_position?.opened_ts ?? null;
  return h("tr", { class: "lv-open-row", "data-open": "1" },
    h("td", { class: "left" }, h("span", { class: "badge info lv-open-tag", text: t("live.open_tag"), title: t("live.open_tag_tip") }),
      opened ? h("div", { class: "lv-t-abs muted small" }, t("live.since", { t: `${ddmm(opened)} ${hhmm(opened)} UTC` })) : null),
    h("td", { class: "left" }, sideBadge(v.long ? "long" : "short")),
    h("td", { class: "lv-c-qty", text: num(v.qty, 4) }),
    h("td", { class: "lv-c-px", text: `${num(v.entry, 2)} → ${num(v.mark, 2)}`, title: t("live.px_tip") }),
    h("td", { class: `lv-res ${tone}` }, h("div", { class: "lv-res-usd" }, tone ? h("span", { class: "g", "aria-hidden": "true", text: glyph(tone) + " " }) : null, susd(v.upnl)),
      h("div", { class: "lv-res-pct muted small", text: frac != null ? t("live.unreal_pct", { p: spctU(frac * 100, 2) }) : t("live.unrealized") })),
    h("td", { class: "lv-c-hold" }, opened ? tk("dur", opened * 1000, h("span")) : "—"),
    h("td", { class: "left lv-reason muted", text: t("live.pos_open_note") }));
}

const tkAge = (iso: string): HTMLElement => tk("age", iso, h("span"));

/** «Закрытые сделки»: итоговые суммы окна, открытая позиция сверху и закрытые round trips (новые за последний опрос подсвечены). */
export function closedTradesCard(s: LiveSnapshot, res: StatsResponse, o: ClosedOpts): HTMLElement {
  const all = [...(res.trades?.rows ?? [])].sort((a, b) => b.exit_ts - a.exit_ts || b.n - a.n);
  const shown = all.slice(0, o.limit);
  const open = openRow(s, res);
  const strip = closedStrip(res);
  const total = res.trades?.total ?? all.length;
  const head = h("header", { class: "card-head" }, h("h3", { text: t("live.closed") }),
    h("span", { class: "muted small", text: all.length ? `${t("live.n_of", { a: shown.length, b: all.length })}${res.trades?.truncated ? ` ${t("live.in_window", { n: total })}` : ""}` : "" }));
  if (!shown.length && !open) {
    return h("section", { class: "card lv-trades-card lv-closed", "aria-label": t("live.closed") }, head, strip,
      emptyState(t("live.closed_none_t"), t("live.closed_none_h"), null));
  }
  const th = (label: string, left = false, c = "", tip = "") => h("th", { class: `${left ? "left" : ""} ${c}`.trim(), scope: "col", title: tip }, h("span", { class: "th-txt", text: label }));
  const body = h("div", { class: "table-wrap lv-trades-wrap", tabindex: 0, role: "region", "aria-label": t("live.closed_aria") },
    h("table", { class: "data lv-trades lv-closed-t" },
      h("thead", {}, h("tr", {},
        th(t("live.h_closed"), true), th(t("live.h_side"), true), th(t("live.h_qty"), false, "lv-c-qty"), th(t("live.h_px"), false, "lv-c-px", t("live.h_px_tip")),
        th(t("live.h_result"), false, "lv-res-h", t("live.h_result_tip")),
        th(t("live.h_hold"), false, "lv-c-hold"), th(t("live.h_reason"), true))),
      h("tbody", {}, open, ...shown.map((r) => {
        const tone = resultTone(r);
        const rt = resultText(r.net, r.ret_account_pct);
        const key = closedKey(r);
        const fresh = o.fresh.has(key);
        return h("tr", { class: `${tone === "pos" ? "tr-win" : tone === "neg" ? "tr-loss" : ""}${fresh ? " tr-new" : ""}`, "data-key": key },
          h("td", { class: "left", title: `${isoUtc(r.exit_ts)} (UTC)` },
            h("time", { dateTime: isoUtc(r.exit_ts) }, tkAge(isoUtc(r.exit_ts)), h("span", { class: "lv-t-abs muted", text: ` · ${ddmm(r.exit_ts)} ${hhmm(r.exit_ts)}` })),
            fresh ? h("span", { class: "badge ok lv-new-tag", text: t("live.new_tag") }) : null),
          h("td", { class: "left" }, sideBadge(r.side)),
          h("td", { class: "lv-c-qty", text: num(r.qty, 4) }),
          h("td", { class: "lv-c-px", text: `${num(r.entry_price, 2)} → ${num(r.exit_price, 2)}` }),
          h("td", { class: `lv-res ${tone}` }, h("div", { class: "lv-res-usd" }, tone ? h("span", { class: "g", "aria-hidden": "true", text: glyph(tone) + " " }) : null, rt.usd),
            h("div", { class: "lv-res-pct muted small", title: r.ret_account_pct == null ? t("live.no_eq_tip") : t("live.eq_entry_tip", { v: usd(r.equity_at_entry, 2) }), text: rt.pct })),
          h("td", { class: "lv-c-hold", text: fmtDuration(r.hold_s) }),
          h("td", { class: "left lv-reason" }, reasonCell(r)));
      }))));
  return h("section", { class: "card lv-trades-card lv-closed", "aria-label": t("live.closed") }, head, strip,
    body,
    all.length > shown.length ? h("button", { class: "btn ghost sm lv-more", type: "button", text: t("live.show_more", { n: Math.min(20, all.length - shown.length) }), onClick: o.onMore }) : null,
    h("p", { class: "muted small lv-closed-note" }, t("live.closed_note")));
}
