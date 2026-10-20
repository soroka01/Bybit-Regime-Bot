// Полосы сигналов/экспозиции по ногам портфеля (canvas): «плюс» — --pos (лонг), «минус» — --neg (шорт), интенсивность = |значение|.
// Дорожки скруглены, подпись ряда — текстовым токеном; при наведении — плавающая подсказка и тонкая вертикаль.
// Основной слой рисуется только при изменении данных/размера/темы, вертикаль наведения — на отдельном прозрачном слое (mousemove не перерисовывает полосы).
import type { Num } from "../types";
import { cssVar, h, observeWidth } from "../ui/dom";
import { date, num } from "../ui/fmt";
import { chartTip } from "./tooltip";

interface StripRow {
  label: string;
  values: Num[];
  max: number; // |значение| для полной насыщенности
}

/** Токены цвета и шрифта: читаются один раз на перерисовку (getComputedStyle не вызывается в циклах и на mousemove). */
function readPalette() {
  return {
    pos: cssVar("--pos"), neg: cssVar("--neg"), track: cssVar("--surface-2"), text: cssVar("--fg-muted"), fg: cssVar("--fg"), cross: cssVar("--crosshair"),
    font: cssVar("--font-ui") || "system-ui, sans-serif",
  };
}

export function signalStrips(host: HTMLElement) {
  let t: number[] = [];
  let rows: StripRow[] = [];
  let hoverX = -1;
  let pal = readPalette();
  let boxW = 0, boxH = 0;
  const ROW = 22, GAP = 6;
  let LABEL = 132;
  host.classList.add("strips-wrap");
  const canvas = h("canvas", { class: "strips", role: "img", "aria-label": "Сигналы и экспозиция по ногам портфеля" });
  const overlay = h("canvas", { class: "strips-overlay", "aria-hidden": "true" });
  const stage = h("div", { class: "strips-stage" }, canvas, overlay);
  const readout = h("div", { class: "muted small", "aria-live": "off", text: "Наведите курсор на полосу" });
  const tip = chartTip(host);
  host.append(stage, readout);

  const rr = (ctx: CanvasRenderingContext2D, x: number, y: number, w: number, hh: number, r: number) => {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + hh, r); ctx.arcTo(x + w, y + hh, x, y + hh, r);
    ctx.arcTo(x, y + hh, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  };

  /** Вертикаль наведения — отдельный слой: очистить и провести одну линию. */
  const drawOverlay = () => {
    const ctx = overlay.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    if (hoverX >= LABEL && hoverX <= boxW) {
      const dpr = devicePixelRatio || 1;
      ctx.scale(dpr, dpr);
      ctx.fillStyle = pal.cross;
      ctx.fillRect(Math.round(hoverX) - 0.5, 0, 1, boxH);
    }
  };

  const draw = () => {
    const w = host.clientWidth;
    if (w < 50) return;
    pal = readPalette();
    const dpr = devicePixelRatio || 1;
    const H = Math.max(rows.length * (ROW + GAP) - GAP, 1);
    boxW = w; boxH = H;
    for (const c of [canvas, overlay]) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(H * dpr);
      c.style.width = w + "px";
      c.style.height = H + "px";
    }
    const ctx = canvas.getContext("2d")!;
    ctx.scale(dpr, dpr);
    const { pos, neg, track, text, fg, font } = pal;
    ctx.font = `12px ${font}`;
    ctx.textBaseline = "middle";
    const n = t.length;
    LABEL = Math.round(Math.min(150, Math.max(78, w * 0.24)));
    const plotW = w - LABEL;
    rows.forEach((r, k) => {
      const y = k * (ROW + GAP);
      ctx.fillStyle = r.label === "Итого" ? fg : text;
      ctx.font = `${r.label === "Итого" ? "600 " : ""}12px ${font}`;
      let lab = r.label;
      while (lab.length > 3 && ctx.measureText(lab).width > LABEL - 10) lab = lab.slice(0, -2) + "…";
      ctx.fillText(lab, 0, y + ROW / 2);
      ctx.save();
      rr(ctx, LABEL, y, plotW, ROW, 6);
      ctx.fillStyle = track;
      ctx.fill();
      ctx.clip();
      const cw = plotW / Math.max(n, 1);
      for (let i = 0; i < n; i++) {
        const v = r.values[i];
        if (v == null || Math.abs(v) < 0.02) continue;
        ctx.globalAlpha = Math.min(1, Math.abs(v) / r.max) * 0.9 + 0.1;
        ctx.fillStyle = v > 0 ? pos : neg;
        ctx.fillRect(LABEL + i * cw, y, Math.ceil(cw) + 0.5, ROW);
      }
      ctx.restore();
    });
    drawOverlay();
  };

  const leave = () => { hoverX = -1; tip.hide(); readout.textContent = "Наведите курсор на полосу"; drawOverlay(); };
  canvas.addEventListener("mouseleave", leave);
  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left - LABEL;
    const k = Math.floor((e.clientY - rect.top) / (ROW + GAP));
    const w = rect.width - LABEL;
    if (x < 0 || x > w || !rows[k] || !t.length) { leave(); return; }
    const i = Math.min(t.length - 1, Math.floor((x / w) * t.length));
    const row = rows[k]!;
    const v = row.values[i];
    hoverX = e.clientX - rect.left;
    readout.textContent = `${row.label} · ${date(t[i])}: ${num(v, 2)}`;
    drawOverlay();
    const hr = host.getBoundingClientRect();
    tip.show({
      title: date(t[i]),
      rows: rows.map((r) => {
        const val = r.values[i];
        return { label: r.label, value: num(val, 2), color: val == null || val === 0 ? undefined : val > 0 ? pal.pos : pal.neg, box: true };
      }),
    }, e.clientX - hr.left, e.clientY - hr.top);
  });

  const stop = observeWidth(host, draw);
  window.addEventListener("tb-theme", draw);
  return {
    set(tt: number[], rr_: StripRow[]) { t = tt; rows = rr_; draw(); },
    destroy() { stop(); window.removeEventListener("tb-theme", draw); },
  };
}
