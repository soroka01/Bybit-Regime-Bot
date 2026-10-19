// Общий вид результата бэктеста: герой из четырёх главных цифр, «Все метрики», графики, помесячная карта, сделки (фильтр, сортировка, CSV).
// Используется вкладками «Лаборатория» и «Портфель».
import type { ApiError } from "../api";
import { barChart } from "../charts/bars";
import { monthlyHeatmap } from "../charts/heatmap";
import { lineChart, type LineChart } from "../charts/line";
import { SPOT_APPROX_NOTE } from "../market";
import type { BacktestResult, Metrics, TradeRow } from "../types";
import { showError } from "../ui/banner";
import { card } from "../ui/card";
import { toggle } from "../ui/controls";
import { downloadText } from "../ui/download";
import { h } from "../ui/dom";
import { date, int, num, pct, sgn, spct } from "../ui/fmt";
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
}

/** Вторичные метрики («Все метрики»): компактные плитки. */
const MINIS: Mini[] = [
  { label: "Ср. месяц", value: (m) => spct(m.avg_month, 1), tone: (m) => sgn(m.avg_month), hint: "Среднее по полным календарным месяцам" },
  { label: "Медиана месяца", value: (m) => spct(m.median_month, 1), tone: (m) => sgn(m.median_month) },
  { label: "Худший месяц", value: (m) => spct(m.worst_month, 1), tone: (m) => sgn(m.worst_month) },
  { label: "Зелёных месяцев", value: (m) => pct(m.green_months, 0) },
  { label: "Месяцы ≥ 15%", value: (m) => pct(m.months_15plus, 0), hint: "Доля месяцев с доходностью не менее 15%" },
  { label: "Calmar", value: (m, s) => (s ? "~" : "") + num(m.calmar, 2), tone: (m) => sgn(m.calmar), hint: "CAGR / |макс. просадка|" },
  { label: "CAGR", value: (m, s) => (s ? "~" : "") + spct(m.cagr, 0), tone: (m) => sgn(m.cagr), hint: "Среднегодовая доходность" },
  { label: "Sortino", value: (m) => num(m.sortino, 2), tone: (m) => sgn(m.sortino) },
  { label: "Profit factor", value: (m) => (m.profit_factor == null ? "∞" : num(m.profit_factor, 2)), hint: "Сумма прибылей / сумма убытков" },
  { label: "Ср. сделка", value: (m) => spct(m.avg_trade, 2), tone: (m) => sgn(m.avg_trade) },
  { label: "В рынке", value: (m) => pct(m.exposure, 0), hint: "Доля времени с открытой позицией" },
  { label: "Ликвидации", value: (m) => int(m.liquidations), tone: (m) => ((m.liquidations ?? 0) > 0 ? "neg" : "") },
  { label: "B&H доходность", value: (m) => spct(m.buy_hold_return, 0), tone: (m) => sgn(m.buy_hold_return), hint: "Купить и держать за тот же период" },
  { label: "B&H просадка", value: (m) => pct(m.bh_max_drawdown, 0), tone: () => "neg" },
];

export interface ResultViewOpts {
  /** Части имени файла CSV (символ, стратегия, таймфрейм…). */
  fileHint?: () => (string | number | null | undefined)[];
}

export interface ResultView {
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
  const overlayText = h("span", { text: "Считаю…" });
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
  const logToggle = toggle({ label: "лог. шкала", checked: false, onChange: (v) => { eq.setLog(v); } });

  const eq: LineChart = lineChart(eqHost, {
    height: 300, log: false, yFmt: (v) => "×" + num(v, v < 10 ? 2 : 1),
    series: [
      { label: "Стратегия", color: "--c1", width: 2 },
      { label: "Buy & Hold", color: "--ref", width: 1.5, dash: [5, 4] },
    ],
  });
  const pr: LineChart = lineChart(prHost, {
    height: 260, yFmt: (v) => num(v, v >= 1000 ? 0 : 2),
    series: [{ label: "Цена", color: "--ref", width: 1.5, fill: 0.1 }],
  });
  const dd: LineChart = lineChart(ddHost, {
    height: 150, yFmt: (v) => pct(v, 0), yMax: 0,
    series: [{ label: "Просадка", color: "--neg", width: 1.5, fill: 0.32, fillMode: "up" }],
  });
  const yearBars = barChart(yearHost, () => ({
    categories: current?.yearly.map((y) => String(y.y)) ?? [],
    series: [{ name: "Год", color: "--c1", values: current?.yearly.map((y) => y.v) ?? [] }],
    fmt: (v) => pct(v, 0), bySign: true, height: 200,
  }));

