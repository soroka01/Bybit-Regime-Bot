// Карточки «Лучших стратегий»: чистая отрисовка (данные + контекст -> DOM) без состояния и сетевых вызовов.
import { t, tDyn } from "../i18n";
import { strategyDesc } from "../i18n/serverText";
import type { LbItem, LbPlanItem, Market, Metrics, Num } from "../types";
import { h, svg } from "../ui/dom";
import { int, num, pct, sgn, spct, utcShort } from "../ui/fmt";
import { icon } from "../ui/icons";
import { glyph } from "../ui/kit";
import { verdictBadge } from "../ui/modal";
import { MIN_OOS_DAYS } from "../ui/verdicts";
import type { Entry } from "./lbState";

/** Настройки, при которых посчитаны карточки, и обработчики кнопок (их подставляет вкладка). */
export interface CardCtx {
  symbol: string; tf: string; days: number; longOnly: boolean; leverage: number; market: Market;
  /** Комиссия и проскальзывание в процентах (для «Копировать для бота»). */
  feePct: number; slipPct: number;
  onOpenLab(it: LbItem): void;
  onCopy(it: LbItem): void;
  onSend(it: LbItem): void;
}

const utc = (s: string | null | undefined) => (s ? utcShort(s) + " UTC" : "—");

/** Запасной путь для старого сервера без reason_code: русские тексты причин (web/research.py) -> стабильные коды. */
const REASON_CODES: Readonly<Record<string, string>> = {
  "нет свечей — скачайте данные": "no_klines",
  "нет данных фандинга/OI/LSR": "no_derivs",
  "недостаточно сделок на in-sample для надёжного подбора": "few_trades",
};

/** Причина с сервера на языке интерфейса: по reason_code, иначе по точному тексту сервера; неизвестный текст показывается как есть. */
export function lbReasonText(raw: string | null | undefined, code?: string | null): string {
  const c = code || (raw ? REASON_CODES[raw] : undefined);
  return (c ? tDyn(`leaderboard.reason.${c}`) : undefined) ?? raw ?? "";
}

let sparkUid = 0;

/** Спарклайн капитала на OOS против Buy & Hold: тонкая линия, мягкая заливка под основным рядом, точка на конце; цвета — токены. */
export function spark(it: LbItem): SVGElement | null {
  const c = it.result?.curve;
  if (!c || c.t.length < 3) return null;
  const W = 220, H = 56;
  const fin = (ys: Num[]) => ys.filter((y): y is number => y != null && Number.isFinite(y));
  const all = [...fin(c.equity), ...fin(c.bh)];
  if (all.length < 3) return null;                          // сервер отдаёт NaN как null — без данных рисовать нечего
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const X = (i: number, n: number) => (4 + (i / (n - 1)) * (W - 8));
  const Y = (y: number) => H - 5 - ((y - lo) / span) * (H - 12);
  // null-точки пропускаются (линия просто соединяет соседние валидные)
  const xy = (ys: Num[]) => ys.flatMap((y, i) => (y != null && Number.isFinite(y) ? [[X(i, ys.length), Y(y)] as [number, number]] : []));
  const pts = (ys: Num[]) => xy(ys).map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const eq = xy(c.equity);
  const gid = `spk${++sparkUid}`;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "spark", role: "img", "aria-label": t("leaderboard.card.spark_aria") });
  const grad = svg("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 },
    svg("stop", { offset: "0%", "stop-color": "var(--c1)", "stop-opacity": 0.28 }), svg("stop", { offset: "100%", "stop-color": "var(--c1)", "stop-opacity": 0 }));
  root.append(svg("defs", {}, grad));
  if (eq.length > 1) {
    const d = `M${eq[0]![0].toFixed(1)},${(H - 2).toFixed(1)} ` + eq.map(([x, y]) => `L${x.toFixed(1)},${y.toFixed(1)}`).join(" ") + ` L${eq[eq.length - 1]![0].toFixed(1)},${(H - 2).toFixed(1)} Z`;
    root.append(svg("path", { d, fill: `url(#${gid})`, stroke: "none" }));
  }
  root.append(
    svg("polyline", { points: pts(c.bh), fill: "none", stroke: "var(--ref)", "stroke-width": 1.25, "stroke-dasharray": "4 3", "stroke-linecap": "round" }),
    svg("polyline", { points: pts(c.equity), fill: "none", stroke: "var(--c1)", "stroke-width": 1.75, "stroke-linecap": "round", "stroke-linejoin": "round" }));
  const end = eq[eq.length - 1];
  if (end) root.append(svg("circle", { cx: end[0], cy: end[1], r: 3.2, fill: "var(--c1)", stroke: "var(--surface-2)", "stroke-width": 1.5 }));
  return root;
}

