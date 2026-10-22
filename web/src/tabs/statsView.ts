// Вкладка «Статистика»: построители DOM для плиток, карточек, предупреждений и состояний (ответ -> элемент). Без сети и состояния вкладки.
// Логика вынесена в stats*.ts (statsKpi, statsMoney, statsWarnings, statsPage; тесты — tests/statsLogic.test.ts). Тексты — из словаря stats.*; тексты сервера — только через textContent.
import { getLang, t, type MsgKey, type Params } from "../i18n";
import { reasonLabel, whyLabel } from "../i18n/storedText";
import type { StatsPositionSide, StatsResponse, StatsTradeRow } from "../types";
import { h } from "../ui/dom";
import { badge, disclosure, emptyState, glyph } from "../ui/kit";
import { icon } from "../ui/icons";
import { sideBadge } from "../ui/side";
import { agoMs, int, num, pctU, spctU, susd, toneOf, usd } from "../ui/fmt";
import { cmpView, defText, heroKpis, miniKpis, type CmpView, type Kpi } from "./statsKpi";
import { feeNote, FUNDING_HINT, fundingSkipped, RECON_REASON, warningChips, type WarnChip } from "./statsWarnings";
import { moneyLine, openAccountPct, type MoneySummary } from "./statsMoney";
import { rangeTitle } from "./statsRange";
import { ddmmyy, stamp, ymdLocal } from "../ui/fmtTime";
import { isKnownUnavailable, trimMessage, unavailableCopy } from "./statsPage";

// ---------------------------------------------------------------- мелкие элементы
/** Значок «i» с определением метрики (фокусируемый; текст — в title и aria-label). */
export const infoMark = (text: string): HTMLElement | null =>
  text ? h("span", { class: "st-info", tabindex: 0, role: "img", "aria-label": text, title: text, text: "i" }) : null;

/** Пометка «оценка»: комиссии, фандинг и реконструированные сделки считаются приблизительно (у сделок с данными Bybit её нет). */
const estMark = (title = t("stats.est.tip")): HTMLElement =>
  h("span", { class: "st-est", title, text: t("stats.est.label") });

/** Сообщение со вставленными в него узлами: «{profit}» и т.п. в тексте заменяются элементами (порядок слов задаёт язык). */
function tNodes(key: MsgKey, params: Params, nodes: Record<string, Node>): (string | Node)[] {
  const marks = Object.fromEntries(Object.keys(nodes).map((k) => [k, `\u0001${k}\u0001`]));
  return t(key, { ...params, ...marks }).split(/\u0001(\w+)\u0001/).map((s, i) => (i % 2 ? nodes[s]! : s)).filter((x) => x !== "");
}

export { sideBadge };

/** Строка денег по набору сделок: «Закрыто N: заработано ▲ +A $, потеряно ▼ −B $, итого ▲ ±C $». Числа красятся ТОЛЬКО по знаку P/L. */
export function moneyStrip(s: MoneySummary, label = t("stats.money.closed")): HTMLElement {
  if (!s.count) return h("p", { class: "st-money muted", "data-n": "0", text: t("stats.money.none") });
  const m = (v: number, strong = false) => {
    const tone = toneOf(v, 0.005);
    return h("b", { class: `st-m ${tone}${strong ? " strong" : ""}` }, tone ? h("span", { class: "g", "aria-hidden": "true", text: glyph(tone) + " " }) : null, susd(v));
  };
  return h("p", { class: "st-money", "data-n": String(s.count), "aria-label": moneyLine(s, label) },
    ...tNodes("stats.money.line", { label, n: s.count }, { profit: m(s.profit), loss: m(s.loss), net: m(s.net, true) }));
}

const signed = (tone: string, text: string): string => `${glyph(tone)} ${text}`.trim();

/** Причина закрытия + бейджи «реконструкция» / «оценка» (общая для таблицы статистики и блока закрытых сделок на вкладке live).
 *  «оценка» — ТОЛЬКО когда итог сделки рассчитан ботом (exact=false); итог по данным Bybit (exact=true) без неё. */
