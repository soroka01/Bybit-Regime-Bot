// Набор встроенных SVG-иконок (24×24, штрих currentColor). Без внешних зависимостей; иконки — декоративные (aria-hidden),
// смысл всегда дублируется текстом или aria-label родителя.
import { svg } from "./dom";

interface Def { d?: string[]; c?: [number, number, number][]; r?: [number, number, number, number, number][]; fill?: string[] }

const ICONS: Record<string, Def> = {
  sun: { c: [[12, 12, 4]], d: ["M12 2v2", "M12 20v2", "m4.93 4.93 1.41 1.41", "m17.66 17.66 1.41 1.41", "M2 12h2", "M20 12h2", "m6.34 17.66-1.41 1.41", "m19.07 4.93-1.41 1.41"] },
  moon: { d: ["M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"] },
  auto: { c: [[12, 12, 9]], fill: ["M12 3a9 9 0 0 1 0 18Z"] },
  x: { d: ["M18 6 6 18", "m6 6 12 12"] },
  warn: { d: ["m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3", "M12 9v4", "M12 17h.01"] },
  info: { c: [[12, 12, 10]], d: ["M12 16v-4", "M12 8h.01"] },
  error: { c: [[12, 12, 10]], d: ["m15 9-6 6", "m9 9 6 6"] },
  ok: { c: [[12, 12, 10]], d: ["m8.5 12.5 2.5 2.5 4.5-5"] },
  inbox: { d: ["M22 12h-6l-2 3h-4l-2-3H2", "M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"] },
  power: { d: ["M12 2v10", "M18.4 6.6a9 9 0 1 1-12.77.04"] },
  refresh: { d: ["M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8", "M21 3v5h-5", "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16", "M8 16H3v5"] },
  copy: { r: [[9, 9, 13, 13, 2]], d: ["M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"] },
  target: { c: [[12, 12, 10], [12, 12, 6], [12, 12, 2]] },
  keyboard: { r: [[2, 4, 20, 16, 2]], d: ["M6 8h.01", "M10 8h.01", "M14 8h.01", "M18 8h.01", "M8 12h.01", "M12 12h.01", "M16 12h.01", "M7 16h10"] },
  chart: { d: ["M3 3v18h18", "m7 14 4-4 3 3 5-6"] },
  search: { c: [[11, 11, 7]], d: ["m20 20-3.5-3.5"] },
  swap: { d: ["M7 4v14", "m3 14 4 4 4-4", "M17 20V6", "m21 10-4-4-4 4"] },
  pulse: { d: ["M3 12h4l3-8 4 16 3-8h4"] },
  clock: { c: [[12, 12, 9]], d: ["M12 7v5l3 2"] },
  flat: { d: ["M4 12h16"] },
  chevron: { d: ["m9 6 6 6-6 6"] },
  arrow: { d: ["M5 12h14", "m13 6 6 6-6 6"] },
  reset: { d: ["M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5"] },
  layers: { d: ["M12 2 2 7l10 5 10-5-10-5Z", "m2 17 10 5 10-5", "m2 12 10 5 10-5"] },
  percent: { c: [[6.5, 6.5, 2.5], [17.5, 17.5, 2.5]], d: ["M19 5 5 19"] },
  gauge: { d: ["m12 14 4-4", "M3.34 19a10 10 0 1 1 17.32 0"] },
  download: { d: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "m7 10 5 5 5-5", "M12 15V3"] },
  plus: { d: ["M12 5v14", "M5 12h14"] },
  trash: { d: ["M3 6h18", "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6", "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"] },
  bot: { r: [[3, 11, 18, 10, 2]], c: [[12, 5, 2]], d: ["M12 7v4", "M8 16h.01", "M16 16h.01"] },
  check: { d: ["m5 12 5 5L20 7"] },
  grid: { r: [[3, 3, 18, 18, 2]], d: ["M3 9h18", "M3 15h18", "M9 3v18", "M15 3v18"] },
  candles: { d: ["M7 4v3", "M7 15v5", "M17 3v5", "M17 17v4", "M5 7h4v8H5z", "M15 8h4v9h-4z"] },
  sliders: { d: ["M4 21v-7", "M4 10V3", "M12 21v-9", "M12 8V3", "M20 21v-5", "M20 12V3", "M1 14h6", "M9 8h6", "M17 16h6"] },
  flask: { d: ["M9 3h6", "M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2.2h12.4a1.5 1.5 0 0 0 1.3-2.2L14 9V3"] },
  github: { fill: ["M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"] },
};

/** Иконка по имени; size — в пикселях. */
export function icon(name: keyof typeof ICONS | string, size = 16): SVGElement {
  const def = ICONS[name] ?? ICONS.info!;
  const el = svg("svg", {
    class: "icon", viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor",
    "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false",
  });
  for (const [cx, cy, r] of def.c ?? []) el.append(svg("circle", { cx, cy, r }));
  for (const [x, y, w, h, rx] of def.r ?? []) el.append(svg("rect", { x, y, width: w, height: h, rx }));
  for (const d of def.d ?? []) el.append(svg("path", { d }));
  for (const d of def.fill ?? []) el.append(svg("path", { d, fill: "currentColor", stroke: "none" }));
  return el;
}

/** Знак бренда: скруглённый квадрат с графиком (цвет — через CSS .logo). */
export function brandMark(): SVGElement {
  const el = svg("svg", { viewBox: "0 0 24 24", width: 16, height: 16, fill: "none", stroke: "currentColor", "stroke-width": 2.6, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
  el.append(svg("path", { d: "M4 17l5-6 4 4 7-9" }), svg("path", { d: "M15 6h5v5", "stroke-width": 2.2 }));
  return el;
}
