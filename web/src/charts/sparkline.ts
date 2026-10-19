// Мини-график ряда (SVG): линия, мягкая заливка, точка на конце. Цвета — токены; смысл дублируется числами рядом (график декоративный, aria-label у родителя).
import { svg } from "../ui/dom";

let uid = 0;

export function sparkline(values: readonly number[], o: { w?: number; h?: number; label?: string } = {}): SVGElement | null {
  const v = values.filter((x) => Number.isFinite(x));
  if (v.length < 2) return null;
  const W = o.w ?? 240, H = o.h ?? 56, padX = 4, padT = 6, padB = 6;
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || Math.abs(hi) * 0.01 || 1;
  const X = (i: number) => padX + (i / (v.length - 1)) * (W - padX * 2);
  const Y = (y: number) => padT + (1 - (y - lo) / span) * (H - padT - padB);
  const pts = v.map((y, i) => [X(i), Y(y)] as const);
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const gid = `sp${++uid}`;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "sparkline", role: "img", "aria-label": o.label ?? "График" });
  root.append(
    svg("defs", {}, svg("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 },
      svg("stop", { offset: "0%", "stop-color": "var(--c1)", "stop-opacity": 0.3 }), svg("stop", { offset: "100%", "stop-color": "var(--c1)", "stop-opacity": 0 }))),
    svg("path", { d: `M${pts[0]![0].toFixed(1)},${H} L${line.split(" ").join(" L")} L${pts[pts.length - 1]![0].toFixed(1)},${H} Z`, fill: `url(#${gid})`, stroke: "none" }),
    svg("polyline", { points: line, fill: "none", stroke: "var(--c1)", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round" }),
    svg("circle", { cx: pts[pts.length - 1]![0].toFixed(1), cy: pts[pts.length - 1]![1].toFixed(1), r: 3.4, fill: "var(--c1)", stroke: "var(--surface-1)", "stroke-width": 1.6 }));
  return root;
}
