// Общий вид результата бэктеста: герой из четырёх главных цифр, «Все метрики», графики, помесячная карта, сделки (фильтр, сортировка, CSV).
// Используется вкладками «Лаборатория» и «Портфель».
import type { ApiError } from "../api";
import { barChart } from "../charts/bars";
import { monthlyHeatmap } from "../charts/heatmap";
import { lineChart, type LineChart } from "../charts/line";
import { t } from "../i18n";
import { SPOT_APPROX_NOTE } from "../market";
import type { BacktestResult, Metrics, TradeRow } from "../types";
import { showError } from "../ui/banner";
import { card } from "../ui/card";
import { toggle } from "../ui/controls";
import { downloadText } from "../ui/download";
import { h } from "../ui/dom";
import { date, int, num, pct, sgn, spct } from "../ui/fmt";
import { stamp } from "../ui/fmtTime";
import { icon } from "../ui/icons";
import { deltaChip, disclosure, glyph, skeletonChart, skeletonTile } from "../ui/kit";
import { dataTable } from "../ui/table";
import { toast } from "../ui/toast";
import { ddVersus, isShortPeriod, periodDays, vsBenchmark } from "./resultLogic";
import { csvFileName, filterTrades, TRADE_FILTERS, tradeCounts, tradesToCsv, type TradeFilter } from "./tradesLogic";

interface Mini {
  label: string;
  value(m: Metrics, short: boolean): string;
  tone?(m: Metrics): string;
  hint?: string;
  /** Годовая метрика: на коротком периоде подсказка — «аннуализировано по N дн.». */
  annualized?: boolean;
}

/** Вторичные метрики («Все метрики»): компактные плитки. */
const MINIS: Mini[] = [
  { label: t("lab.res.m.avgMonth"), value: (m) => spct(m.avg_month, 1), tone: (m) => sgn(m.avg_month), hint: t("lab.res.m.avgMonth.hint") },
  { label: t("lab.res.m.medianMonth"), value: (m) => spct(m.median_month, 1), tone: (m) => sgn(m.median_month) },
  { label: t("lab.res.m.worstMonth"), value: (m) => spct(m.worst_month, 1), tone: (m) => sgn(m.worst_month) },
  { label: t("lab.res.m.greenMonths"), value: (m) => pct(m.green_months, 0) },
  { label: t("lab.res.m.months15"), value: (m) => pct(m.months_15plus, 0), hint: t("lab.res.m.months15.hint") },
  { label: "Calmar", value: (m, s) => (s ? "~" : "") + num(m.calmar, 2), tone: (m) => sgn(m.calmar), hint: t("lab.res.m.calmar.hint"), annualized: true },
  { label: "CAGR", value: (m, s) => (s ? "~" : "") + spct(m.cagr, 0), tone: (m) => sgn(m.cagr), hint: t("lab.res.m.cagr.hint"), annualized: true },
  { label: "Sortino", value: (m) => num(m.sortino, 2), tone: (m) => sgn(m.sortino) },
  { label: "Profit factor", value: (m) => (m.profit_factor == null ? "∞" : num(m.profit_factor, 2)), hint: t("lab.res.m.pf.hint") },
  { label: t("lab.res.m.avgTrade"), value: (m) => spct(m.avg_trade, 2), tone: (m) => sgn(m.avg_trade) },
  { label: t("lab.res.m.exposure"), value: (m) => pct(m.exposure, 0), hint: t("lab.res.m.exposure.hint") },
  { label: t("lab.res.m.liquidations"), value: (m) => int(m.liquidations), tone: (m) => ((m.liquidations ?? 0) > 0 ? "neg" : "") },
  { label: t("lab.res.m.bhReturn"), value: (m) => spct(m.buy_hold_return, 0), tone: (m) => sgn(m.buy_hold_return), hint: t("lab.res.m.bhReturn.hint") },
  { label: t("lab.res.m.bhDd"), value: (m) => pct(m.bh_max_drawdown, 0), tone: () => "neg" },
];

interface ResultViewOpts {
  /** Части имени файла CSV (символ, стратегия, таймфрейм…). */
  fileHint?: () => (string | number | null | undefined)[];
}

interface ResultView {
  el: HTMLElement;
  /** text — необязательная подпись поверх результата (например, «ожидаю сервер, попытка 2/3»). */
  setBusy(b: boolean, text?: string): void;
  setError(e: ApiError | null, retry?: () => void): void;
  setData(r: BacktestResult): void;
  /** Слот между графиком капитала и ценой (используется портфелем). */
  slot: HTMLElement;
  destroy(): void;
}

