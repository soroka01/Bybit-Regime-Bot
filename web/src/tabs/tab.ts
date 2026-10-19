import type { Meta } from "../types";
import { h } from "../ui/dom";

export interface Tab {
  el: HTMLElement;
  /** Вкладка стала видимой (запустить обновления). */
  show(): void;
  /** Вкладка скрыта (остановить таймеры/запросы). */
  hide(): void;
  /** Какие данные использует вкладка (для полосы свежести); null — полоса скрыта. */
  dataContext?(): DataCtx | null;
  /** Данные обновились — пересчитать текущий результат. */
  refresh?(): void;
}

export interface DataCtx { symbol: string; tfs: string[] }

/** Вкладка сообщает, что сменился символ/таймфрейм (полоса свежести перечитает статус). */
export const notifyContext = () => window.dispatchEvent(new Event("tb-context"));

export type TabFactory = (meta: Meta) => Tab;

/**
 * Раскладка «панель параметров + результат». head — заголовок страницы над раскладкой; bar — «липкая» панель действий внизу панели
 * (на широком экране панель прокручивается над ней, на узком панель встаёт над результатом, а bar прилипает к низу окна, пока видна панель).
 */
export function panelLayout(panel: HTMLElement[], main: HTMLElement[], opts: { head?: HTMLElement; bar?: HTMLElement } = {}): { el: HTMLElement; panel: HTMLElement; main: HTMLElement } {
  const p = opts.bar
    ? h("aside", { class: "panel has-bar", "aria-label": "Параметры" }, h("div", { class: "panel-body" }, ...panel), opts.bar)
    : h("aside", { class: "panel", "aria-label": "Параметры" }, ...panel);
  const m = h("div", { class: "main" }, ...main);
  const layout = h("div", { class: "layout" }, p, m);
  return { el: opts.head ? h("div", { class: "page" }, opts.head, layout) : layout, panel: p, main: m };
}

/** Предел истории таймфрейма (дней) — из /api/meta, единый с сервером. */
export const capFor = (meta: Meta, tf: string): number => meta.history_days[tf] ?? 1095;
export const capReason = (meta: Meta, tf: string, days: number): string | null =>
  days > capFor(meta, tf) ? `для ${tf} доступно до ${capFor(meta, tf)} дн.` : null;
export const DAY_OPTIONS = [
  { value: "90", label: "90 д" },
  { value: "180", label: "180 д" },
  { value: "365", label: "1 год" },
  { value: "730", label: "2 года" },
  { value: "1095", label: "3 года" },
];
export const DAY_VALUES: readonly number[] = DAY_OPTIONS.map((o) => +o.value);
/** Периоды, которые предлагает «Лучшие стратегии» (OOS-окно 33% должно быть содержательным). */
export const LB_DAYS: readonly number[] = [180, 365, 730, 1095];

/**
 * Ближайший допустимый период не больше предела истории таймфрейма. Берётся только из allowed (чтобы select/сегменты
 * никогда не получали значение вне своих вариантов); если ни один вариант не помещается в предел — наименьший из них.
 */
export const fitDays = (meta: Meta, tf: string, days: number, allowed: readonly number[] = DAY_VALUES): number => {
  const cap = capFor(meta, tf);
  const ok = allowed.filter((d) => d <= cap);
  if (!ok.length) return Math.min(...allowed);
  if (days <= cap && allowed.includes(days)) return days;
  const below = ok.filter((d) => d <= days);
  return below.length ? below[below.length - 1]! : ok[0]!;
};
