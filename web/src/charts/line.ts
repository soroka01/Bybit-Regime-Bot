// Обёртка над uPlot: тема из CSS-переменных, лог-шкала, синхронный курсор, маркеры сделок, градиентная заливка,
// своя легенда-чипы и плавающая подсказка (вместо стандартной легенды со значениями).
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { Num } from "../types";
import { cssVar, h, observeWidth } from "../ui/dom";
import { date, fmtUtc, MONTHS } from "../ui/fmt";
import { dmLocal } from "../ui/fmtTime";
import { withAlpha } from "./colors";
import { chartTip, type TipRow } from "./tooltip";

interface LineSpec {
  label: string;
  /** Имя CSS-переменной цвета, например "--c1". */
  color: string;
  width?: number;
  dash?: number[];
  /** Заливка под линией — непрозрачность у линии (0..1); градиент затухает к краю графика. По умолчанию у первого ряда 0.16, если рядов ≤ 2. */
  fill?: number;
  /** "down": заливка от линии вниз (по умолчанию); "up": от линии вверх до нуля (просадка — насыщеннее у дна). */
  fillMode?: "down" | "up";
  fmt?: (v: number) => string;
  step?: boolean;
  /** «Тихий» ряд (границы полосы): рисуется тонкой линией, но не попадает в легенду, подсказку и точки курсора. */
  quiet?: boolean;
}

interface ChartMarker {
  t: number;
  p: number;
  kind: "entry" | "exit";
  side: "long" | "short";
  /** Выделенный маркер (выбранная сделка): крупнее, с обводкой цвета текста. */
  active?: boolean;
}

/** Закрашенная полоса между двумя рядами (индексы в `series`): например, мин…макс капитала внутри интервала. */
interface LineBand { hi: number; lo: number; color: string; alpha?: number }

interface LineOpts {
  height: number;
  series: LineSpec[];
  yFmt: (v: number) => string;
  log?: boolean;
  sync?: string;
  yMin?: number;
  yMax?: number;
  bands?: LineBand[];
  /** Строка-пояснение под значениями в подсказке (по индексу точки). */
  tipNote?: (idx: number) => string | undefined;
}

export interface LineChart {
  setData(x: number[], ys: Num[][]): void;
  setLog(on: boolean): void;
  setMarkers(m: ChartMarker[]): void;
  destroy(): void;
}

const pad2 = (n: number) => String(n).padStart(2, "0");

