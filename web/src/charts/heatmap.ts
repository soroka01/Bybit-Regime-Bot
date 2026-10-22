// Тепловые карты на DOM-таблицах (помесячная доходность, развёртка параметров) и профиль параметра на SVG.
// Шкала дивергентная: «минус» (--heat-neg) ↔ нейтраль (--heat-mid) ↔ «плюс» (--heat-pos); интенсивность = |v| / масштаб.
// Цвет текста в ячейке подбирается по контрасту с вычисленной заливкой, значения всегда читаемы; подсказка — плавающая карточка.
import { t } from "../i18n";
import { metricAt, type Num, type SweepResult } from "../types";
import { cssVar, h, observeWidth, svg } from "../ui/dom";
import { int, MONTHS, num, pct, spct } from "../ui/fmt";
import { inkOn, mix } from "./colors";
import { niceScale } from "./scale";
import { chartTip, type ChartTip } from "./tooltip";

interface Scale { cell(t: number | null): { bg: string; ink: string } | null }

/** Читает токены тепловой шкалы (на момент отрисовки; тема может смениться). */
function heatScale(): Scale {
  const neg = cssVar("--heat-neg"), mid = cssVar("--heat-mid"), pos = cssVar("--heat-pos");
  const fg = cssVar("--fg"), inv = cssVar("--bg");
  return {
    cell(t) {
      if (t == null || !isFinite(t)) return null;
      const k = Math.min(Math.abs(t), 1);
      const bg = mix(mid, t >= 0 ? pos : neg, k);
      return { bg, ink: inkOn(bg, fg, inv) };
    },
  };
}

const paint = (td: HTMLElement, c: { bg: string; ink: string } | null) => { if (c) { td.style.background = c.bg; td.style.color = c.ink; } };

/** Ячейки красятся инлайном (цвет вычисляется) — при смене темы их нужно перекрасить. Реестр на слабых ссылках: убранные из документа карты отбрасываются. */
const registry = new Set<WeakRef<{ wrap: HTMLElement; cells: [HTMLElement, number | null][] }>>();
let listening = false;
function repaintOnTheme(wrap: HTMLElement, cells: [HTMLElement, number | null][]): void {
  const rec = { wrap, cells };
  (wrap as HTMLElement & { _heat?: unknown })._heat = rec;      // запись жива, пока жива сама карта
  registry.add(new WeakRef(rec));
  if (listening) return;
  listening = true;
  window.addEventListener("tb-theme", () => {
    const sc = heatScale();
    for (const ref of [...registry]) {
      const r = ref.deref();
      if (!r || !r.wrap.isConnected) { registry.delete(ref); continue; }
      for (const [td, t] of r.cells) paint(td, sc.cell(t));
    }
  });
}

/** Подсказка для ячеек: показывается у ячейки по наведению и по фокусу focusEl (по умолчанию сама ячейка; у кликабельных — кнопка внутри неё). */
function cellTip(wrap: HTMLElement, tip: ChartTip, td: HTMLElement, title: string, value: string, tone: string, label: string, note?: string, focusEl: HTMLElement = td) {
  const show = () => {
    const hr = wrap.getBoundingClientRect(), r = td.getBoundingClientRect();
    tip.show({ title, rows: [{ label, value, color: td.style.background, box: true, tone }], note }, r.left - hr.left + r.width / 2, r.top - hr.top + r.height / 2);
  };
  td.addEventListener("pointerenter", show);
  focusEl.addEventListener("focus", show);
  td.addEventListener("pointerleave", () => tip.hide());
  focusEl.addEventListener("blur", () => tip.hide());
}

const legend = (lo: string, hi: string) =>
  h("div", { class: "heat-legend", "aria-hidden": "true" }, h("span", { text: lo }), h("span", { class: "bar" }), h("span", { text: hi }));