export function reasonCell(r: StatsTradeRow): HTMLElement {
  const reason = reasonLabel(r.reason);      // подпись для показа; логика (группировка, «реконструкция») всегда по исходной строке
  return h("span", { class: "st-reason" }, h("span", { class: "st-reason-t", text: reason || "—", title: reason }),
    r.reconstructed ? h("span", { class: "badge warn has-glyph st-mini-badge", title: t("stats.recon.badge_tip") }, h("span", { class: "g", "aria-hidden": "true", text: "!" }), t("stats.recon.badge")) : null,
    !r.exact ? h("span", { class: "badge off st-mini-badge", title: r.reconstructed ? t("stats.est.tip_bot") : t("stats.est.tip_calc") }, t("stats.est.label")) : null);
}

// ---------------------------------------------------------------- дельта к предыдущему окну
function cmpChip(v: CmpView | null): HTMLElement | null {
  if (!v) return null;
  if (v.kind === "na") return h("span", { class: "delta na", title: v.title, role: "img", "aria-label": t("stats.cmp.aria", { title: v.title }), text: "—" });
  const g = v.dir === "up" ? "▲" : v.dir === "down" ? "▼" : "=";
  return h("span", { class: `delta ${v.good}`, title: v.title }, h("span", { class: "g", "aria-hidden": "true", text: g }), v.text);
}

// ---------------------------------------------------------------- плитки
function kpiTile(k: Kpi, res: StatsResponse, hero: boolean): HTMLElement {
  const info = infoMark(defText(res.definitions, k.def, res.config?.taker_fee_est));
  const chip = hero ? cmpChip(cmpView(res, k.cmp)) : null;
  const val = h("div", { class: `metric-value ${k.tone}${k.why ? " muted-val" : ""}`, title: k.why ?? "" },
    k.tone && k.value !== "—" ? h("span", { class: "g", "aria-hidden": "true", text: glyph(k.tone) }) : null, k.value);
  return h("div", { class: `metric big st-tile kpi-${k.key}${k.key === "roi" ? " accent-edge" : ""}`, "data-kpi": k.key },
    h("div", { class: "metric-top" }, h("div", { class: "st-lab" }, h("span", { class: "metric-label", text: k.label }), info, k.est ? estMark() : null), chip),
    val,
    k.sub ? h("div", { class: "metric-sub", text: k.sub }) : null);
}

export function heroGrid(res: StatsResponse): HTMLElement {
  const cmpNote = res.comparison?.available && res.comparison.prev_start && res.comparison.prev_end
    ? t("stats.cmp.note", { from: ddmmyy(res.comparison.prev_start), to: ddmmyy(res.comparison.prev_end) }) : null;
  return h("div", { class: "st-hero-wrap" },
    h("div", { class: "st-hero" }, ...heroKpis(res).map((k) => kpiTile(k, res, true))),
    cmpNote ? h("p", { class: "muted small st-cmp-note", text: cmpNote }) : null);
}

function miniGrid(res: StatsResponse): HTMLElement {
  return h("div", { class: "minis st-minis" }, ...miniKpis(res).map((k) =>
    h("div", { class: "mini", "data-kpi": k.key, title: k.why ?? "" },
      h("div", { class: "st-lab" }, h("span", { class: "metric-label", text: k.label }), infoMark(defText(res.definitions, k.def, res.config?.taker_fee_est)), k.est ? estMark() : null),
      h("div", { class: `mini-val ${k.tone}${k.why ? " muted-val" : ""}` }, k.tone && k.value !== "—" ? h("span", { class: "g", "aria-hidden": "true", text: glyph(k.tone) }) : null, k.value),
      k.sub ? h("div", { class: "metric-sub", text: k.sub }) : null)));
}

export const allMetrics = (res: StatsResponse): HTMLElement =>
  disclosure(t("stats.all_metrics"), [miniGrid(res)], { key: "stats.all", defaultOpen: window.innerWidth >= 1100 });