function heroTile(o: { label: string; value: string; tone?: string; vs?: { tone: string; text: string }; chip?: string; sub?: string; hint?: string }): HTMLElement {
  const tone = o.tone ?? "";
  return h("div", { class: "metric big rv-tile", title: o.hint ?? "" },
    h("div", { class: "metric-top" }, h("div", { class: "metric-label", text: o.label })),
    h("div", { class: `metric-value ${tone}` }, tone ? h("span", { class: "g", "aria-hidden": "true", text: glyph(tone) }) : null, o.value),
    o.vs?.text ? h("div", { class: "rv-delta" }, deltaChip(o.vs.tone, o.vs.text)) : o.chip ? h("div", { class: "rv-delta" }, deltaChip("", o.chip)) : null,
    o.sub ? h("div", { class: "metric-sub", text: o.sub }) : null);
}

export function createResultView(opts: ResultViewOpts = {}): ResultView {
  const banner = h("div", { class: "banner-slot" });
  const notices = h("div", { class: "rv-notices" });
  const hero = h("div", { class: "rv-hero" });
  const minis = h("div", { class: "minis rv-minis" });
  const allBox = h("div", { class: "rv-all-slot" });
  const overlayText = h("span", { text: t("lab.busy") });
  const overlay = h("div", { class: "overlay", hidden: true, "aria-hidden": "true" }, h("div", { class: "busy-pill" }, h("div", { class: "spinner" }), overlayText));
  const skeleton = h("div", { class: "result-skeleton", "aria-hidden": "true" },
    h("div", { class: "rv-hero" }, ...Array.from({ length: 4 }, skeletonTile)), skeletonChart(300), skeletonChart(220));

  const eqHost = h("div", { class: "chart" });
  const prHost = h("div", { class: "chart" });
  const ddHost = h("div", { class: "chart" });
  const monthHost = h("div");
  const yearHost = h("div", { class: "chart" });
  const slot = h("div");
  const note = h("p", { class: "muted small" });
  const rangeLabel = h("span", { class: "muted small rv-range" });

  let current: BacktestResult | null = null;
  const logToggle = toggle({ label: t("lab.res.logScale"), checked: false, onChange: (v) => { eq.setLog(v); } });

  const eq: LineChart = lineChart(eqHost, {
    height: 300, log: false, yFmt: (v) => "×" + num(v, v < 10 ? 2 : 1),
    series: [
      { label: t("lab.res.series.strategy"), color: "--c1", width: 2 },
      { label: "Buy & Hold", color: "--ref", width: 1.5, dash: [5, 4] },
    ],
  });
  const pr: LineChart = lineChart(prHost, {
    height: 260, yFmt: (v) => num(v, v >= 1000 ? 0 : 2),
    series: [{ label: t("lab.res.series.price"), color: "--ref", width: 1.5, fill: 0.1 }],
  });
  const dd: LineChart = lineChart(ddHost, {
    height: 150, yFmt: (v) => pct(v, 0), yMax: 0,
    series: [{ label: t("lab.res.series.drawdown"), color: "--neg", width: 1.5, fill: 0.32, fillMode: "up" }],
  });
  const yearBars = barChart(yearHost, () => ({
    categories: current?.yearly.map((y) => String(y.y)) ?? [],
    series: [{ name: t("lab.res.series.year"), color: "--c1", values: current?.yearly.map((y) => y.v) ?? [] }],
    fmt: (v) => pct(v, 0), bySign: true, height: 200,
  }));

  // ---------- сделки
  let filter: TradeFilter = "all";
  let allTrades: TradeRow[] = [];
  let shownTrades: TradeRow[] = [];
  const trades = dataTable<TradeRow>({
    caption: t("lab.res.trades"),
    sort: { key: "entry", dir: -1 },
    limit: 50,
    empty: t("lab.res.noTrades"),
    rowCls: (r) => (r.ret == null || r.ret === 0 ? "" : r.ret > 0 ? "tr-win" : "tr-loss"),
    columns: [
      { key: "entry", label: t("lab.res.col.entry"), align: "left", value: (r) => r.entry, render: (r) => datetimeShort(r.entry) },
      { key: "exit", label: t("lab.res.col.exit"), align: "left", value: (r) => r.exit, render: (r) => datetimeShort(r.exit) },
      { key: "side", label: t("lab.res.col.side"), align: "left", value: (r) => r.side, render: (r) => (r.side === "long" ? t("lab.res.side.long") : t("lab.res.side.short")), title: t("lab.res.col.side.hint") },
      { key: "ret", label: t("lab.res.col.result"), value: (r) => r.ret, render: (r) => `${glyph(sgn(r.ret))} ${spct(r.ret, 2)}`.trim(), cls: (r) => `${sgn(r.ret)} strong` },
      { key: "bars", label: t("lab.res.col.bars"), value: (r) => r.bars, render: (r) => int(r.bars) },
      { key: "ep", label: t("lab.res.col.entryPrice"), value: (r) => r.entry_price, render: (r) => num(r.entry_price, 2) },
      { key: "xp", label: t("lab.res.col.exitPrice"), value: (r) => r.exit_price, render: (r) => num(r.exit_price, 2) },
    ],
  });
  const tradesNote = h("p", { class: "muted small rv-trades-note" });
  const filterBtns = new Map<TradeFilter, HTMLButtonElement>();
  const filterBar = h("div", { class: "rv-filters", role: "group", "aria-label": t("lab.res.filterLabel") });
  for (const f of TRADE_FILTERS) {
    const b = h("button", { type: "button", class: "chip-btn", "aria-pressed": String(f.key === filter), onClick: () => { filter = f.key; applyFilter(); } });
    filterBtns.set(f.key, b);
    filterBar.append(b);
  }
  const csvBtn = h("button", { class: "btn sm", type: "button", onClick: () => exportCsv() }, icon("download", 14), h("span", { text: t("lab.res.csv") }));

  /** «дд.мм.гг чч:мм» (ru) / «ГГГГ-ММ-ДД чч:мм» (en); время сервера — UTC. */
  const datetimeShort = (s: string): string => {
    const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(s);
    return m ? stamp(Date.UTC(+m[1]!, +m[2]! - 1, +m[3]!, +m[4]!, +m[5]!) / 1000) : s;
  };

  function paintFilterLabels() {
    const c = tradeCounts(allTrades);
    for (const f of TRADE_FILTERS) {
      const b = filterBtns.get(f.key)!;
      b.replaceChildren(f.label, h("span", { class: "cnt", text: String(c[f.key]) }));
      b.setAttribute("aria-pressed", String(f.key === filter));
      b.disabled = f.key !== "all" && c[f.key] === 0;
    }
  }
  function applyFilter() {
    shownTrades = filterTrades(allTrades, filter);
    trades.setEmpty(allTrades.length ? t("lab.res.noTradesFilter") : t("lab.res.noTrades"));
    trades.setRows(shownTrades);
    paintFilterLabels();
    csvBtn.disabled = !shownTrades.length;
    csvBtn.title = shownTrades.length ? t("lab.res.csvTitle", { n: shownTrades.length }) : t("lab.res.csvNone");
  }
  function exportCsv() {
    if (!shownTrades.length) return;
    const name = csvFileName(["trades", ...(opts.fileHint?.() ?? []), filter === "all" ? "" : filter]);
    downloadText(name, tradesToCsv(shownTrades));
    toast(t("lab.res.downloaded", { name }), "ok");
  }

  const legend = h("div", { class: "marker-legend small muted" },
    h("span", null, h("i", { class: "mk up" }), ` ${t("lab.res.legend.long")}`),
    h("span", null, h("i", { class: "mk down" }), ` ${t("lab.res.legend.short")}`),
    h("span", null, h("i", { class: "mk ring" }), ` ${t("lab.res.legend.exit")}`));

  const tradesCard = h("section", { class: "card rv-trades" },
    h("header", { class: "card-head" }, h("h3", { text: t("lab.res.trades") }), h("div", { class: "rv-trades-tools" }, csvBtn)),
    filterBar, trades.el, tradesNote);

  // Живая область — только эта короткая строка (скринридер зачитывает «Считаю…» и одну фразу с итогом), а не весь результат с графиками и таблицей
  const status = h("p", { class: "sr-only rv-status", role: "status", "aria-live": "polite" });
  const el = h("div", { class: "result is-initial" },
    status, skeleton, banner, notices, hero, allBox,
    card(t("lab.res.card.equity"), [eqHost], [rangeLabel, logToggle.el]),
    slot,
    card(t("lab.res.card.price"), [prHost, legend, note]),
    card(t("lab.res.card.drawdown"), [ddHost]),
    h("div", { class: "grid-2" },
      card(t("lab.res.card.monthly"), [monthHost]),
      card(t("lab.res.card.yearly"), [yearHost])),
    tradesCard,
    overlay);

  const notice = (text: string, tone: "info" | "warn", strong?: string) =>
    h("div", { class: `notice${tone === "warn" ? " warn" : ""}` }, strong ? h("strong", { text: strong }) : null, h("span", { text }));

  return {
    el, slot,
    setBusy(b, text) {
      overlayText.textContent = text ?? t("lab.busy");
      status.textContent = b ? overlayText.textContent : "";
      el.setAttribute("aria-busy", String(b));
      overlay.hidden = !b;
      el.classList.toggle("busy", b);
    },
    setError(e, retry) {
      if (e) showError(banner, e, retry ?? (() => {}));
      else banner.replaceChildren();
      el.classList.toggle("has-error", !!e);
    },
    setData(r) {
      current = r;
      banner.replaceChildren();
      el.classList.remove("has-error");
      const m = r.metrics;
      const days = periodDays(r);
      const short = isShortPeriod(r);

      const nn: HTMLElement[] = [];
      const daysTxt = t("lab.days.n", { n: Math.round(days) });
      if (r.market === "spot") {
        nn.push(notice(t("lab.res.spotBody", { lev: r.effective?.leverage ?? 1, fee: num((r.effective?.fee ?? 0.001) * 100, 3), note: SPOT_APPROX_NOTE }), "info", `${t("lab.res.spotTitle")} `));
      }
      if (short) nn.push(notice(t("lab.res.shortBody", { days: daysTxt }), "warn", `${t("lab.res.shortTitle")} `));
      notices.replaceChildren(...nn);

      const vs = vsBenchmark(m.total_return, m.buy_hold_return);
      const dv = ddVersus(m.max_drawdown, m.bh_max_drawdown);
      hero.replaceChildren(
        heroTile({ label: t("lab.res.hero.return"), value: spct(m.total_return, 0), tone: sgn(m.total_return), vs, sub: `B&H ${spct(m.buy_hold_return, 0)} · CAGR ${short ? "~" : ""}${spct(m.cagr, 0)}`, hint: t("lab.res.hero.return.hint") }),
        heroTile({ label: t("lab.res.hero.dd"), value: pct(m.max_drawdown, 1), tone: "neg", vs: dv, sub: `B&H ${pct(m.bh_max_drawdown, 0)}`, hint: t("lab.res.hero.dd.hint") }),
        heroTile({ label: "Sharpe", value: num(m.sharpe, 2), tone: sgn(m.sharpe), sub: `Sortino ${num(m.sortino, 2)} · Calmar ${short ? "~" : ""}${num(m.calmar, 2)}`, hint: short ? t("lab.res.annualized", { days: daysTxt }) : t("lab.res.hero.sharpe.hint") }),
        heroTile({ label: t("lab.res.hero.trades"), value: int(m.trades), chip: t("lab.res.hero.winRate", { pct: pct(m.win_rate, 0) }), sub: t("lab.res.hero.avgTrade", { avg: spct(m.avg_trade, 2), pf: m.profit_factor == null ? "∞" : num(m.profit_factor, 2) }), hint: t("lab.res.hero.trades.hint") }));

      minis.replaceChildren(...MINIS.map((c) => {
        const tone = c.tone?.(m) ?? "";
        return h("div", { class: "mini", title: short && c.annualized ? t("lab.res.annualized", { days: daysTxt }) : (c.hint ?? "") },
          h("div", { class: "metric-label", text: c.label }), h("div", { class: `mini-val ${tone}`, text: c.value(m, short) }));
      }));
      if (!allBox.firstChild) allBox.append(disclosure(t("lab.res.allMetrics"), [minis], { key: "res.all", defaultOpen: window.innerWidth >= 1100 }));
      allBox.firstElementChild?.classList.add("rv-all");

      el.classList.remove("is-initial");
      status.textContent = t("lab.res.status", { ret: spct(m.total_return, 0), dd: pct(m.max_drawdown, 1), n: int(m.trades) });
      rangeLabel.textContent = t("lab.res.range", { start: date(r.data_range.start), end: date(r.data_range.end), n: r.data_range.bars, bars: int(r.data_range.bars) });
      eq.setData(r.t, [r.equity, r.bh]);
      pr.setData(r.t, [r.price]);
      dd.setData(r.t, [r.drawdown]);
      pr.setMarkers([
        ...r.markers.entries.filter((x) => x.p != null).map((x) => ({ t: x.t, p: x.p as number, kind: "entry" as const, side: x.side })),
        ...r.markers.exits.filter((x) => x.p != null).map((x) => ({ t: x.t, p: x.p as number, kind: "exit" as const, side: x.side })),
      ]);
      note.textContent = r.markers.truncated ? t("lab.res.markersTruncated") : "";
      monthHost.replaceChildren(monthlyHeatmap(r.monthly, r.yearly));
      yearBars.redraw();
      allTrades = r.trades;
      if (filter !== "all" && !tradeCounts(allTrades)[filter]) filter = "all";
      applyFilter();
      tradesNote.textContent = r.trades_total > r.trades.length
        ? t("lab.res.tradesShown", { shown: int(r.trades.length), total: int(r.trades_total) }) : t("lab.res.tradesTotal", { total: int(r.trades_total) });
    },
    destroy() { eq.destroy(); pr.destroy(); dd.destroy(); yearBars.destroy(); },
  };
}
