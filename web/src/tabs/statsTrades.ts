// Вкладка «Статистика»: таблица закрытых сделок (round trips) — фильтры со счётчиками, сортировка, раскрытие fills, CSV, «Показать ещё».
// Клик по строке выделяет вход/выход сделки на графике капитала (onSelect). Логика фильтров и CSV — statsTradeLogic.ts, суммы — statsMoney.ts.
import { t } from "../i18n";
import { reasonLabel } from "../i18n/storedText";
import type { StatsFill, StatsResponse, StatsTradeRow } from "../types";
import { downloadText } from "../ui/download";
import { h } from "../ui/dom";
import { fillBasisText, filterStTrades, ST_TRADE_FILTERS, stTradeCounts, stTradesToCsv, type StTradeFilter } from "./statsTradeLogic";
import { fmtDuration, isoUtc, stamp } from "../ui/fmtTime";
import { moneyLine, moneySummary, resultTone } from "./statsMoney";
import { icon } from "../ui/icons";
import { agoMs, int, num, spctU, susd, toneOf, usd } from "../ui/fmt";
import { csvFileName } from "./tradesLogic";
import { glyph } from "../ui/kit";
import { dataTable } from "../ui/table";
import { toast } from "../ui/toast";
import { orderBadge } from "../ui/side";
import { moneyStrip, reasonCell, sideBadge } from "./statsView";

interface TradesCard {
  el: HTMLElement;
  update(res: StatsResponse): void;
  setSelected(n: number | null): void;
}