// ---------------------------------------------------------------- заголовок страницы: окно, качество данных, среда
export function windowLine(res: StatsResponse): HTMLElement | null {
  const w = res.window;
  if (!w) return null;
  const days = w.length_s / 86400;
  const len = days >= 1 ? t("stats.window.days", { n: num(days, days < 10 ? 1 : 0) }) : t("stats.window.hours", { n: num(w.length_s / 3600, 1) });
  const partial = res.equity?.starts_before_data || (w.effective_start - w.start) > 60;
  const parts: (string | HTMLElement)[] = [
    h("span", { class: "st-win-main" }, icon("clock", 14), `${rangeTitle(res.range)}: `, h("b", { text: `${ymdLocal(w.start_date)} – ${ymdLocal(w.end_date)}` }), ` (UTC, ${len})`),
  ];
  if (partial) {
    const eff = res.equity?.eq_start_ts ?? w.effective_start;
    parts.push(h("span", { class: "st-win-note", title: t("stats.window.data_from_tip") }, t("stats.window.data_from", { t: stamp(eff) })));
  }
  return h("div", { class: "st-window muted small" }, ...parts);
}

/** Строка чипов «Качество данных» + сворачиваемый список с полными текстами. */
export function qualityBlock(res: StatsResponse): HTMLElement {
  const chips = warningChips(res.warnings);
  if (!chips.length) {
    return h("div", { class: "st-quality ok" }, badge(t("stats.quality.ok"), "ok", { glyph: "✓", title: t("stats.quality.ok_tip") }));
  }
  const chip = (c: WarnChip) => badge(c.label, c.level === "warn" ? "warn" : "info", { glyph: c.level === "warn" ? "!" : "i", title: `${c.text}${c.hint ? "\n" + c.hint : ""}` });
  const nWarn = chips.filter((c) => c.level === "warn").length;
  const list = h("ul", { class: "st-qlist" }, ...chips.map((c) => h("li", { class: c.level },
    h("div", { class: "st-q-head" }, chip(c)),
    h("p", { class: "st-q-text", text: c.text }),
    c.hint ? h("p", { class: "st-q-hint muted small", text: c.hint }) : null)));
  return h("div", { class: "st-quality" },
    h("div", { class: "st-qchips", role: "list", "aria-label": t("stats.quality.title") }, ...chips.map((c) => { const b = chip(c); b.setAttribute("role", "listitem"); return b; })),
    disclosure(t("stats.quality.title"), [list], { key: "stats.quality", badge: nWarn ? t("stats.quality.warns", { n: nWarn }) : t("stats.quality.notes", { n: chips.length }) }));
}

// ---------------------------------------------------------------- баннеры и заметки над содержимым
const posText = (p: StatsPositionSide | null | undefined): string => (p ? `${p.side === "long" ? t("stats.side.long") : t("stats.side.short")} ${num(p.qty, 4)} BTC` : t("stats.pos.none_short"));

export function pageNotices(res: StatsResponse): HTMLElement[] {
  const out: HTMLElement[] = [];
  const mm = res.position_mismatch;
  if (mm) {
    out.push(h("div", { class: "banner warn st-mismatch", role: "alert" },
      h("strong", { text: t("stats.mismatch.title") }),
      h("span", {}, t("stats.mismatch.detail", { replayed: posText(mm.replayed), actual: posText(mm.actual) })),
      h("p", { class: "st-mm-p" }, t("stats.mismatch.p1"), t("stats.mismatch.p2"))));
  }
  const n = res.kpis?.trades ?? 0;
  if (res.available && n === 0) {
    out.push(h("div", { class: "notice st-zero", role: "note" }, h("strong", { text: t("stats.zero.title") }),
      h("span", { text: t("stats.zero.text") })));
  } else if (res.available && res.warnings.some((w) => w.code === "few_trades")) {
    out.push(h("div", { class: "notice warn st-few", role: "note" }, h("strong", { text: t("stats.few.title", { n }) }),
      h("span", { text: t("stats.few.text") })));
  }
  return out;
}