export function monthlyHeatmap(
  monthly: { y: number; m: number; v: Num }[],
  yearly: { y: number; v: Num }[],
): HTMLElement {
  if (!monthly.length) return h("p", { class: "muted", text: t("charts.no_data") });
  const years = [...new Set(monthly.map((x) => x.y))].sort();
  const map = new Map(monthly.map((x) => [`${x.y}-${x.m}`, x.v]));
  const ymap = new Map(yearly.map((x) => [x.y, x.v]));
  const sc = heatScale();
  const wrap = h("div", { class: "heat-wrap" });
  const tip = chartTip(wrap);
  const cells: [HTMLElement, number | null][] = [];
  const head = h("tr", null, h("th", { text: "" }), ...MONTHS.map((m) => h("th", { scope: "col", text: m })), h("th", { scope: "col", class: "sep", text: t("charts.heat.year") }));
  const rows = years.map((y) =>
    h("tr", null,
      h("th", { scope: "row", text: String(y) }),
      ...MONTHS.map((_, i) => {
        const v = map.get(`${y}-${i + 1}`);
        const td = h("td", { text: v == null ? "" : pct(v, 0), "aria-label": v == null ? undefined : `${MONTHS[i]} ${y}: ${spct(v, 2)}` });
        if (v != null) { cells.push([td, v / 0.25]); paint(td, sc.cell(v / 0.25)); cellTip(wrap, tip, td, `${MONTHS[i]} ${y}`, spct(v, 2), v >= 0 ? "pos" : "neg", t("charts.return")); }
        return td;
      }),
      (() => {
        const v = ymap.get(y) ?? null;
        const td = h("td", { class: "sep strong", text: pct(v, 0), "aria-label": v == null ? undefined : `${y}: ${spct(v, 2)}` });
        if (v != null) { cells.push([td, v / 0.8]); paint(td, sc.cell(v / 0.8)); cellTip(wrap, tip, td, t("charts.heat.year_title", { year: y }), spct(v, 2), v >= 0 ? "pos" : "neg", t("charts.return")); }
        return td;
      })()));
  wrap.append(h("div", { class: "table-wrap", style: "border:0;background:none" }, h("table", { class: "heat" }, h("thead", null, head), h("tbody", null, ...rows))),
    legend("−25%", "+25%"), tip.el);
  repaintOnTheme(wrap, cells);
  return wrap;
}

export const SWEEP_METRICS: { key: string; label: string; fmt(v: Num): string; higherIsBetter: boolean }[] = [
  { key: "total_return", label: t("charts.return"), fmt: (v) => spct(v, 0), higherIsBetter: true },
  { key: "sharpe", label: "Sharpe", fmt: (v) => num(v, 2), higherIsBetter: true },
  { key: "calmar", label: "Calmar", fmt: (v) => num(v, 2), higherIsBetter: true },
  { key: "avg_month", label: t("charts.heat.avg_month"), fmt: (v) => spct(v, 1), higherIsBetter: true },
  { key: "max_drawdown", label: t("charts.heat.max_dd"), fmt: (v) => pct(v, 0), higherIsBetter: true },
  { key: "trades", label: t("charts.trades"), fmt: (v) => int(v), higherIsBetter: true },
];