/** Подписи оси времени (UTC, по-русски): годы → месяцы (январь = год) → дни → часы. incr — шаг между метками, секунды. */
function timeTicks(_u: uPlot, splits: number[], _axis: number, _space: number, incr: number): string[] {
  return splits.map((v) => {
    const d = new Date(v * 1000);
    const mo = d.getUTCMonth(), day = d.getUTCDate();
    if (incr >= 300 * 86400) return String(d.getUTCFullYear());
    if (incr >= 27 * 86400) return mo === 0 ? String(d.getUTCFullYear()) : MONTHS[mo]!;
    if (incr >= 86400) return day === 1 ? MONTHS[mo]! : dmLocal(day, mo + 1);
    const hm = `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
    return hm === "00:00" ? dmLocal(day, mo + 1) : hm;
  });
}

/** Подпись момента в подсказке: дата (если время суток нулевое) или дата и время UTC. */
const fmtStamp = (t: number): string => (t % 86400 === 0 ? date(t) : fmtUtc(t));

export function lineChart(host: HTMLElement, o: LineOpts): LineChart {
  let u: uPlot | null = null;
  let rawX: number[] = [];
  let rawYs: Num[][] = o.series.map(() => []);
  let log = !!o.log;
  let markers: ChartMarker[] = [];
  const current = () =>
    [rawX, ...rawYs.map((y) => (log ? y.map((v) => (v != null && v > 0 ? v : null)) : y))] as uPlot.AlignedData;

  const tip = chartTip(host);
  const legend = h("div", { class: "chart-legend" });
  host.append(legend);

  const build = () => {
    u?.destroy();
    const text = cssVar("--fg-muted"), grid = cssVar("--grid"), surface = cssVar("--surface-1");
    const font = `11px ${cssVar("--font-ui") || "system-ui, sans-serif"}`;
    const colors = o.series.map((s) => cssVar(s.color) || "#888888");
    const long = cssVar("--long"), short = cssVar("--short"), fg = cssVar("--fg");

    // легенда: только для ≥2 видимых рядов (у одного ряда подпись — заголовок карточки)
    const loud = o.series.filter((s) => !s.quiet).length;
    legend.replaceChildren(...(loud > 1 ? o.series.map((s, i) => {
      if (s.quiet) return null;
      const k = h("i", { class: `lg-key${s.dash ? " dash" : ""}` });
      k.style.setProperty("--k", colors[i]!);
      return h("span", { class: "lg-item" }, k, s.label);
    }).filter((x): x is HTMLElement => !!x) : []));
    legend.hidden = loud <= 1;

    const fillOf = (s: LineSpec, i: number): number => s.fill ?? (i === 0 && loud <= 2 ? 0.16 : 0);
    const opts: uPlot.Options = {
      // ось времени в UTC — как и все подписи в таблицах (см. ui/fmt.ts)
      tzDate: (ts) => uPlot.tzDate(new Date(ts * 1000), "UTC"),
      width: Math.max(host.clientWidth, 200),
      height: o.height,
      padding: [10, 12, 0, 0],
      cursor: {
        sync: o.sync ? { key: o.sync, setSeries: false } : undefined,
        drag: { x: true, y: false, setScale: true },
        points: {
          size: (_u, si) => (o.series[si - 1]?.quiet ? 0 : 9), width: 2,
          stroke: (_u, si) => (o.series[si - 1]?.quiet ? "transparent" : surface),
          fill: (_u, si) => (o.series[si - 1]?.quiet ? "transparent" : colors[si - 1] ?? fg),
        },
      },
      bands: o.bands?.map((b) => ({ series: [b.hi + 1, b.lo + 1] as [number, number], fill: withAlpha(cssVar(b.color) || "#888888", b.alpha ?? 0.12) })),
      legend: { show: false },
      scales: {
        x: { time: true },
        y: {
          distr: log ? 3 : 1,
          range: (_u, min, max) => {
            if (log) return [Math.max(min * 0.9, 1e-6), max * 1.1];
            const padv = (max - min) * 0.06 || Math.abs(max) * 0.05 || 1;
            return [o.yMin ?? min - padv, o.yMax ?? max + padv];
          },
        },
      },
      axes: [
        { stroke: text, font, grid: { stroke: grid, width: 1 }, ticks: { show: false, size: 0 }, border: { show: false }, gap: 8, size: 30, values: timeTicks },
        {
          stroke: text, font, grid: { stroke: grid, width: 1 }, ticks: { show: false, size: 0 }, border: { show: false }, gap: 8,
          size: (self, values, axisIdx, cycle) => {
            const ax = self.axes[axisIdx]!;
            if (cycle > 1) return (ax as unknown as { _size: number })._size;
            self.ctx.font = font;
            // ширина самой длинной подписи при шрифте в CSS-пикселях (ctx.font задан без масштаба dpr) + зазор
            const w = (values ?? []).reduce((m, v) => Math.max(m, self.ctx.measureText(String(v)).width), 0);
            return Math.ceil(w + 14);
          },
          values: (_u, vals) => vals.map((v) => (v == null ? "" : o.yFmt(v))),
        },
      ],
      series: [
        {},
        ...o.series.map((s, i): uPlot.Series => {
          const a = fillOf(s, i);
          const down = (s.fillMode ?? "down") === "down";
          return {
            label: s.label,
            stroke: s.quiet ? withAlpha(colors[i]!, 0.3) : colors[i],
            width: s.width ?? 1.75,
            dash: s.dash,
            cap: "round",
            fill: a
              ? (self) => {
                const { top, height } = self.bbox;
                const g = self.ctx.createLinearGradient(0, top, 0, top + height);
                g.addColorStop(0, withAlpha(colors[i]!, down ? a : a * 0.15));
                g.addColorStop(1, withAlpha(colors[i]!, down ? 0 : a));
                return g;
              }
              : undefined,
            fillTo: (_self, _si, min) => (o.yMax === 0 ? 0 : min),
            points: { show: false },
            spanGaps: false,
            paths: s.step ? uPlot.paths.stepped!({ align: 1 }) : undefined,
            value: (_u, v) => (v == null ? "—" : (s.fmt ?? o.yFmt)(v)),
          };
        }),
      ],
      hooks: {
        setCursor: [
          (self) => {
            const { idx, left, top } = self.cursor;
            if (idx == null || left == null || top == null || left < 0 || !rawX.length) { tip.hide(); return; }
            const t = self.data[0]![idx];
            if (t == null) { tip.hide(); return; }
            const rows: TipRow[] = [];
            o.series.forEach((s, i) => {
              if (s.quiet) return;
              const v = self.data[i + 1]?.[idx];
              if (v == null) return;
              rows.push({ label: s.label, value: (s.fmt ?? o.yFmt)(v), color: colors[i], dash: !!s.dash });
            });
            if (!rows.length) { tip.hide(); return; }
            const hr = host.getBoundingClientRect(), or = self.over.getBoundingClientRect();
            tip.show({ title: fmtStamp(t), rows, note: o.tipNote?.(idx) }, or.left - hr.left + left, or.top - hr.top + top);
          },
        ],
        draw: [
          (self) => {
            const { ctx } = self;
            const { left, top, width, height } = self.bbox;
            const dpr = devicePixelRatio || 1;
            ctx.save();
            ctx.beginPath();
            ctx.rect(left, top, width, height);
            ctx.clip();
            const r0 = 4.5 * dpr;
            // выбранные сделки рисуем последними (поверх остальных)
            for (const m of [...markers].sort((a, b) => +!!a.active - +!!b.active)) {
              const r = m.active ? r0 * 1.45 : r0;
              const x = self.valToPos(m.t, "x", true);
              const y = self.valToPos(m.p, "y", true);
              if (!isFinite(x) || !isFinite(y) || x < left || x > left + width) continue;
              ctx.beginPath();
              const col = m.side === "long" ? long : short;
              if (m.kind === "entry") {
                // треугольник: вверх — лонг, вниз — шорт
                const d = m.side === "long" ? 1 : -1;
                ctx.moveTo(x, y - d * r * 1.1);
                ctx.lineTo(x - r, y + d * r * 0.8);
                ctx.lineTo(x + r, y + d * r * 0.8);
                ctx.closePath();
                ctx.fillStyle = col;
                ctx.fill();
                ctx.lineWidth = (m.active ? 2 : 1.2) * dpr;
                ctx.strokeStyle = m.active ? fg : surface;
                ctx.stroke();
              } else {
                ctx.arc(x, y, r * 0.7, 0, 2 * Math.PI);
                ctx.fillStyle = surface;
                ctx.fill();
                ctx.lineWidth = (m.active ? 2.4 : 1.6) * dpr;
                ctx.strokeStyle = m.active ? fg : col || fg;
                ctx.stroke();
              }
            }
            ctx.restore();
            // конечная точка первого ряда: точка с кольцом цвета поверхности (не рисуется на нулевом/пустом ряду)
            const xs = self.data[0]!, ys = self.data[1];
            if (ys && loud <= 2) {
              let i = ys.length - 1;
              while (i >= 0 && ys[i] == null) i--;
              if (i >= 0) {
                const x = self.valToPos(xs[i]!, "x", true), y = self.valToPos(ys[i]!, "y", true);
                if (isFinite(x) && isFinite(y) && x >= left && x <= left + width + 1) {
                  ctx.save();
                  ctx.beginPath(); ctx.arc(x, y, 5.5 * dpr, 0, 2 * Math.PI); ctx.fillStyle = surface; ctx.fill();
                  ctx.beginPath(); ctx.arc(x, y, 3.5 * dpr, 0, 2 * Math.PI); ctx.fillStyle = colors[0]!; ctx.fill();
                  ctx.restore();
                }
              }
            }
          },
        ],
      },
    };
    // цвет перекрестия задаёт CSS (.u-cursor-* в styles/charts.css берут токен --crosshair) — из JS он не нужен
    u = new uPlot(opts, current(), host);
    host.querySelector<HTMLElement>(".u-over")?.addEventListener("mouseleave", () => tip.hide());
  };

  const onTheme = () => build();
  window.addEventListener("tb-theme", onTheme);
  const stop = observeWidth(host, (w) => {
    if (u && w > 50) u.setSize({ width: w, height: o.height });
  });
  build();

  return {
    setData(x, ys) {
      rawX = x;
      rawYs = ys;
      tip.hide();
      u?.setData(current());
    },
    setLog(on) {
      if (on === log) return;
      log = on;
      build(); // в лог-режиме неположительные значения отбрасываются в current()
    },
    setMarkers(m) {
      markers = m;
      u?.redraw(false);
    },
    destroy() {
      window.removeEventListener("tb-theme", onTheme);
      stop();
      u?.destroy();
      legend.remove();
      tip.el.remove();
    },
  };
}