  // ---------- сделки
  let filter: TradeFilter = "all";
  let allTrades: TradeRow[] = [];
  let shownTrades: TradeRow[] = [];
  const trades = dataTable<TradeRow>({
    caption: "Сделки",
    sort: { key: "entry", dir: -1 },
    limit: 50,
    empty: "За выбранный период сделок нет",
    rowCls: (r) => (r.ret == null || r.ret === 0 ? "" : r.ret > 0 ? "tr-win" : "tr-loss"),
    columns: [
      { key: "entry", label: "Вход", align: "left", value: (r) => r.entry, render: (r) => datetimeShort(r.entry) },
      { key: "exit", label: "Выход", align: "left", value: (r) => r.exit, render: (r) => datetimeShort(r.exit) },
      { key: "side", label: "Сторона", align: "left", value: (r) => r.side, render: (r) => (r.side === "long" ? "△ Лонг" : "▽ Шорт"), title: "Сторона сделки. Цвет — только у результата (прибыль/убыток)" },
      { key: "ret", label: "Результат", value: (r) => r.ret, render: (r) => `${glyph(sgn(r.ret))} ${spct(r.ret, 2)}`.trim(), cls: (r) => `${sgn(r.ret)} strong` },
      { key: "bars", label: "Свечей", value: (r) => r.bars, render: (r) => int(r.bars) },
      { key: "ep", label: "Цена входа", value: (r) => r.entry_price, render: (r) => num(r.entry_price, 2) },
      { key: "xp", label: "Цена выхода", value: (r) => r.exit_price, render: (r) => num(r.exit_price, 2) },
    ],
  });
  const tradesNote = h("p", { class: "muted small rv-trades-note" });
  const filterBtns = new Map<TradeFilter, HTMLButtonElement>();
  const filterBar = h("div", { class: "rv-filters", role: "group", "aria-label": "Фильтр сделок" });
  for (const f of TRADE_FILTERS) {
    const b = h("button", { type: "button", class: "chip-btn", "aria-pressed": String(f.key === filter), onClick: () => { filter = f.key; applyFilter(); } });
    filterBtns.set(f.key, b);
    filterBar.append(b);
  }
  const csvBtn = h("button", { class: "btn sm", type: "button", onClick: () => exportCsv() }, icon("download", 14), h("span", { text: "Скачать CSV" }));

  const datetimeShort = (s: string): string => {
    const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(s);
    return m ? `${m[3]}.${m[2]}.${m[1]!.slice(2)} ${m[4]}:${m[5]}` : s;
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
    trades.setEmpty(allTrades.length ? "Нет сделок под выбранный фильтр" : "За выбранный период сделок нет");
    trades.setRows(shownTrades);
    paintFilterLabels();
    csvBtn.disabled = !shownTrades.length;
    csvBtn.title = shownTrades.length ? `Скачать ${shownTrades.length} сделок в CSV (UTC, доходность — доля)` : "Нет сделок для выгрузки";
  }
  function exportCsv() {
    if (!shownTrades.length) return;
    const name = csvFileName(["trades", ...(opts.fileHint?.() ?? []), filter === "all" ? "" : filter]);
    downloadText(name, tradesToCsv(shownTrades));
    toast(`Скачано: ${name}`, "ok");
  }

  const legend = h("div", { class: "marker-legend small muted" },
    h("span", null, h("i", { class: "mk up" }), " вход в лонг"),
    h("span", null, h("i", { class: "mk down" }), " вход в шорт"),
    h("span", null, h("i", { class: "mk ring" }), " выход"));

  const tradesCard = h("section", { class: "card rv-trades" },
    h("header", { class: "card-head" }, h("h3", { text: "Сделки" }), h("div", { class: "rv-trades-tools" }, csvBtn)),
    filterBar, trades.el, tradesNote);

  // Живая область — только эта короткая строка (скринридер зачитывает «Считаю…» и одну фразу с итогом), а не весь результат с графиками и таблицей
  const status = h("p", { class: "sr-only rv-status", role: "status", "aria-live": "polite" });
  const el = h("div", { class: "result is-initial" },
    status, skeleton, banner, notices, hero, allBox,
    card("Капитал против Buy & Hold", [eqHost], [rangeLabel, logToggle.el]),
    slot,
    card("Цена и сделки", [prHost, legend, note]),
    card("Просадка", [ddHost]),
    h("div", { class: "grid-2" },
      card("Доходность по месяцам", [monthHost]),
      card("Доходность по годам", [yearHost])),
    tradesCard,
    overlay);

  const notice = (text: string, tone: "info" | "warn", strong?: string) =>
    h("div", { class: `notice${tone === "warn" ? " warn" : ""}` }, strong ? h("strong", { text: strong }) : null, h("span", { text }));

