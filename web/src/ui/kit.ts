// Небольшие фабрики UI-примитивов (стили — styles/components.css). Подробный перечень — web/DESIGN.md.
import { h } from "./dom";
import { icon } from "./icons";

export type Tone = "ok" | "warn" | "bad" | "info" | "off";

/** Глиф направления: ▲ плюс, ▼ минус (рядом со знаком числа; цвет никогда не единственный носитель смысла). */
export const glyph = (tone: string): string => (tone === "pos" ? "▲" : tone === "neg" ? "▼" : "");

/** Бейдж со статусной точкой: ok / warn / bad / info / off. glyph — необязательный знак вместо точки (✓ ! ✕ …). */
export function badge(text: string, tone: Tone = "off", opts: { title?: string; glyph?: string; live?: boolean } = {}): HTMLElement {
  return h("span", { class: `badge ${tone}${opts.glyph ? " has-glyph" : ""}${opts.live ? " live" : ""}`, title: opts.title },
    opts.glyph ? h("span", { class: "g", "aria-hidden": "true", text: opts.glyph }) : null, text);
}

/** Чип с ▲/▼ для дельты (положительная — pos, отрицательная — neg, иначе нейтральный). */
export function deltaChip(tone: string, text?: string): HTMLElement {
  return h("span", { class: `delta ${tone}` }, tone ? h("span", { class: "g", "aria-hidden": "true", text: glyph(tone) }) : null, text ?? null);
}

export interface TileOpts {
  label: string;
  value: string;
  /** "pos" | "neg" | "" — окрашивает значение и добавляет чип ▲/▼ (если delta !== false). */
  tone?: string;
  sub?: string;
  hint?: string;
  /** Показывать чип ▲/▼ у подписи (по умолчанию да, если задан tone). */
  delta?: boolean;
  big?: boolean;
}

/** Плитка метрики: подпись, крупное значение, чип-дельта, строка-пояснение. */
export function metricTile(o: TileOpts): HTMLElement {
  const tone = o.tone ?? "";
  return h("div", { class: `metric${o.big ? " big" : ""}`, title: o.hint ?? "" },
    h("div", { class: "metric-top" }, h("div", { class: "metric-label", text: o.label }), tone && o.delta !== false ? deltaChip(tone) : null),
    h("div", { class: `metric-value ${tone}`, text: o.value }),
    o.sub ? h("div", { class: "metric-sub", text: o.sub }) : null);
}

/** Заголовок страницы: title + подзаголовок слева, действия справа. */
export function pageHead(title: string, sub?: string, actions: (HTMLElement | null)[] = []): HTMLElement {
  return h("div", { class: "page-head" },
    h("div", { class: "page-head-text" }, h("h2", { class: "page-title", text: title }), sub ? h("p", { class: "page-sub", text: sub }) : null),
    actions.some(Boolean) ? h("div", { class: "page-actions" }, ...actions) : null);
}

export const sectionLabel = (text: string): HTMLElement => h("div", { class: "section-label", text });
export const kbd = (key: string, decorative = false): HTMLElement => h("kbd", { class: "kbd", text: key, "aria-hidden": decorative ? "true" : undefined });

/** Кнопка в состоянии загрузки: спиннер вместо содержимого, ширина сохраняется. */
export function setLoading(btn: HTMLButtonElement, on: boolean): void {
  btn.classList.toggle("is-loading", on);
  btn.disabled = on;
  if (on) btn.setAttribute("aria-busy", "true"); else btn.removeAttribute("aria-busy");
}

/** Скелетон-плитка метрики / блок графика (aria-hidden: декоративные заглушки на время загрузки). */
export const skeletonTile = (): HTMLElement => h("div", { class: "skeleton skel-tile", "aria-hidden": "true" });
export const skeletonChart = (height = 280): HTMLElement => h("div", { class: "skeleton skel-chart", "aria-hidden": "true", style: `--h:${height}px` });
export const skeletonLines = (n = 3): HTMLElement =>
  h("div", { "aria-hidden": "true", style: "display:flex;flex-direction:column;gap:8px" },
    ...Array.from({ length: n }, (_, i) => h("div", { class: `skeleton skel-line ${["w80", "w60", "w40"][i % 3]}` })));

/** Пустое состояние: иконка, строка-подсказка, необязательное действие. */
export function emptyState(title: string, hint?: string, action?: HTMLElement | null, opts: { big?: boolean; icon?: string } = {}): HTMLElement {
  return h("div", { class: `empty-state${opts.big ? " big" : ""}` },
    h("div", { class: "empty-icon", "aria-hidden": "true" }, icon(opts.icon ?? "inbox", 22)),
    h("p", { class: "strong", text: title }),
    hint ? h("p", { text: hint }) : null,
    action ?? null);
}

/** Индикатор прогресса (определённый: 0..1; без аргумента — неопределённый). */
export function progressBar(fraction?: number): HTMLElement {
  const fill = h("div", { class: "progress-fill", style: fraction == null ? "" : `width:${Math.round(fraction * 100)}%` });
  return h("div", { class: `progress${fraction == null ? " indet" : ""}`, role: "progressbar" }, fill);
}

/**
 * Шкала лимита (role="meter"): заливка до value/max, цвет по tone (ok/warn/bad) и ТЕКСТОВАЯ метка состояния (смысл не держится на цвете),
 * необязательные риски-пороги (marks, доли 0…1). Стили — styles/components.css (.meter).
 */
export function meterBar(o: { label: string; value: number; max: number; tone?: "ok" | "warn" | "bad"; valueText: string; marks?: number[] }): HTMLElement {
  const frac = o.max > 0 ? Math.max(0, Math.min(1, o.value / o.max)) : 0;
  const el = h("div", {
    class: `meter ${o.tone ?? "ok"}`, role: "meter", "aria-label": o.label, "aria-valuemin": 0, "aria-valuemax": o.max,
    "aria-valuenow": Math.min(o.max, Math.max(0, o.value)), "aria-valuetext": o.valueText,
  }, h("div", { class: "meter-fill", style: `width:${(frac * 100).toFixed(1)}%` }), ...(o.marks ?? []).map((m) => h("i", { class: "meter-mark", "aria-hidden": "true", style: `left:${(m * 100).toFixed(1)}%` })));
  return el;
}

/**
 * Сворачиваемый блок на <details>. С key состояние (открыт/закрыт) запоминается в localStorage; первый визит — defaultOpen.
 * Стили — styles/components.css (.disclosure).
 */
export function disclosure(title: string, body: (Node | string)[], opts: { key?: string; defaultOpen?: boolean; badge?: string } = {}): HTMLDetailsElement {
  const k = opts.key ? `tb.disc.${opts.key}` : "";
  let open = opts.defaultOpen ?? false;
  if (k) {
    try { const v = localStorage.getItem(k); if (v === "1" || v === "0") open = v === "1"; } catch { /* приватный режим */ }
  }
  const d = h("details", { class: "disclosure", open }, h("summary", null, h("span", { class: "disc-title", text: title }), opts.badge ? h("span", { class: "disc-badge", text: opts.badge }) : null), h("div", { class: "disc-body" }, ...body));
  if (k) d.addEventListener("toggle", () => { try { localStorage.setItem(k, d.open ? "1" : "0"); } catch { /* ignore */ } });
  return d;
}
