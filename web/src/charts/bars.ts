// Столбчатая диаграмма на SVG (годовая доходность, доходность окон walk-forward).
// Спецификация: столбец ≤ 28 px, скругление 4 px только на «рабочем» конце (у нулевой линии — прямой), зазор 2 px между соседями,
// окраска по знаку (--pos / --neg) или цветом ряда, подпись значения у конца при достаточном месте, подсказка по наведению и фокусу.
import type { Num } from "../types";
import { cssVar, h, observeWidth, svg } from "../ui/dom";
import { niceScale } from "./scale";
import { chartTip } from "./tooltip";

export interface BarSeries {
  name: string;
  color: string; // CSS-переменная
  values: Num[];
  titles?: string[];
}

export interface BarOpts {
  categories: string[];
  series: BarSeries[];
  fmt(v: number): string;
  height?: number;
  /** Окрашивать по знаку (одна серия) вместо цвета серии. */
  bySign?: boolean;
}

/** Прямоугольник со скруглёнными углами только на конце (up: вверху, иначе внизу); r урезается по размерам. */
function barPath(x: number, y: number, w: number, hgt: number, r: number, endTop: boolean): string {
  const rr = Math.max(0, Math.min(r, w / 2, hgt));
  if (rr < 0.5) return `M${x},${y}h${w}v${hgt}h${-w}Z`;
  return endTop
    ? `M${x},${y + hgt}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + hgt}Z`
    : `M${x},${y}H${x + w}V${y + hgt - rr}Q${x + w},${y + hgt} ${x + w - rr},${y + hgt}H${x + rr}Q${x},${y + hgt} ${x},${y + hgt - rr}Z`;
}

export function barChart(host: HTMLElement, getOpts: () => BarOpts): { redraw(): void; destroy(): void } {
  let width = 0;
  const tip = chartTip(host);
  const legend = h("div", { class: "chart-legend" });
  const draw = () => {
    const o = getOpts();
    const H = o.height ?? 200;
    const W = Math.max(width, 240);
    const padL = 48, padB = 34, padT = 16, padR = 8;
    const all = o.series.flatMap((s) => s.values).filter((v): v is number => v != null && isFinite(v));
    tip.hide();
    if (!all.length || !o.categories.length) {
      host.replaceChildren(h("p", { class: "muted small", text: "Нет данных для графика" }));
      return;
    }
    const { lo, hi, ticks: tickVals } = niceScale(Math.min(0, ...all), Math.max(0, ...all), H < 180 ? 4 : 5);
    const y = (v: number) => padT + ((hi - v) / (hi - lo)) * (H - padT - padB);
    const gw = (W - padL - padR) / o.categories.length;
    const bw = Math.max(2, Math.min(28, (gw * 0.72) / o.series.length) - 2);       // 2 px зазор между соседями, потолок 28 px
    const grid = cssVar("--grid"), muted = cssVar("--fg-muted"), axis = cssVar("--axis"), fg = cssVar("--fg");
    const font = cssVar("--font-ui");
    // цвета читаются один раз на перерисовку, а не в цикле по столбцам
    const posC = cssVar("--pos"), negC = cssVar("--neg");
    const seriesColors = o.series.map((s) => cssVar(s.color));
    const root = svg("svg", { class: "bars", viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Столбчатая диаграмма" });
    root.style.fontFamily = font;
    for (const v of tickVals) {
      root.append(
        svg("line", { x1: padL, x2: W - padR, y1: y(v), y2: y(v), stroke: grid, "stroke-width": 1 }),
        Object.assign(svg("text", { x: padL - 8, y: y(v) + 4, "text-anchor": "end", fill: muted, "font-size": 11 }), { textContent: o.fmt(v) }),
      );
    }
    root.append(svg("line", { x1: padL, x2: W - padR, y1: y(0), y2: y(0), stroke: axis, "stroke-width": 1 }));
    const every = Math.ceil(o.categories.length / Math.max(1, Math.floor((W - padL) / 58)));
    const labelOk = (gw / o.series.length) >= 44;
    o.categories.forEach((c, i) => {
      const cx = padL + gw * i + gw / 2;
      if (i % every === 0) {
        root.append(Object.assign(svg("text", { x: cx, y: H - 8, "text-anchor": "middle", fill: muted, "font-size": 11 }), { textContent: c }));
      }
      o.series.forEach((s, k) => {
        const v = s.values[i];
        if (v == null || !isFinite(v)) return;
        const x = cx - ((bw + 2) * o.series.length) / 2 + k * (bw + 2) + 1;
        const col = o.bySign ? (v >= 0 ? posC : negC) : seriesColors[k]!;
        const top = Math.min(y(v), y(0)), hgt = Math.max(1.5, Math.abs(y(v) - y(0)));
        const bar = svg("path", { class: "bar", d: barPath(x, top, bw, hgt, 4, v >= 0), fill: col, tabindex: 0, role: "img",
          "aria-label": `${c}${o.series.length > 1 ? " · " + s.name : ""}: ${o.fmt(v)}` });
        const show = () => {
          const hr = host.getBoundingClientRect(), br = bar.getBoundingClientRect();
          tip.show({
            title: c,
            rows: [{ label: o.series.length > 1 ? s.name : "Значение", value: o.fmt(v), color: col, box: true, tone: o.bySign ? (v >= 0 ? "pos" : "neg") : "" }],
            note: s.titles?.[i] ? s.titles[i] : undefined,
          }, br.left - hr.left + br.width / 2, br.top - hr.top + Math.min(br.height / 2, 12));
        };
        bar.addEventListener("pointermove", show);
        bar.addEventListener("focus", show);
        bar.addEventListener("pointerleave", () => tip.hide());
        bar.addEventListener("blur", () => tip.hide());
        root.append(bar);
        if (labelOk && o.series.length === 1) {   // подпись значения у конца столбца — только если помещается
          root.append(Object.assign(svg("text", {
            x: x + bw / 2, y: v >= 0 ? top - 5 : top + hgt + 12, "text-anchor": "middle", fill: fg, "font-size": 11, "font-weight": 600, "pointer-events": "none",
          }), { textContent: o.fmt(v) }));
        }
      });
    });
    // легенда — только для нескольких рядов
    legend.replaceChildren(...(o.series.length > 1 ? o.series.map((s, k) => {
      const key = h("i", { class: "lg-key area" });
      key.style.setProperty("--k", seriesColors[k]!);
      return h("span", { class: "lg-item" }, key, s.name);
    }) : []));
    legend.hidden = o.series.length <= 1;
    host.replaceChildren(legend, root, tip.el);
  };
  const stop = observeWidth(host, (w) => { if (w > 0 && Math.abs(w - width) > 1) { width = w; draw(); } });
  const onTheme = () => draw();
  window.addEventListener("tb-theme", onTheme);
  width = host.clientWidth;
  draw();
  return { redraw: draw, destroy: () => { stop(); window.removeEventListener("tb-theme", onTheme); } };
}