const mini = (label: string, value: string, tone: string, dim: string, sub?: string, tip?: string) =>
  h("div", { class: "mini", title: tip ?? "" },
    h("div", { class: "metric-label", text: label }),
    h("div", { class: "mini-val " + tone }, tone ? h("span", { class: "g", "aria-hidden": "true", text: glyph(tone) }) : null, (tip ? "~" : "") + value),
    sub ? h("div", { class: "metric-sub", text: sub }) : null,
    h("div", { class: "dimmed", text: `IS ${dim}` }));

/** Одна из трёх «героических» цифр карточки: подпись, крупное значение (со знаком ▲/▼ — цвет не единственный носитель), строки-пояснения. */
const heroNum = (label: string, value: string, tone: string, subs: string[], tip?: string) =>
  h("div", { class: "lb-num", title: tip ?? "" },
    h("div", { class: "lb-num-l", text: label }),
    h("div", { class: `lb-num-v ${tone}` }, tone ? h("span", { class: "g", "aria-hidden": "true", text: glyph(tone) }) : null, (tip ? "~" : "") + value),
    ...subs.map((t) => h("div", { class: "lb-num-s", text: t })));

/** Медаль для первой тройки (число внутри — смысл не держится на цвете), у остальных — просто номер. */
const rankBadge = (rank: number): HTMLElement =>
  h("div", { class: "lb-rank" }, h("span", { class: rank <= 3 ? `medal m${rank}` : "rank-n", title: rank <= 3 ? t("leaderboard.card.rank_title", { rank }) : "", text: String(rank) }));

/** Отпечаток карточки: если не изменился — DOM переиспользуется (см. ui/keyed.ts). */
export function itemSig(rank: number, it: LbItem, ctx: CardCtx): string {
  const r = it.result;
  return JSON.stringify([
    rank, ctx.symbol, ctx.tf, ctx.days, ctx.longOnly, ctx.leverage, ctx.market, ctx.feePct, ctx.slipPct,
    it.computed_at, it.from_cache, it.data_last, it.short_oos, it.overlays, r?.verdict, r?.params, r?.oos?.total_return, r?.is?.total_return,
  ]);
}