  return {
    el, slot,
    setBusy(b, text) {
      overlayText.textContent = text ?? "Считаю…";
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
      if (r.market === "spot") {
        nn.push(notice(`плечо ${r.effective?.leverage ?? 1}×, только лонг, фандинг не начисляется, комиссия ${num((r.effective?.fee ?? 0.001) * 100, 3)} %. Данные: ${SPOT_APPROX_NOTE}.`, "info", "Спот: "));
      }
      if (short) nn.push(notice(`всего ${Math.round(days)} дн. Годовые метрики (Calmar, CAGR) аннуализированы и сильно завышены — не сравнивайте их; для оценки берите период от года.`, "warn", "Короткий период: "));
      notices.replaceChildren(...nn);

      const vs = vsBenchmark(m.total_return, m.buy_hold_return);
      const dv = ddVersus(m.max_drawdown, m.bh_max_drawdown);
      hero.replaceChildren(
        heroTile({ label: "Доходность", value: spct(m.total_return, 0), tone: sgn(m.total_return), vs, sub: `B&H ${spct(m.buy_hold_return, 0)} · CAGR ${short ? "~" : ""}${spct(m.cagr, 0)}`, hint: "Итоговая доходность за период против Buy & Hold за тот же период" }),
        heroTile({ label: "Макс. просадка", value: pct(m.max_drawdown, 1), tone: "neg", vs: dv, sub: `B&H ${pct(m.bh_max_drawdown, 0)}`, hint: "Наибольшее падение капитала от пика" }),
        heroTile({ label: "Sharpe", value: num(m.sharpe, 2), tone: sgn(m.sharpe), sub: `Sortino ${num(m.sortino, 2)} · Calmar ${short ? "~" : ""}${num(m.calmar, 2)}`, hint: short ? `аннуализировано по ${Math.round(days)} дн.` : "Доходность на единицу волатильности (годовая)" }),
        heroTile({ label: "Сделок", value: int(m.trades), chip: `винрейт ${pct(m.win_rate, 0)}`, sub: `ср. сделка ${spct(m.avg_trade, 2)} · PF ${m.profit_factor == null ? "∞" : num(m.profit_factor, 2)}`, hint: "Число закрытых сделок и доля прибыльных" }));

      minis.replaceChildren(...MINIS.map((c) => {
        const tone = c.tone?.(m) ?? "";
        return h("div", { class: "mini", title: short && /Calmar|CAGR/.test(c.label) ? `аннуализировано по ${Math.round(days)} дн.` : (c.hint ?? "") },
          h("div", { class: "metric-label", text: c.label }), h("div", { class: `mini-val ${tone}`, text: c.value(m, short) }));
      }));
      if (!allBox.firstChild) allBox.append(disclosure("Все метрики", [minis], { key: "res.all", defaultOpen: window.innerWidth >= 1100 }));
      allBox.firstElementChild?.classList.add("rv-all");

      el.classList.remove("is-initial");
      status.textContent = `Результат обновлён: доходность ${spct(m.total_return, 0)}, макс. просадка ${pct(m.max_drawdown, 1)}, сделок ${int(m.trades)}`;
      rangeLabel.textContent = `${date(r.data_range.start)} — ${date(r.data_range.end)} · ${int(r.data_range.bars)} свечей`;
      eq.setData(r.t, [r.equity, r.bh]);
      pr.setData(r.t, [r.price]);
      dd.setData(r.t, [r.drawdown]);
      pr.setMarkers([
        ...r.markers.entries.filter((x) => x.p != null).map((x) => ({ t: x.t, p: x.p as number, kind: "entry" as const, side: x.side })),
        ...r.markers.exits.filter((x) => x.p != null).map((x) => ({ t: x.t, p: x.p as number, kind: "exit" as const, side: x.side })),
      ]);
      note.textContent = r.markers.truncated ? "Показаны последние ~1200 сделок; полный список — в таблице ниже." : "";
      monthHost.replaceChildren(monthlyHeatmap(r.monthly, r.yearly));
      yearBars.redraw();
      allTrades = r.trades;
      if (filter !== "all" && !tradeCounts(allTrades)[filter]) filter = "all";
      applyFilter();
      tradesNote.textContent = r.trades_total > r.trades.length
        ? `Показано ${int(r.trades.length)} из ${int(r.trades_total)} (последние сделки; более ранние не передаются). CSV содержит только показанные.` : `Всего сделок: ${int(r.trades_total)}.`;
    },
    destroy() { eq.destroy(); pr.destroy(); dd.destroy(); yearBars.destroy(); },
  };
}