// ---------------------------------------------------------------- карточки
const card = (cls: string, title: string, body: (Node | null)[], head?: (Node | null)[], label = title): HTMLElement =>
  h("section", { class: `card ${cls}`, "aria-label": label }, h("header", { class: "card-head" }, h("h3", { text: title }), ...(head ?? [])), ...body);

const kv = (label: string, value: string | Node, tone = "", extra?: Node | null, tip?: string): HTMLElement =>
  h("div", { class: "st-kv" }, h("dt", { title: tip ?? "" }, label, extra ?? null), h("dd", { class: tone }, value));

export function positionCard(res: StatsResponse): HTMLElement {
  const p = res.open_position;
  if (!p) {
    return card("st-pos is-flat", t("stats.pos.title"), [h("div", { class: "st-flat" },
      h("div", { class: "empty-icon", "aria-hidden": "true" }, icon("flat", 22)),
      h("p", { class: "strong", text: t("stats.pos.flat_title") }),
      h("p", { class: "muted", text: t("stats.pos.flat_hint") }))],
    [h("span", { class: "lv-side flat st-flat-tag" }, h("span", { "aria-hidden": "true", text: "■ " }), "FLAT")]);
  }
  const tu = toneOf(p.upnl, 0.005), tn = toneOf(p.net_est, 0.005);
  const acct = openAccountPct(p.upnl, res.equity?.eq_end);      // % от кошелька сейчас (дополняет доллары)
  const opened = p.opened_ts
    ? h("span", {}, `${stamp(p.opened_ts)} UTC`, h("span", { class: "muted", text: ` · ${agoMs(p.opened_ts * 1000)}` }))
    : h("span", { class: "muted", text: t("stats.pos.opened_unknown") });
  return card("st-pos", t("stats.pos.title"), [
    h("div", { class: "st-pos-top" },
      h("div", {}, h("div", { class: "st-big", text: `${num(p.qty, 4)} BTC` }), h("div", { class: "muted small", text: t("stats.pos.notional", { v: usd(p.notional, 2) }) })),
      h("div", { class: `st-pos-pnl ${tu}` }, h("div", { class: "st-big" }, tu ? h("span", { class: "g", "aria-hidden": "true", text: glyph(tu) + " " }) : null, susd(p.upnl)),
        h("div", { class: "muted small st-pos-pct", text: acct != null ? t("stats.money.of_account", { v: spctU(acct, 2) }) : "" }),
        h("div", { class: "muted small", text: t("stats.pos.upnl") }))),
    h("dl", { class: "st-kvs" },
      kv(t("stats.pos.entry"), num(p.entry, 2)), kv("Mark", num(p.mark, 2)),
      kv(t("stats.pos.entry_fee"), usd(p.fees_paid_est, 2), "", estMark()),
      kv(t("stats.pos.funding"), usd(p.funding_est, 2), "", estMark(p.funding_complete ? undefined : t("stats.pos.funding_part"))),
      kv(t("stats.pos.net"), signed(tn, susd(p.net_est)), tn, estMark(), t("stats.pos.net_tip")),
      kv(t("stats.pos.opened"), opened)),
    p.funding_complete ? null : h("p", { class: "muted small st-note", text: t("stats.pos.funding_incomplete") + FUNDING_HINT }),
  ], [p.side ? sideBadge(p.side) : null], t("stats.pos.title"));
}

