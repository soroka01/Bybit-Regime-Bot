// Гистограмма (SVG): распределение доходностей сделок. Столбцы по корзинам, цвет по знаку корзины (--neg / --pos; корзина, содержащая 0, — нейтральная --ref),
// пунктирная линия нуля, подписи концов корзин по оси X, подсказка по hover/focus. Модель корзин — tabs/statsLogic.ts: histModel (чистая функция).
import { cssVar, h, observeWidth, svg } from "../ui/dom";
import { int, num, pct } from "../ui/fmt";
import { niceScale } from "./scale";
import { chartTip } from "./tooltip";
import type { HistModel } from "../tabs/statsLogic";

const TONE_VAR = { neg: "--neg", pos: "--pos", mixed: "--ref" } as const;
export const TONE_WORD = { neg: "убыток", pos: "прибыль", mixed: "содержит 0" } as const;

export function histogramChart(host: HTMLElement, getModel: () => HistModel | null, o: { height?: number; unit?: string } = {}): { redraw(): void; destroy(): void } {
  let width = 0;
  const tip = chartTip(host);
  const unit = o.unit ?? "%";
  const draw = () => {
    const m = getModel();
    tip.hide();
    if (!m || !m.total) {
      host.replaceChildren(h("p", { class: "muted small", text: "Нет сделок для гистограммы" }));
      return;
    }
    const H = o.height ?? 220, W = Math.max(width, 260);
    const padL = 36, padR = 8, padT = 14, padB = 40;
    const { hi, ticks } = niceScale(0, m.maxCount, 4);
    const x = (v: number) => padL + ((v - m.min) / (m.max - m.min || 1)) * (W - padL - padR);
    const y = (c: number) => padT + ((hi - c) / (hi || 1)) * (H - padT - padB);
    const grid = cssVar("--grid"), muted = cssVar("--fg-muted"), axis = cssVar("--axis"), fg = cssVar("--fg");
    const root = svg("svg", { class: "hist", viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": "Гистограмма доходностей сделок" });
    root.style.fontFamily = cssVar("--font-ui");
    for (const t of ticks) {
      if (!Number.isInteger(t)) continue;
      root.append(svg("line", { x1: padL, x2: W - padR, y1: y(t), y2: y(t), stroke: grid }),
        Object.assign(svg("text", { x: padL - 8, y: y(t) + 4, "text-anchor": "end", fill: muted, "font-size": 11 }), { textContent: String(t) }));
    }
    root.append(svg("line", { x1: padL, x2: W - padR, y1: y(0), y2: y(0), stroke: axis }));
    const bw = (W - padL - padR) / m.bins.length;
    m.bins.forEach((b) => {
      const col = cssVar(TONE_VAR[b.tone]);
      const x0 = x(b.lo) + 1, w = Math.max(2, x(b.hi) - x(b.lo) - 2);
      const top = y(b.count), hgt = Math.max(b.count > 0 ? 1.5 : 0, y(0) - top);
      const label = `${b.label}: ${int(b.count)} сделок (${pct(b.share, 0)}), ${TONE_WORD[b.tone]}`;
      const g = svg("g", { class: "hbar", tabindex: b.count ? 0 : -1, role: "img", "aria-label": label });
      g.append(svg("rect", { x: x0, y: 0, width: w, height: H - padB, fill: "transparent" }));      // широкая зона наведения
      if (b.count) g.append(svg("path", { d: roundTop(x0, top, w, hgt, Math.min(4, w / 2)), fill: col }));
      const show = () => {
        const hr = host.getBoundingClientRect(), r = g.getBoundingClientRect();
        tip.show({ title: b.label, rows: [{ label: "Сделок", value: `${int(b.count)} · ${pct(b.share, 0)}`, color: col, box: true }], note: TONE_WORD[b.tone] }, r.left - hr.left + r.width / 2, r.top - hr.top + Math.min(r.height / 2, 40));
      };
      g.addEventListener("pointermove", show);
      g.addEventListener("focus", show);
      g.addEventListener("pointerleave", () => tip.hide());
      g.addEventListener("blur", () => tip.hide());
      root.append(g);
    });
    // линия нуля
    if (m.zeroPos != null) {
      const zx = x(0);
      root.append(svg("line", { x1: zx, x2: zx, y1: padT - 4, y2: H - padB, stroke: fg, "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0.6 }),
        Object.assign(svg("text", { x: zx, y: padT - 5, "text-anchor": "middle", fill: muted, "font-size": 10 }), { textContent: "0" }));
    }
    // подписи по оси X: ~каждые 60 px, концы корзин
    const every = Math.max(1, Math.ceil(m.bins.length / Math.max(1, Math.floor((W - padL - padR) / 62))));
    const edges = [m.bins[0]!.lo, ...m.bins.map((b) => b.hi)];
    const span = m.max - m.min, d = span >= 10 ? 0 : span >= 1 ? 1 : 2;
    edges.forEach((e, i) => {
      if (i % every !== 0 && i !== edges.length - 1) return;
      if (i !== edges.length - 1 && edges.length - 1 - i < every * 0.6) return;     // не лепим вплотную к последней подписи
      const anchor = i === 0 ? "start" : i === edges.length - 1 ? "end" : "middle";     // крайние подписи не вылезают за SVG
      root.append(Object.assign(svg("text", { x: x(e), y: H - padB + 16, "text-anchor": anchor, fill: muted, "font-size": 11 }), { textContent: num(e, d) }));
    });
    root.append(Object.assign(svg("text", { x: (padL + W - padR) / 2, y: H - 6, "text-anchor": "middle", fill: muted, "font-size": 11 }), { textContent: `доходность сделки, ${unit}` }));
    void bw;
    host.replaceChildren(root, tip.el);
  };
  const stop = observeWidth(host, (w) => { if (w > 0 && Math.abs(w - width) > 1) { width = w; draw(); } });
  const onTheme = () => draw();
  window.addEventListener("tb-theme", onTheme);
  width = host.clientWidth;
  draw();
  return { redraw: draw, destroy: () => { stop(); window.removeEventListener("tb-theme", onTheme); } };
}

/** Столбец, скруглённый только сверху (рабочий конец), основание — у нулевой линии. */
function roundTop(x: number, y: number, w: number, hgt: number, r: number): string {
  const rr = Math.max(0, Math.min(r, w / 2, hgt));
  if (rr < 0.5) return `M${x},${y}h${w}v${hgt}h${-w}Z`;
  return `M${x},${y + hgt}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + hgt}Z`;
}