export function sweepHeatmap(
  res: SweepResult, metric: string, cur: Record<string, number>, onPick: (x: number, y: number) => void,
): HTMLElement {
  const ry = res.y;
  if (!ry) return h("p", { class: "muted", text: t("charts.heat.no_second_axis") });
  const spec = SWEEP_METRICS.find((m) => m.key === metric) ?? SWEEP_METRICS[0]!;
  const grid = res.grids[metric] ?? [];
  const flat = grid.flat().filter((v): v is number => v != null && isFinite(v));
  const maxAbs = Math.max(...flat.map(Math.abs), 1e-9);
  const lo = Math.min(...flat), hi = Math.max(...flat);
  const norm = (v: Num): number | null => {
    if (v == null) return null;
    if (metric === "max_drawdown") return hi === lo ? 0 : ((v - lo) / (hi - lo)) * 2 - 1;
    if (metric === "trades") return hi === lo ? 0 : ((v - lo) / (hi - lo)) * 1.2 - 0.2;
    return v / maxAbs;
  };
  const fmtAxis = (v: number) => (Number.isInteger(v) ? String(v) : num(v, 2));
  const sc = heatScale();
  const wrap = h("div", { class: "heat-wrap" });
  const tip = chartTip(wrap);
  const cells: [HTMLElement, number | null][] = [];
  const bestVal = metric === "trades" || !flat.length ? null : Math.max(...flat);   // лучшая ячейка (у всех метрик «больше — лучше»; просадка — менее отрицательная)
  let bestMarked = false;
  const head = h("tr", null, h("th", { class: "corner", text: `${ry.name} ↓ / ${res.x.name} →` }),
    ...res.x.values.map((x) => h("th", { scope: "col", text: fmtAxis(x) })));
  const rows = ry.values.map((y, j) =>
    h("tr", null, h("th", { scope: "row", text: fmtAxis(y) }),
      ...res.x.values.map((x, i) => {
        const v = grid[j]?.[i] ?? null;
        const isBase = cur[res.x.name] === x && cur[ry.name] === y;
        const isBest = bestVal != null && v === bestVal && !bestMarked;
        if (isBest) bestMarked = true;
        // ячейка таблицы остаётся ячейкой (роль button на td ломала семантику таблицы): кликабельная кнопка — внутри неё
        const pick = () => onPick(x, y);
        const btn = h("button", {
          class: "pick", type: "button", onClick: pick, text: spec.fmt(v),
          "aria-label": t("charts.heat.aria_pick", { xname: res.x.name, x, yname: ry.name, y, value: spec.fmt(v), best: isBest ? t("charts.heat.aria_best") : "", base: isBase ? t("charts.heat.aria_base") : "" }),
        });
        const td = h("td", { class: "pickable" + (isBase ? " base" : "") + (isBest ? " best" : "") }, btn);
        cells.push([td, norm(v)]);
        paint(td, sc.cell(norm(v)));
        cellTip(wrap, tip, td, `${res.x.name}=${fmtAxis(x)} · ${ry.name}=${fmtAxis(y)}`, spec.fmt(v),
          v != null && v < 0 && metric !== "max_drawdown" ? "neg" : v != null && v > 0 && metric !== "max_drawdown" && metric !== "trades" ? "pos" : "", spec.label, [isBase ? t("charts.heat.note_base") : "", isBest ? t("charts.heat.note_best") : "", t("charts.pick_hint")].filter(Boolean).join(" · "), btn);
        return td;
      })));
  const sym = metric !== "max_drawdown" && metric !== "trades";
  wrap.append(h("div", { class: "table-wrap", style: "border:0;background:none" }, h("table", { class: "heat sweep" }, h("thead", null, head), h("tbody", null, ...rows))),
    sym ? legend(`−${spec.fmt(maxAbs).replace(/^[+−-]/, "")}`, `+${spec.fmt(maxAbs).replace(/^[+−-]/, "")}`)
      : legend(t("charts.heat.worse", { v: spec.fmt(lo) }), t("charts.heat.better", { v: spec.fmt(hi) })), tip.el);
  repaintOnTheme(wrap, cells);
  return wrap;
}

/**
 * Одномерный профиль: метрика против значения параметра; кликабельные/фокусируемые точки, текущее и лучшее значения выделены.
 * Возвращает элемент и destroy() — отключает ResizeObserver (вызывать перед удалением элемента).
 */
