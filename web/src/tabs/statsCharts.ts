// Вкладка «Статистика»: карточки с графиками — капитал (полоса мин…макс, маркеры сделок), просадка, P/L по периодам, календарь, гистограмма, дневная доходность.
// Графики создаются один раз и обновляются через update(res): так автообновление не сбрасывает прокрутку и курсор.
import { t } from "../i18n";
import { barChart } from "../charts/bars";
import { calendarChart } from "../charts/calendar";
import { histogramChart } from "../charts/histogram";
import { lineChart, type LineChart } from "../charts/line";
import type { Num, StatsResponse } from "../types";
import { segmented } from "../ui/controls";
import { h } from "../ui/dom";
import { num, pctU, snum, spctU, susd, usd } from "../ui/fmt";
import { emptyState } from "../ui/kit";
import { BUCKET_TITLE, bucketLabel, bucketTitle, bucketValues, type BucketKind } from "./statsBuckets";
import { calendarIsCompact } from "../charts/calendarModel";
import { ddmm, mdLocal, stamp, ymdLocal } from "../ui/fmtTime";
import { equityMapper, tradeMarkers } from "./statsMarkers";
import { histModel, type HistModel } from "../charts/histModel";
import { infoMark } from "./statsView";

export type EqUnit = "usdt" | "pct";
export const isEqUnit = (v: unknown): v is EqUnit => v === "usdt" || v === "pct";

const sec = (title: string, body: HTMLElement[], head: (HTMLElement | null)[] = [], cls = ""): HTMLElement =>
  h("section", { class: `card st-chart ${cls}`.trim(), "aria-label": title }, h("header", { class: "card-head" }, h("h3", { text: title }), ...head), ...body);
const sr = (): HTMLElement => h("p", { class: "sr-only" });

interface StatsCharts {
  equity: HTMLElement; drawdown: HTMLElement; pnl: HTMLElement; hist: HTMLElement; daily: HTMLElement; calendar: HTMLElement;
  update(res: StatsResponse, selected: number | null): void;
  setSelected(res: StatsResponse, selected: number | null): void;
  /** К графику капитала (прокрутка). */
  scrollToEquity(): void;
  destroy(): void;
}

