// Вкладка «Статистика»: таблица закрытых сделок (round trips) — фильтры со счётчиками, сортировка, раскрытие fills, CSV, «Показать ещё».
// Клик по строке выделяет вход/выход сделки на графике капитала (onSelect). Логика фильтров и CSV — statsLogic.ts.
import type { StatsFill, StatsResponse, StatsTradeRow } from "../types";
import { downloadText } from "../ui/download";
import { h } from "../ui/dom";
import { agoMs, fillBasisText, filterStTrades, fmtDuration, fsp, fsusd, isoUtc, moneyLine, moneySummary, resultTone, ST_TRADE_FILTERS, stamp, stTradeCounts, stTradesToCsv, toneOf, type StTradeFilter } from "./statsLogic";
import { icon } from "../ui/icons";
import { int, num, usd } from "../ui/fmt";
import { csvFileName } from "./tradesLogic";
import { glyph } from "../ui/kit";
import { dataTable } from "../ui/table";
import { toast } from "../ui/toast";
import { orderBadge } from "../ui/side";
import { moneyStrip, reasonCell, sideBadge } from "./statsView";

export interface TradesCard {
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
  const gotoBtn = h("button", { class: "btn ghost sm", type: "button", hidden: true, onClick: () => o.onGoto() }, icon("chart", 14), h("span", { text: "Показать на графике" }));
  const csvBtn = h("button", { class: "btn sm", type: "button", onClick: () => exportCsv() }, icon("download", 14), h("span", { text: "Скачать CSV" }));
  const filterBar = h("div", { class: "rv-filters st-filters", role: "group", "aria-label": "Фильтр сделок" });
  const btns = new Map<StTradeFilter, HTMLButtonElement>();
  for (const f of ST_TRADE_FILTERS) {
    const b = h("button", { type: "button", class: "chip-btn", "aria-pressed": "false", onClick: () => { o.setFilter(f.key); apply(); } });
    btns.set(f.key, b);
    filterBar.append(b);
  }