export function createTradesCard(o: { getFilter(): StTradeFilter; setFilter(f: StTradeFilter): void; onSelect(n: number | null): void; onGoto(): void; fileHint(): string[] }): TradesCard {
  let rows: StatsTradeRow[] = [];
  let shown: StatsTradeRow[] = [];
  let selected: number | null = null;
  const open = new Set<number>();

  const note = h("p", { class: "muted small st-trades-note" });
  const sum = h("div", { class: "st-money-slot" });        // «Закрыто N: заработано … потеряно … итого …» по показанным (отфильтрованным) сделкам
  const count = h("span", { class: "muted small" });
  const gotoBtn = h("button", { class: "btn ghost sm", type: "button", hidden: true, onClick: () => o.onGoto() }, icon("chart", 14), h("span", { text: t("stats.trades.goto") }));
  const csvBtn = h("button", { class: "btn sm", type: "button", onClick: () => exportCsv() }, icon("download", 14), h("span", { text: t("stats.trades.csv") }));
  const filterBar = h("div", { class: "rv-filters st-filters", role: "group", "aria-label": t("stats.trades.filter_aria") });
  const btns = new Map<StTradeFilter, HTMLButtonElement>();
  for (const f of ST_TRADE_FILTERS) {
    const b = h("button", { type: "button", class: "chip-btn", "aria-pressed": "false", onClick: () => { o.setFilter(f.key); apply(); } });
    btns.set(f.key, b);
    filterBar.append(b);
  }

  const table = dataTable<StatsTradeRow>({
    caption: t("stats.trades.caption"),
    sort: { key: "exit", dir: -1 },
    limit: 50,
    empty: t("stats.trades.empty"),
    rowKey: (r) => String(r.n),
    rowCls: (r) => `${r.net == null || r.net === 0 ? "" : r.net > 0 ? "tr-win" : "tr-loss"}${r.reconstructed ? " tr-recon" : ""}`,
    isSelected: (r) => r.n === selected,
    onRowClick: (r) => { selected = selected === r.n ? null : r.n; gotoBtn.hidden = selected == null; o.onSelect(selected); table.refresh(); },
    expand: { open: (r) => open.has(r.n), render: (r) => fillsView(r) },
    columns: [
      {
        key: "n", label: t("stats.col.n"), align: "left", value: (r) => r.n, title: t("stats.col.n_tip"),
        node: (r) => {
          const isOpen = open.has(r.n);
          const b = h("button", {
            type: "button", class: "row-exp", "aria-expanded": String(isOpen), "aria-label": t("stats.col.n_aria", { n: r.n }),
            onClick: (e: Event) => { e.stopPropagation(); if (open.has(r.n)) open.delete(r.n); else open.add(r.n); table.refresh(); },
            onKeydown: (e: Event) => { e.stopPropagation(); },
          }, h("i", { "aria-hidden": "true" }));
          return h("span", { class: "st-n" }, b, h("span", { text: String(r.n) }));
        },
      },
      { key: "exit", label: t("stats.col.closed"), align: "left", value: (r) => r.exit_ts, node: (r) => timeCell(r.exit_ts) },
      { key: "side", label: t("stats.col.side"), align: "left", value: (r) => r.side, node: (r) => sideBadge(r.side), title: t("stats.col.side_tip") },
      {
        key: "net", label: t("stats.col.net"), value: (r) => r.net, render: (r) => `${glyph(resultTone(r))} ${susd(r.net)}`.trim(),
        cls: (r) => `${resultTone(r)} strong st-money-c`, title: t("stats.col.net_tip"),
      },
      {
        key: "acct", label: t("stats.col.acct"), value: (r) => r.ret_account_pct, render: (r) => spctU(r.ret_account_pct, 2), cls: (r) => `${toneOf(r.ret_account_pct, 0.005)} st-money-c`,
        title: t("stats.col.acct_tip"),
      },
      { key: "gross", label: t("stats.col.gross"), value: (r) => r.gross, render: (r) => susd(r.gross), cls: (r) => toneOf(r.gross, 0.005), title: t("stats.col.gross_tip") },
      { key: "fees", label: t("stats.col.fees"), value: (r) => r.fees, render: (r) => usd(r.fees, 2), title: t("stats.col.fees_tip") },
      { key: "fund", label: t("stats.col.funding"), value: (r) => r.funding, render: (r) => usd(r.funding, 2), title: t("stats.col.funding_tip") },
      { key: "qty", label: t("stats.col.qty"), value: (r) => r.qty, render: (r) => num(r.qty, 4), title: t("stats.col.qty_tip") },
      { key: "ep", label: t("stats.col.entry_price"), value: (r) => r.entry_price, render: (r) => num(r.entry_price, 2) },
      { key: "xp", label: t("stats.col.exit_price"), value: (r) => r.exit_price, render: (r) => num(r.exit_price, 2) },
      { key: "entry", label: t("stats.col.entry"), align: "left", value: (r) => r.entry_ts, node: (r) => timeCell(r.entry_ts) },
      { key: "hold", label: t("stats.col.hold"), value: (r) => r.hold_s, render: (r) => fmtDuration(r.hold_s) },
      { key: "ret", label: t("stats.col.ret"), value: (r) => r.ret_pct, render: (r) => spctU(r.ret_pct, 2), cls: (r) => toneOf(r.ret_pct, 0.005), title: t("stats.col.ret_tip") },
      { key: "reason", label: t("stats.col.reason"), align: "left", value: (r) => r.reason, node: (r) => reasonCell(r) },
    ],
  });

  function timeCell(ts: number): HTMLElement {
    return h("time", { dateTime: isoUtc(ts), title: `${agoMs(ts * 1000)} · ${isoUtc(ts)}`, text: stamp(ts) });
  }
  function fillsView(r: StatsTradeRow): HTMLElement {
    const fill = (f: StatsFill) => h("tr", {},
      h("td", { class: "left", text: `${stamp(f.ts)} UTC` }),
      h("td", { class: "left" }, orderBadge(f.side === "buy")),
      h("td", { text: num(f.qty, 4) }), h("td", { text: num(f.price, 2) }), h("td", { text: usd(f.fee, 4) }),
      h("td", { class: "left" }, f.synthetic ? h("span", { class: "badge warn st-mini-badge", text: t("stats.fills.synthetic") }) : null, f.split ? h("span", { class: "badge off st-mini-badge", text: t("stats.fills.flip"), title: t("stats.fills.flip_tip") }) : null,
        f.basis ? h("span", { class: "muted small", text: ` ${fillBasisText(f.basis)}` }) : null),
      h("td", { class: "left muted", text: reasonLabel(f.reason) }));
    return h("div", { class: "st-fills" },
      h("div", { class: "st-fills-head muted small" }, t("stats.fills.head", { n: r.n }), t("stats.fills.margin", { v: spctU(r.ret_margin_pct, 2) }), r.reconstructed ? t("stats.fills.recon_note") : ""),
      h("table", { class: "data st-fills-t" },
        h("caption", { class: "sr-only", text: t("stats.fills.title", { n: r.n }) }),
        h("thead", {}, h("tr", {}, ...[[t("stats.fills.time"), true], [t("stats.col.side"), true], [t("stats.col.qty"), false], [t("stats.fills.price"), false], [t("stats.fills.fee"), false], [t("stats.fills.marks"), true], [t("stats.col.reason"), true]].map(([label, l]) => h("th", { class: l ? "left" : "", scope: "col", text: String(label) })))),
        h("tbody", {}, ...r.fills.map(fill))));
  }

  function paintFilters() {
    const c = stTradeCounts(rows), cur = o.getFilter();
    for (const f of ST_TRADE_FILTERS) {
      const b = btns.get(f.key)!;
      b.replaceChildren(f.label, h("span", { class: "cnt", text: String(c[f.key]) }));
      b.setAttribute("aria-pressed", String(f.key === cur));
      b.disabled = f.key !== "all" && c[f.key] === 0;
    }
  }
  function apply() {
    if (o.getFilter() !== "all" && !stTradeCounts(rows)[o.getFilter()]) o.setFilter("all");
    shown = filterStTrades(rows, o.getFilter());
    table.setEmpty(rows.length ? t("stats.trades.empty_filter") : t("stats.trades.empty"));
    table.setRows(shown);
    paintFilters();
    sum.replaceChildren(moneyStrip(moneySummary(shown)));
    sum.title = shown.length ? moneyLine(moneySummary(shown)) : "";
    count.textContent = rows.length ? t("stats.trades.count", { shown: shown.length, total: rows.length }) : "";
    csvBtn.disabled = !shown.length;
    csvBtn.title = shown.length ? t("stats.trades.csv_tip", { n: shown.length }) : t("stats.trades.csv_none");
  }
  function exportCsv() {
    if (!shown.length) return;
    const f = o.getFilter();
    const name = csvFileName(["stats-trades", ...o.fileHint(), f === "all" ? "" : f]);
    downloadText(name, stTradesToCsv(shown));
    toast(t("stats.trades.downloaded", { name }), "ok");
  }

  const el = h("section", { class: "card st-trades", "aria-label": t("stats.trades.title") },
    h("header", { class: "card-head" }, h("h3", { text: t("stats.trades.title") }), count, h("div", { class: "rv-trades-tools" }, gotoBtn, csvBtn)),
    filterBar, sum, table.el, note);

  return {
    el,
    update(r) {
      rows = r.trades?.rows ?? [];
      const keep = new Set(rows.map((x) => x.n));
      for (const n of [...open]) if (!keep.has(n)) open.delete(n);
      if (selected != null && !keep.has(selected)) { selected = null; o.onSelect(null); }
      gotoBtn.hidden = selected == null;
      apply();
      const tr = r.trades;
      note.textContent = !tr ? "" : tr.truncated
        ? t("stats.trades.note_truncated", { shown: int(rows.length), total: int(tr.total) })
        : t("stats.trades.note_total", { total: int(tr.total) });
    },
    setSelected(n) { selected = n; gotoBtn.hidden = n == null; table.refresh(); },
  };
}
