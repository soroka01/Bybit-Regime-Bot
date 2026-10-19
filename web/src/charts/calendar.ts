// Календарь дневного P/L в стиле GitHub: недели — столбцы, дни — строки, понедельник первым; дивергентная шкала --heat-neg ↔ --heat-mid ↔ --heat-pos.
// Цвет ячейки задаёт CSS (color-mix по --k), поэтому смена темы не требует перекраски. Для окон короче 14 суток — компактный список по дням.
// Сетка и раскраска — чистая логика (tabs/statsLogic.ts: calendarSections / calLevel / calendarList).
import type { StatsCalendarCell } from "../types";
import { h } from "../ui/dom";
import { glyph } from "../ui/kit";
import { susd, toneOf, usd } from "../ui/fmt";
import { calendarIsCompact, calendarList, calendarSections, calLevel, ymdRu } from "../tabs/statsLogic";
import { chartTip } from "./tooltip";

const WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const wdOf = (date: string): string => WD[(new Date(date + "T00:00:00Z").getUTCDay() + 6) % 7]!;

export function calendarChart(startDate: string, endDate: string, cells: readonly StatsCalendarCell[]): HTMLElement {
  const wrap = h("div", { class: "cal" });
  const tip = chartTip(wrap);
  const traded = cells.filter((c) => c.trades > 0);
  const wins = traded.filter((c) => (c.net ?? 0) > 0).length, losses = traded.filter((c) => (c.net ?? 0) < 0).length;
  const maxAbs = Math.max(0, ...traded.map((c) => Math.abs(c.net ?? 0)));

  if (calendarIsCompact(startDate, endDate)) {
    const list = calendarList(startDate, endDate, cells);
    const rows = list.map((d) => {
      const lvl = calLevel(d.net, maxAbs);
      const tone = toneOf(d.net, 0.005);
      return h("li", { class: `cal-row${d.trades ? "" : " idle"}` },
        h("span", { class: "cal-row-d" }, h("b", { text: wdOf(d.date) }), ` ${ymdRu(d.date).slice(0, 5)}`),
        d.trades ? h("span", { class: "cal-row-bar", "aria-hidden": "true" }, h("i", { class: tone, style: `width:${Math.max(6, Math.abs(lvl) * 100)}%` })) : h("span"),
        h("span", { class: `cal-row-n ${tone}`, text: d.trades ? `${glyph(tone)} ${susd(d.net)}`.trim() : "—" }),
        h("span", { class: "cal-row-t muted", text: d.trades ? `${d.trades} сд.` : "сделок нет" }));
    });
    wrap.append(h("ul", { class: "cal-list", "aria-label": "P/L по дням" }, ...rows), tip.el);
    return wrap;
  }

  const sections = calendarSections(startDate, endDate, cells);
  for (const sec of sections) {
    const g = sec.grid;
    const cs = g.cols <= 8 ? 40 : g.cols <= 20 ? 30 : g.cols <= 30 ? 26 : 20;      // размер клетки, px: короткие периоды — крупнее
    const dims = `--n:${g.cols};--cs:${cs}px`;
    const months = h("div", { class: "cal-months", style: dims, "aria-hidden": "true" },
      ...g.months.map((m) => h("span", { style: `grid-column:${m.col + 1}`, text: m.label })));
    const days = h("div", { class: "cal-wd", style: `--cs:${cs}px`, "aria-hidden": "true" }, ...WD.map((d, i) => h("span", { style: `grid-row:${i + 1}`, text: i % 2 === 0 ? d : "" })));
    const grid = h("div", {
      class: "cal-grid", style: dims, role: "img",
      "aria-label": `Календарь P/L по дням${sec.year ? ` за ${sec.year} год` : ""}: торговых дней ${traded.length}, прибыльных ${wins}, убыточных ${losses}`,
    });
    for (const d of g.days) {
      if (!d.inRange) { continue; }
      const lvl = calLevel(d.net, maxAbs || g.maxAbs);
      const cls = `cal-cell${d.trades ? (lvl > 0 ? " pos" : lvl < 0 ? " neg" : " zero") : ""}`;
      const c = h("div", { class: cls, style: `grid-row:${d.row + 1};grid-column:${d.col + 1};--k:${Math.abs(lvl).toFixed(3)}` });
      const show = () => {
        const hr = wrap.getBoundingClientRect(), r = c.getBoundingClientRect();
        tip.show({
          title: `${wdOf(d.date)} ${ymdRu(d.date)}`,
          rows: d.trades
            ? [{ label: "Net", value: susd(d.net), color: getComputedStyle(c).backgroundColor, box: true, tone: toneOf(d.net, 0.005) }, { label: "Сделок", value: String(d.trades) }]
            : [{ label: "Сделок", value: "нет" }],
        }, r.left - hr.left + r.width / 2, r.top - hr.top + r.height / 2);
      };
      c.addEventListener("pointerenter", show);
      c.addEventListener("pointerleave", () => tip.hide());
      grid.append(c);
    }
    wrap.append(h("div", { class: "cal-sec" },
      sec.year ? h("div", { class: "cal-year", text: String(sec.year) }) : null,
      h("div", { class: "cal-body" }, h("span"), months, days, grid)));
  }
  wrap.append(
    h("div", { class: "heat-legend cal-legend", "aria-hidden": "true" },
      h("span", { text: maxAbs ? `−${usd(maxAbs, 2)}` : "убыток" }), h("span", { class: "bar" }), h("span", { text: maxAbs ? `+${usd(maxAbs, 2)}` : "прибыль" })),
    tip.el);
  return wrap;
}