  const table = dataTable<StatsTradeRow>({
    caption: "Закрытые сделки бота",
    sort: { key: "exit", dir: -1 },
    limit: 50,
    empty: "За выбранный период закрытых сделок нет",
    rowKey: (r) => String(r.n),
    rowCls: (r) => `${r.net == null || r.net === 0 ? "" : r.net > 0 ? "tr-win" : "tr-loss"}${r.reconstructed ? " tr-recon" : ""}`,
    isSelected: (r) => r.n === selected,
    onRowClick: (r) => { selected = selected === r.n ? null : r.n; gotoBtn.hidden = selected == null; o.onSelect(selected); table.refresh(); },
    expand: { open: (r) => open.has(r.n), render: (r) => fillsView(r) },
    columns: [
      {
        key: "n", label: "№", align: "left", value: (r) => r.n, title: "Номер сделки (по порядку закрытия); стрелка раскрывает составляющие fills",
        node: (r) => {
          const isOpen = open.has(r.n);
          const b = h("button", {
            type: "button", class: "row-exp", "aria-expanded": String(isOpen), "aria-label": `Сделка ${r.n}: показать fills`,
            onClick: (e: Event) => { e.stopPropagation(); if (open.has(r.n)) open.delete(r.n); else open.add(r.n); table.refresh(); },
            onKeydown: (e: Event) => { e.stopPropagation(); },
          }, h("i", { "aria-hidden": "true" }));
          return h("span", { class: "st-n" }, b, h("span", { text: String(r.n) }));
        },
      },
      { key: "exit", label: "Закрыта, UTC", align: "left", value: (r) => r.exit_ts, node: (r) => timeCell(r.exit_ts) },
      { key: "side", label: "Сторона", align: "left", value: (r) => r.side, node: (r) => sideBadge(r.side), title: "Сторона позиции (лонг/шорт). Цвет результата от стороны не зависит" },
      {
        key: "net", label: "Итог, $", value: (r) => r.net, render: (r) => `${glyph(resultTone(r))} ${fsusd(r.net)}`.trim(),
        cls: (r) => `${resultTone(r)} strong st-money-c`, title: "Итог сделки в долларах = валовый − комиссии − фандинг, USDT: по данным Bybit, а у сделок с бейджем «оценка» — расчёт бота",
      },
      {
        key: "acct", label: "% счёта", value: (r) => r.ret_account_pct, render: (r) => fsp(r.ret_account_pct, 2), cls: (r) => `${toneOf(r.ret_account_pct, 0.005)} st-money-c`,
        title: "Итог / кошелёк счёта на входе в сделку, % (проценты разных сделок не складываются). «—» — нет точки капитала на входе",
      },
      { key: "gross", label: "Валовый", value: (r) => r.gross, render: (r) => fsusd(r.gross), cls: (r) => toneOf(r.gross, 0.005), title: "Валовый P/L до комиссий и фандинга, USDT" },
      { key: "fees", label: "Комиссии*", value: (r) => r.fees, render: (r) => usd(r.fees, 2), title: "* оценка: объём fill × ставка тейкера; у сделок без бейджа «оценка» — фактические по данным Bybit" },
      { key: "fund", label: "Фандинг*", value: (r) => r.funding, render: (r) => usd(r.funding, 2), title: "* оценка по локальным данным; у сделок без бейджа «оценка» — фактический по данным Bybit; уплачено (отрицательное — получено)" },
      { key: "qty", label: "Объём", value: (r) => r.qty, render: (r) => num(r.qty, 4), title: "Объём входа, BTC" },
      { key: "ep", label: "Цена входа", value: (r) => r.entry_price, render: (r) => num(r.entry_price, 2) },
      { key: "xp", label: "Цена выхода", value: (r) => r.exit_price, render: (r) => num(r.exit_price, 2) },
      { key: "entry", label: "Вход, UTC", align: "left", value: (r) => r.entry_ts, node: (r) => timeCell(r.entry_ts) },
      { key: "hold", label: "Удержание", value: (r) => r.hold_s, render: (r) => fmtDuration(r.hold_s) },
      { key: "ret", label: "На объём, %", value: (r) => r.ret_pct, render: (r) => fsp(r.ret_pct, 2), cls: (r) => toneOf(r.ret_pct, 0.005), title: "Net / входной объём, % (не % счёта)" },
      { key: "reason", label: "Причина", align: "left", value: (r) => r.reason, node: (r) => reasonCell(r) },
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
      h("td", { class: "left" }, f.synthetic ? h("span", { class: "badge warn st-mini-badge", text: "синтетическое" }) : null, f.split ? h("span", { class: "badge off st-mini-badge", text: "переворот", title: "Fill, меняющий сторону, разделён между двумя сделками" }) : null,
        f.basis ? h("span", { class: "muted small", text: ` ${fillBasisText(f.basis)}` }) : null),
      h("td", { class: "left muted", text: f.reason ?? "" }));
    return h("div", { class: "st-fills" },
      h("div", { class: "st-fills-head muted small" }, `Сделка №${r.n}: `, `на маржу ≈ ${fsp(r.ret_margin_pct, 2)} (оценка, текущее плечо)`, r.reconstructed ? " · цена выхода реконструирована" : ""),
      h("table", { class: "data st-fills-t" },
        h("caption", { class: "sr-only", text: `Fills сделки ${r.n}` }),
        h("thead", {}, h("tr", {}, ...[["Время", true], ["Сторона", true], ["Объём", false], ["Цена", false], ["Комиссия*", false], ["Пометки", true], ["Причина", true]].map(([t, l]) => h("th", { class: l ? "left" : "", scope: "col", text: String(t) })))),
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
    table.setEmpty(rows.length ? "Нет сделок под выбранный фильтр" : "За выбранный период закрытых сделок нет");
    table.setRows(shown);
    paintFilters();
    sum.replaceChildren(moneyStrip(moneySummary(shown)));
    sum.title = shown.length ? moneyLine(moneySummary(shown)) : "";
    count.textContent = rows.length ? `${shown.length} из ${rows.length}` : "";
    csvBtn.disabled = !shown.length;
    csvBtn.title = shown.length ? `Скачать ${shown.length} сделок в CSV (UTC, проценты — в процентах)` : "Нет сделок для выгрузки";
  }
  function exportCsv() {
    if (!shown.length) return;
    const f = o.getFilter();
    const name = csvFileName(["stats-trades", ...o.fileHint(), f === "all" ? "" : f]);
    downloadText(name, stTradesToCsv(shown));
    toast(`Скачано: ${name}`, "ok");
  }

  const el = h("section", { class: "card st-trades", "aria-label": "Сделки" },
    h("header", { class: "card-head" }, h("h3", { text: "Сделки" }), count, h("div", { class: "rv-trades-tools" }, gotoBtn, csvBtn)),
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
      const t = r.trades;
      note.textContent = !t ? "" : t.truncated
        ? `Показаны последние ${int(rows.length)} из ${int(t.total)} сделок по времени выхода; более ранние не передаются. CSV содержит только показанные.`
        : `Всего сделок в окне: ${int(t.total)}. Итог — по данным Bybit; «оценка» — рассчитано ботом, точнее после обновления фандинга (*); «реконструкция» — закрытие по стопу, цена выхода оценена.`;
    },
    setSelected(n) { selected = n; gotoBtn.hidden = n == null; table.refresh(); },
  };
}