export function itemCard(rank: number, it: LbItem, ctx: CardCtx): HTMLElement {
  const r = it.result!;
  const o: Metrics = r.oos, i: Metrics = r.is;
  const overlays = it.overlays ?? { htf: null, stop: null };
  const extra = [overlays.htf ? `HTF EMA(${overlays.htf})` : "", overlays.stop ? t("leaderboard.card.atr_stop", { n: overlays.stop }) : ""].filter(Boolean);
  const sp = spark(it);
  const oosDays = it.oos_days ?? it.oos_range?.days ?? 0;
  const tip = it.short_oos ? t("leaderboard.card.annualized", { days: Math.round(oosDays) }) : undefined;
  const tooShort = r.verdict === "few" && oosDays > 0 && oosDays < MIN_OOS_DAYS;
  const bh = it.buy_hold_oos ?? o.buy_hold_return;
  return h("article", { class: `lb-card ${r.verdict}${rank <= 3 ? ` top top${rank}` : ""}`, "aria-label": `${rank}. ${it.strategy}` },
    rankBadge(rank),
    h("div", { class: "lb-main" },
      h("div", { class: "lb-title" },
        h("h3", { text: it.strategy }), verdictBadge(r.verdict, tooShort ? t("leaderboard.card.too_short") : undefined),
        it.derivative ? h("span", { class: "badge info", text: t("leaderboard.card.derivative") }) : null,
        it.from_cache ? h("span", { class: "cache-chip", title: t("leaderboard.card.cached_tip", { when: it.computed_at ? utc(it.computed_at) : t("leaderboard.card.earlier"), data: utc(it.data_last) }) }, icon("clock", 12), t("leaderboard.card.cached")) : null),
      h("p", { class: "lb-desc muted small", text: strategyDesc(it.strategy, it.desc) }),
      h("div", { class: "chips" },
        ...Object.entries(r.params).map(([k, v]) => h("span", { class: "chip-param", text: `${k}=${v}` })),
        ...extra.map((e) => h("span", { class: "chip-param alt", text: e }))),
      h("div", { class: "lb-hero" },
        heroNum(t("leaderboard.card.oos_return"), spct(o.total_return, 0), sgn(o.total_return), [t("leaderboard.card.bh_oos", { v: spct(bh, 0) }), `IS ${spct(i.total_return, 0)}`]),
        heroNum(t("leaderboard.card.max_dd"), pct(o.max_drawdown, 0), "neg", [`IS ${pct(i.max_drawdown, 0)}`]),
        heroNum("Sharpe", num(o.sharpe, 2), sgn(o.sharpe), [`IS ${num(i.sharpe, 2)}`], tip)),
      h("details", { class: "lb-more" },
        h("summary", {}, h("span", { text: t("leaderboard.card.more") }), h("span", { class: "lb-more-peek muted small", text: t("leaderboard.card.peek", { calmar: num(o.calmar, 2), trades: int(o.trades), green: pct(o.green_months, 0) }) })),
        h("div", { class: "minis" },
          mini("Calmar", num(o.calmar, 2), sgn(o.calmar), num(i.calmar, 2), undefined, tip),
          mini(t("leaderboard.card.trades"), int(o.trades), "", int(i.trades)),
          mini(t("leaderboard.card.green_months"), pct(o.green_months, 0), "", pct(i.green_months, 0)),
          mini(t("leaderboard.card.avg_month"), spct(o.avg_month, 1), sgn(o.avg_month), spct(i.avg_month, 1)))),
      tooShort ? h("p", { class: "muted small lb-note", text: t("leaderboard.card.too_short_note", { days: MIN_OOS_DAYS }) }) : null,
      ctx.market === "perp" && !it.funding_included && !it.derivative ? h("p", { class: "muted small lb-note", text: t("leaderboard.card.no_funding") }) : null,
      ctx.market === "spot" && it.derivative ? h("p", { class: "muted small lb-note", text: t("leaderboard.card.spot_deriv") }) : null),
    h("div", { class: "lb-side" },
      sp ?? h("div", { class: "muted small", text: t("leaderboard.card.no_chart") }),
      h("div", { class: "lb-spark-cap muted small" }, h("i", { class: "lg-key", "aria-hidden": "true" }), t("leaderboard.card.spark_cap"), h("i", { class: "lg-key dash", "aria-hidden": "true" }), "Buy&Hold"),
      h("div", { class: "btn-group lb-btns", role: "group", "aria-label": t("leaderboard.card.actions") },
        h("button", { class: "btn sm", type: "button", text: t("leaderboard.card.open_lab"), onClick: () => ctx.onOpenLab(it) }),
        h("button", { class: "btn sm", type: "button", text: t("leaderboard.card.copy"), title: t("leaderboard.card.copy_tip"), onClick: () => ctx.onCopy(it) }),
        h("button", { class: "btn sm", type: "button", text: t("leaderboard.card.send"), title: t("leaderboard.card.send_tip"), onClick: () => ctx.onSend(it) }))));
}

/** Текст и вид «компактной» карточки для стратегий без рейтингового значения. */
export function compactInfo(e: Entry | undefined): { cls: string; text: string } {
  if (!e) return { cls: "pending", text: t("leaderboard.compact.none") };
  switch (e.k) {
    case "item": {
      const code = e.item.reason_code;
      return { cls: "skipped", text: e.item.reason == null && !code ? t("leaderboard.reason.few_trades") : lbReasonText(e.item.reason, code) };
    }
    case "error": return { cls: "failed", text: t("leaderboard.compact.error", { error: e.error }) };
    case "skip": return { cls: "skipped", text: t("leaderboard.compact.skip", { reason: lbReasonText(e.skip, e.code) }) };
    case "pending": return { cls: "running", text: t("leaderboard.compact.pending") };
    case "busy": return { cls: "failed", text: t("leaderboard.compact.busy") };
    case "none": return { cls: "pending", text: t("leaderboard.compact.none") };
  }
}

export function compactCard(name: string, p: LbPlanItem | undefined, e: Entry | undefined): HTMLElement {
  const { cls, text } = compactInfo(e);
  return h("article", { class: `lb-card compact ${cls}` },
    h("div", { class: "lb-rank" }, h("span", { class: "rank-n", text: "–" })),
    h("div", { class: "lb-main" }, h("div", { class: "lb-title" }, h("h3", { text: name }), p?.derivative ? h("span", { class: "badge info", text: t("leaderboard.card.derivative") }) : null),
      h("p", { class: "muted small", text: `${strategyDesc(name, p?.desc ?? "")} — ${text}` })));
}