export function sweepProfile(
  res: SweepResult, metric: string, current: number, onPick: (x: number) => void,
): { el: HTMLElement; destroy(): void } {
  const spec = SWEEP_METRICS.find((m) => m.key === metric) ?? SWEEP_METRICS[0]!;
  const xs = res.x.values;
  const pts = res.points ?? [];
  const vals: Num[] = xs.map((_, i) => metricAt(pts[i], metric));
  const finite = vals.filter((v): v is number => v != null && isFinite(v));
  if (!finite.length) return { el: h("p", { class: "muted", text: t("charts.profile.no_metric") }), destroy() {} };
  const bestVal = metric === "trades" ? null : Math.max(...finite);
  const bestIdx = bestVal == null ? -1 : vals.findIndex((v) => v === bestVal);
  const curIdx = xs.reduce((bi, x, i) => (Math.abs(x - current) < Math.abs((xs[bi] ?? Infinity) - current) ? i : bi), 0);
  const fmtAxis = (v: number) => (Number.isInteger(v) ? String(v) : num(v, 2));

  const host = h("div", { class: "profile" });
  const tip = chartTip(host);
  const draw = (w: number) => {
    const W = Math.max(w, 320), H = 240, L = 58, R = 18, T = 18, B = 36;
    let dmin = Math.min(...finite), dmax = Math.max(...finite);
    if (metric !== "trades") { dmin = Math.min(dmin, 0); dmax = Math.max(dmax, 0); }
    const { lo, hi, ticks: tickVals } = niceScale(dmin, dmax, 5);
    const X = (i: number) => L + (xs.length === 1 ? (W - L - R) / 2 : (i / (xs.length - 1)) * (W - L - R));
    const Y = (v: number) => T + ((hi - v) / (hi - lo)) * (H - T - B);
    const grid = cssVar("--grid"), muted = cssVar("--fg-muted"), axis = cssVar("--axis"), line = cssVar("--c1"), neg = cssVar("--neg"), ok = cssVar("--ok");
    const fg = cssVar("--fg"), surface = cssVar("--surface-1");
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H, role: "group", "aria-label": t("charts.profile.aria", { metric: spec.label, x: res.x.name }) });
    for (const v of tickVals) {
      root.append(svg("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: grid }),
        Object.assign(svg("text", { x: L - 8, y: Y(v) + 4, "text-anchor": "end", fill: muted, "font-size": 11 }), { textContent: spec.fmt(v) }));
    }
    if (lo < 0 && hi > 0) root.append(svg("line", { x1: L, x2: W - R, y1: Y(0), y2: Y(0), stroke: axis, "stroke-width": 1 }));
    const every = Math.ceil(xs.length / Math.max(1, Math.floor((W - L - R) / 44)));
    xs.forEach((x, i) => {
      if (i % every === 0) root.append(Object.assign(svg("text", { x: X(i), y: H - 16, "text-anchor": "middle", fill: muted, "font-size": 11 }), { textContent: fmtAxis(x) }));
    });
    root.append(Object.assign(svg("text", { x: (L + W - R) / 2, y: H - 2, "text-anchor": "middle", fill: muted, "font-size": 11 }), { textContent: res.x.name }));
    root.append(svg("line", { x1: X(curIdx), x2: X(curIdx), y1: T, y2: H - B, stroke: fg, "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0.45 }),
      Object.assign(svg("text", { x: X(curIdx), y: T - 5, "text-anchor": "middle", fill: muted, "font-size": 10 }), { textContent: t("charts.profile.now") }));
    const linePts = xs.map((_, i) => (vals[i] == null ? null : `${X(i).toFixed(1)},${Y(vals[i]!).toFixed(1)}`)).filter(Boolean).join(" ");
    root.append(svg("polyline", { points: linePts, fill: "none", stroke: line, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    xs.forEach((x, i) => {
      const v = vals[i];
      if (v == null) return;
      const isCur = i === curIdx, isBest = i === bestIdx;
      const g = svg("g", { class: "pt", tabindex: 0, role: "button", "aria-label": t("charts.profile.point_aria", { name: res.x.name, xv: fmtAxis(x), value: spec.fmt(v), trades: int(pts[i]?.trades ?? null) }) });
      const dot = svg("circle", { cx: X(i), cy: Y(v), r: isBest || isCur ? 6 : 4.5, fill: isBest ? ok : v < 0 && metric !== "trades" ? neg : line, stroke: isCur ? fg : surface, "stroke-width": 2 });
      g.append(svg("circle", { cx: X(i), cy: Y(v), r: 14, fill: "transparent" }), dot); // увеличенная зона наведения/клика (вторым — видимая точка)
      const show = () => {
        const hr = host.getBoundingClientRect(), r = dot.getBoundingClientRect();
        tip.show({
          title: `${res.x.name} = ${fmtAxis(x)}`,
          rows: [{ label: spec.label, value: spec.fmt(v), color: line, tone: metric !== "trades" && metric !== "max_drawdown" ? (v < 0 ? "neg" : v > 0 ? "pos" : "") : "" },
            { label: t("charts.trades"), value: int(pts[i]?.trades ?? null) }],
          note: [isCur ? t("charts.profile.note_cur") : "", isBest ? t("charts.profile.note_best") : "", t("charts.pick_hint")].filter(Boolean).join(" · "),
        }, r.left - hr.left + r.width / 2, r.top - hr.top);
      };
      g.addEventListener("pointerenter", show);
      g.addEventListener("focus", show);
      g.addEventListener("pointerleave", () => tip.hide());
      g.addEventListener("blur", () => tip.hide());
      g.addEventListener("click", () => onPick(x));
      g.addEventListener("keydown", (e) => { if ((e as KeyboardEvent).key === "Enter" || (e as KeyboardEvent).key === " ") { e.preventDefault(); onPick(x); } });
      root.append(g);
    });
    host.replaceChildren(root, h("p", { class: "muted small" },
      bestIdx >= 0 ? t("charts.profile.legend_best", { name: res.x.name, x: fmtAxis(xs[bestIdx]!) }) : t("charts.profile.legend")), tip.el);
  };
  let last = 0;
  const stop = observeWidth(host, (w) => { if (w > 0 && Math.abs(w - last) > 1) { last = w; draw(w); } });
  const onTheme = () => draw(last || host.clientWidth || 640);
  window.addEventListener("tb-theme", onTheme);
  draw(host.clientWidth || 640);
  return { el: host, destroy() { stop(); window.removeEventListener("tb-theme", onTheme); } };
}