export function sideCard(res: StatsResponse): HTMLElement {
  const by = res.breakdown?.by_side ?? [];
  const get = (k: string) => by.find((x) => x.key === k);
  const l = get("long"), s = get("short");
  const total = (l?.count ?? 0) + (s?.count ?? 0);
  if (!total) return card("st-sides", t("stats.sides.title"), [emptyState(t("stats.sides.empty"), t("stats.sides.empty_hint"), null, { icon: "swap" })]);
  const tile = (side: "long" | "short", g: typeof l) => {
    const n = g?.count ?? 0, tone = toneOf(g?.net, 0.005);
    return h("div", { class: `st-side-tile ${side}` },
      h("div", { class: "st-side-top" }, sideBadge(side), h("span", { class: "muted small", text: t("stats.sides.count", { n }) })),
      h("div", { class: `st-side-net ${tone}` }, signed(tone, susd(g?.net)), estMark()),
      h("div", { class: "st-side-sub" }, h("span", { class: "muted", text: "Win rate " }), h("b", { text: n ? pctU(g?.win_rate_pct, 0) : "—" }),
        h("span", { class: "muted", text: ` · ${t("stats.sides.avg")} ` }), h("b", { text: n ? susd(g?.avg_net) : "—" })));
  };
  const lp = total ? ((l?.count ?? 0) / total) * 100 : 0;
  const bar = h("div", { class: "st-split", role: "img", "aria-label": t("stats.sides.bar_aria", { l: l?.count ?? 0, s: s?.count ?? 0 }) },
    l?.count ? h("i", { class: "long", style: `width:${lp}%` }) : null, s?.count ? h("i", { class: "short", style: `width:${100 - lp}%` }) : null);
  return card("st-sides", t("stats.sides.title"), [
    h("div", { class: "st-side-grid" }, tile("long", l), tile("short", s)), bar,
    h("div", { class: "st-split-cap muted small" }, h("span", { text: `${t("stats.side.long")} ${l?.count ?? 0} (${num(lp, 0)}%)` }), h("span", { text: `${t("stats.side.short")} ${s?.count ?? 0} (${num(100 - lp, 0)}%)` }))]);
}

export function reasonCard(res: StatsResponse): HTMLElement {
  const rows = [...(res.breakdown?.by_reason ?? [])].sort((a, b) => b.count - a.count);
  if (!rows.length) return card("st-reasons", t("stats.reasons.title"), [emptyState(t("stats.sides.empty"), undefined, null, { icon: "inbox" })]);
  const maxAbs = Math.max(1e-9, ...rows.map((r) => Math.abs(r.net ?? 0)));
  const tbl = h("table", { class: "data st-rt" },
    h("caption", { class: "sr-only", text: t("stats.reasons.caption") }),
    h("thead", {}, h("tr", {},
      ...[[t("stats.reasons.reason"), "left"], [t("stats.reasons.trades"), ""], ["Win rate", ""], ["Net", "st-net-h"], [t("stats.reasons.avg_trade"), ""]].map(([label, c]) => h("th", { class: c, scope: "col" }, h("span", { class: "th-txt", text: label }))))),
    h("tbody", {}, ...rows.map((r) => {
      const tone = toneOf(r.net, 0.005);
      return h("tr", {},
        h("td", { class: "left st-reason-k" }, whyLabel(r.key) || "—", r.key.includes("реконструкция") ? h("span", { class: "badge off st-mini-badge", text: t("stats.est.label"), title: t("stats.est.tip_exit") }) : null),
        h("td", { text: int(r.count) }), h("td", { text: pctU(r.win_rate_pct, 0) }),
        h("td", { class: `st-net ${tone}` }, h("span", { class: "st-bar", "aria-hidden": "true" }, h("i", { class: tone, style: `width:${Math.max(3, (Math.abs(r.net ?? 0) / maxAbs) * 100)}%` })), h("span", { class: "st-net-v", text: signed(tone, susd(r.net)) })),
        h("td", { text: susd(r.avg_net) }));
    })));
  return card("st-reasons", t("stats.reasons.title"), [h("div", { class: "table-wrap", tabindex: 0, role: "region", "aria-label": t("stats.reasons.title") }, tbl)],
    [h("span", { class: "muted small", text: t("stats.reasons.group_note") })]);
}