export function createStatsCharts(o: { getUnit(): EqUnit; setUnit(u: EqUnit): void; onClearSelection(): void }): StatsCharts {
  let unit = o.getUnit();
  let last: StatsResponse | null = null;
  let selected: number | null = null;

  // ---------------- капитал
  const eqHost = h("div", { class: "chart" });
  const eqBody = h("div", { class: "st-eq-body" });
  const eqSummary = sr();
  const eqLegend = h("div", { class: "marker-legend st-eq-legend small muted" });
  const selNote = h("span", { class: "st-sel", hidden: true });
  const seg = segmented({ label: t("stats.chart.units"), options: [{ value: "usdt", label: "USDT" }, { value: "pct", label: t("stats.chart.pct_start") }], value: unit, onChange: (v) => { if (v === "usdt" || v === "pct") { unit = v; o.setUnit(v); if (last) paintEquity(last); } } });
  seg.el.classList.add("st-seg");
  let eqData: { lo: Num[]; hi: Num[] } = { lo: [], hi: [] };
  const fmtVal = (v: number) => (unit === "usdt" ? usd(v) : spctU(v, 2));
  const eq: LineChart = lineChart(eqHost, {
    height: 300, sync: "st",
    yFmt: (v) => (unit === "usdt" ? num(v, Math.abs(v) >= 1000 ? 0 : 2) : num(v, Math.abs(v) < 10 ? 1 : 0) + "%"),
    series: [
      { label: t("stats.chart.equity"), color: "--c1", width: 2, fill: 0.14, fmt: fmtVal },
      { label: t("stats.chart.min"), color: "--c1", width: 1, quiet: true }, { label: t("stats.chart.max"), color: "--c1", width: 1, quiet: true },
    ],
    bands: [{ hi: 2, lo: 1, color: "--c1", alpha: 0.14 }],
    tipNote: (i) => (eqData.lo[i] != null && eqData.hi[i] != null && eqData.lo[i] !== eqData.hi[i] ? t("stats.chart.interval_range", { lo: fmtVal(eqData.lo[i]!), hi: fmtVal(eqData.hi[i]!) }) : undefined),
  });
  const clearSel = h("button", { class: "btn ghost sm st-sel-x", type: "button", "aria-label": t("stats.chart.clear_sel"), onClick: () => o.onClearSelection(), text: "✕" });
  selNote.append(h("span", { class: "st-sel-t" }), clearSel);
  const equityCard = sec(t("stats.chart.eq_title"), [h("div", { class: "st-eq-wrap", role: "img", "aria-label": t("stats.chart.eq_aria") }, eqBody), eqLegend, eqSummary], [selNote, seg.el], "st-equity");
  const eqWrap = equityCard.querySelector<HTMLElement>(".st-eq-wrap")!;

  function paintEquity(res: StatsResponse) {
    const s = res.series?.equity, e = res.equity;
    if (!s || s.t.length < 2 || !e) {
      eq.setData([], [[], [], []]); eq.setMarkers([]);
      eqBody.replaceChildren(emptyState(t("stats.chart.eq_few"), t("stats.chart.eq_few_hint"), null, { icon: "chart" }));
      eqLegend.replaceChildren(); eqSummary.textContent = t("stats.chart.eq_few_sr");
      eqWrap.setAttribute("aria-label", t("stats.chart.eq_few_aria"));
      return;
    }
    if (!eqBody.contains(eqHost)) eqBody.replaceChildren(eqHost);
    const map = equityMapper(unit, e.eq_start ?? null);
    const m = (a: Num[]) => a.map((x) => (x == null ? null : map(x)));
    eqData = { lo: m(s.lo), hi: m(s.hi) };
    eq.setData(s.t, [m(s.v), eqData.lo, eqData.hi]);
    const rows = res.trades?.rows ?? [];
    const w = res.window;
    const mk = tradeMarkers(rows, s.t, s.v, w?.start ?? s.t[0]!, w?.end ?? s.t[s.t.length - 1]!, selected, map);
    eq.setMarkers(mk);
    const auto = rows.length <= 150;
    eqLegend.replaceChildren(
      h("span", { class: "lg-item" }, h("i", { class: "lg-key area", style: "--k:var(--c1);opacity:.35" }), t("stats.chart.lg_range")),
      ...(rows.length ? [h("span", { class: "lg-item" }, h("i", { class: "mk up", "aria-hidden": "true" }), t("stats.chart.lg_long")), h("span", { class: "lg-item" }, h("i", { class: "mk down", "aria-hidden": "true" }), t("stats.chart.lg_short")),
        h("span", { class: "lg-item" }, h("i", { class: "mk ring", "aria-hidden": "true" }), t("stats.chart.lg_exit"))] : []),
      h("span", { text: rows.length ? (auto ? t("stats.chart.markers", { n: mk.length }) : t("stats.chart.markers_hidden")) : t("stats.chart.no_trades") }));
    const first = s.v.find((x) => x != null), lastV = [...s.v].reverse().find((x) => x != null);
    const txt = t("stats.chart.eq_summary", {
      from: usd(first), to: usd(lastV), n: s.t.length,
      min: usd(Math.min(...s.lo.filter((x): x is number => x != null))), max: usd(Math.max(...s.hi.filter((x): x is number => x != null))),
    });
    eqSummary.textContent = txt;
    eqWrap.setAttribute("aria-label", t("stats.chart.eq_aria_full", { summary: txt }));
    paintSelection(res);
  }
  function paintSelection(res: StatsResponse) {
    const row = selected == null ? null : res.trades?.rows.find((r) => r.n === selected);
    selNote.hidden = !row;
    if (row) selNote.querySelector(".st-sel-t")!.textContent = t("stats.chart.selected", { n: row.n, side: row.side === "long" ? t("stats.side.long") : t("stats.side.short"), from: stamp(row.entry_ts), to: stamp(row.exit_ts), net: susd(row.net) });
  }

  // ---------------- просадка
  const ddHost = h("div", { class: "chart" });
  const ddBody = h("div", { class: "st-dd-body" });
  const ddNote = h("span", { class: "muted small" });
  const ddSummary = sr();
  const dd: LineChart = lineChart(ddHost, {
    height: 150, sync: "st", yMax: 0, yFmt: (v) => num(v, Math.abs(v) < 1 ? 2 : Math.abs(v) < 10 ? 1 : 0) + "%",
    series: [{ label: t("stats.chart.dd"), color: "--neg", width: 1.5, fill: 0.32, fillMode: "up", fmt: (v) => pctU(v, 2) }],
  });
  const ddWrap = h("div", { role: "img", "aria-label": t("stats.chart.dd_aria") }, ddBody);
  const ddCard = sec(t("stats.chart.dd_title"), [ddWrap, ddSummary], [ddNote, infoMark(t("stats.chart.dd_info"))], "st-dd");
  function paintDd(res: StatsResponse) {
    const s = res.series?.drawdown, e = res.equity;
    if (!s || s.t.length < 2) {
      dd.setData([], [[]]);
      ddBody.replaceChildren(emptyState(t("stats.chart.dd_few"), undefined, null, { icon: "chart" }));
      ddNote.textContent = "";
      return;
    }
    if (!ddBody.contains(ddHost)) ddBody.replaceChildren(ddHost);
    dd.setData(s.t, [s.dd_pct]);
    ddNote.textContent = e && e.max_drawdown_pct != null && e.max_drawdown_pct < 0 ? t("stats.chart.dd_note", { dd: pctU(e.max_drawdown_pct, 2), a: ddmm(e.max_drawdown_peak_ts), b: ddmm(e.max_drawdown_trough_ts) }) : t("stats.chart.dd_none");
    const txt = t("stats.chart.dd_summary", { v: pctU(e?.max_drawdown_pct, 2) });
    ddSummary.textContent = txt;
    ddWrap.setAttribute("aria-label", t("stats.chart.dd_aria_full", { summary: txt }));
  }

  // ---------------- P/L по периодам
  const pnlHost = h("div", { class: "chart st-bars" });
  const pnlTitle = h("span", { class: "muted small" });
  const pnlSummary = sr();
  const pnlBars = barChart(pnlHost, () => {
    const b = last?.series?.pnl_buckets;
    const kind = (b?.kind ?? "day") as BucketKind;
    const rows = b?.rows ?? [];
    return {
      categories: rows.map((r) => bucketLabel(kind, r.t)), height: 220, bySign: true, fmt: (v) => snum(v, Math.abs(v) < 10 ? 2 : 1),
      series: [{ name: "Net", color: "--c1", values: bucketValues(rows), titles: rows.map((r) => `${bucketTitle(kind, r.t)}\n${r.n ? t("stats.chart.bucket_tip", { n: r.n, w: r.wins }) : t("stats.chart.bucket_none")}`) }],
    };
  });
  const pnlCard = sec(t("stats.chart.pnl_title"), [h("div", { role: "group", "aria-label": t("stats.chart.pnl_aria") }, pnlHost), pnlSummary], [pnlTitle], "st-pnl");

  // ---------------- дневная доходность
  const dayHost = h("div", { class: "chart st-bars" });
  const daySummary = sr();
  const dayBars = barChart(dayHost, () => {
    const d = last?.series?.daily_returns ?? [];
    return { categories: d.map((x) => mdLocal(x.date)), height: 220, bySign: true, fmt: (v) => spctU(v, Math.abs(v) < 10 ? 1 : 0),
      series: [{ name: t("stats.chart.day_series"), color: "--c1", values: d.map((x) => x.ret_pct), titles: d.map((x) => ymdLocal(x.date)) }] };
  });
  const dayCard = sec(t("stats.chart.day_title"), [h("div", { role: "group", "aria-label": t("stats.chart.day_aria") }, dayHost), daySummary], [infoMark(t("stats.chart.day_info"))], "st-daily");

  // ---------------- гистограмма
  let model: HistModel | null = null;
  const histHost = h("div", { class: "chart st-hist" });
  const histSummary = sr();
  const histChart = histogramChart(histHost, () => model, { height: 220 });
  const histLegend = h("div", { class: "marker-legend small muted" },
    h("span", { class: "lg-item" }, h("i", { class: "lg-key area", style: "--k:var(--neg)" }), t("stats.chart.lg_loss")), h("span", { class: "lg-item" }, h("i", { class: "lg-key area", style: "--k:var(--pos)" }), t("stats.chart.lg_profit")),
    h("span", { class: "lg-item" }, h("i", { class: "lg-key area", style: "--k:var(--ref)" }), t("stats.chart.lg_zero")));
  const histCard = sec(t("stats.chart.hist_title"), [h("div", { role: "group", "aria-label": t("stats.chart.hist_aria") }, histHost), histLegend, histSummary], [infoMark(t("stats.chart.hist_info"))], "st-histc");

  // ---------------- календарь
  const calBody = h("div", { class: "st-cal-body" });
  const calNote = h("span", { class: "muted small" });
  const calCard = sec(t("stats.chart.cal_title"), [calBody], [calNote, infoMark(t("stats.chart.cal_info"))], "st-cal");

  function paintAll(res: StatsResponse) {
    const b = res.series?.pnl_buckets;
    pnlTitle.textContent = b ? BUCKET_TITLE[b.kind] : "";
    const pn = (b?.rows ?? []).filter((r) => r.n > 0);
    pnlSummary.textContent = pn.length ? t("stats.chart.pnl_summary", { n: pn.length, net: susd(pn.reduce((a, r) => a + (r.net ?? 0), 0)) }) : t("stats.chart.pnl_none");
    pnlBars.redraw();
    const d = res.series?.daily_returns ?? [];
    dayCard.hidden = d.length < 2;
    daySummary.textContent = d.length ? t("stats.chart.day_summary", { n: d.length }) : "";
    dayBars.redraw();
    model = histModel(res.series?.histogram);
    histChart.redraw();
    histLegend.hidden = !model;
    histSummary.textContent = model ? t("stats.chart.hist_summary", { total: model.total, k: model.bins.filter((x) => x.tone === "neg").reduce((a, x) => a + x.count, 0) }) : t("stats.chart.hist_none");
    const w = res.window;
    if (w && res.series) {
      calBody.replaceChildren(calendarChart(w.start_date, w.end_date, res.series.calendar));
      calNote.textContent = calendarIsCompact(w.start_date, w.end_date) ? t("stats.chart.cal_compact") : "";
    } else calBody.replaceChildren();
  }

  return {
    equity: equityCard, drawdown: ddCard, pnl: pnlCard, hist: histCard, daily: dayCard, calendar: calCard,
    update(res, sel) {
      last = res; selected = sel; unit = o.getUnit(); seg.set(unit);
      paintEquity(res); paintDd(res); paintAll(res);
    },
    setSelected(res, sel) { selected = sel; paintEquity(res); },
    scrollToEquity() { equityCard.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" }); },
    destroy() { eq.destroy(); dd.destroy(); pnlBars.destroy(); dayBars.destroy(); histChart.destroy(); },
  };
}