export function costsCard(res: StatsResponse): HTMLElement {
  const k = res.kpis, c = res.config;
  const total = k && (k.fees_total != null || k.funding_total != null) ? (k.fees_total ?? 0) + (k.funding_total ?? 0) : null;
  const note = feeNote(c?.taker_fee_est);
  const stale = res.warnings.find((w) => w.code === "funding_stale");
  const def = (key: string) => defText(res.definitions, key, c?.taker_fee_est);
  return card("st-costs", t("stats.costs.title"), [
    h("dl", { class: "st-kvs" },
      kv(t("stats.costs.fees"), usd(k?.fees_total, 2), "", estMark(), def("fees_est")),
      kv(t("stats.costs.funding"), usd(k?.funding_total, 2), "", estMark(), def("funding_est")),
      kv(t("stats.costs.total"), usd(total, 2), "st-strong"),
      kv(t("stats.costs.fees_gross"), pctU(k?.fees_pct_of_gross_profit, 1), "", null, def("fees_pct_of_gross_profit")),
      kv(t("stats.costs.turnover"), usd(k?.turnover, 0), "", null, def("turnover")),
      kv(t("stats.costs.taker"), c ? `${num(c.taker_fee_est * 100, c.taker_fee_est * 100 < 0.1 ? 3 : 2)}%` : "—", "", null, t("stats.costs.taker_tip"))),
    note ? h("div", { class: "notice st-fee-note", role: "note" }, h("span", { text: note })) : null,
    stale ? h("p", { class: "muted small st-note", text: `${fundingSkipped(stale.trades)} ${FUNDING_HINT}` }) : null,
  ]);
}

export function reconCard(res: StatsResponse): HTMLElement | null {
  const r = res.reconciliation;
  if (!r) return null;
  const head = [infoMark(defText(res.definitions, "reconciliation", res.config?.taker_fee_est))];
  if (r.unexplained == null) {
    return card("st-recon", t("stats.recon.title"), [
      emptyState(t("stats.recon.unavailable"), RECON_REASON[r.reason ?? ""] ?? t("stats.recon.unavailable_hint"), null, { icon: "info" })], head);
  }
  const col = (label: string, val: number | null, hint: string, est = false) => {
    const tone = toneOf(val, 0.005);
    return h("div", { class: "st-rc" }, h("div", { class: "st-rc-l muted", text: label }), h("div", { class: `st-rc-v ${tone}` }, signed(tone, susd(val))), est ? estMark() : null, h("div", { class: "st-rc-s muted small", text: hint }));
  };
  const op = (s: string) => h("div", { class: "st-op", "aria-hidden": "true", text: s });
  return card("st-recon", t("stats.recon.title"), [
    h("div", { class: "st-recon-eq" },
      col(t("stats.recon.wallet_delta"), r.wallet_delta, t("stats.recon.wallet_delta_sub"), false), op("−"),
      col(t("stats.recon.bot_pnl"), r.bot_pnl_est, t("stats.recon.bot_pnl_sub"), true), op("="),
      col(t("stats.recon.unexplained"), r.unexplained, t("stats.recon.unexplained_sub"))),
    h("p", { class: "st-note muted" }, h("strong", { text: t("stats.recon.not_bot_error") }),
      t("stats.recon.explain"),
      r.flat_at_start === false ? t("stats.recon.open_at_start") : r.flat_at_start === true ? t("stats.recon.flat_at_start") : ""),
  ], head);
}

// ---------------------------------------------------------------- состояния страницы
export function unavailableCard(reason: string, message: string, onRetry: () => void): HTMLElement {
  const u = unavailableCopy(reason);
  // серверное сообщение — русский запасной текст: на другом языке для известных причин его заменяют заголовок и пояснение интерфейса
  const shown = getLang() === "ru" || !isKnownUnavailable(reason) ? trimMessage(u.title, message) : "";
  return h("section", { class: "card st-state st-unavail", role: "status", "aria-label": u.title, "data-reason": reason },
    h("div", { class: "empty-icon", "aria-hidden": "true" }, icon(u.icon, 26)),
    h("h3", { text: u.title }),
    shown ? h("p", { class: "strong", text: shown }) : null,
    h("p", { class: "muted", text: u.hint }),
    h("button", { class: "btn", type: "button", onClick: onRetry }, icon("refresh", 14), t("stats.refresh")),
    h("p", { class: "muted small st-code" }, t("stats.unavail.code"), h("code", { text: reason })));
}
