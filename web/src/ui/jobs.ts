// Глобальный индикатор «идёт загрузка данных…» (счётчик выполняющихся fetch/update в api.ts).
import { dataJobsActive, onDataJobs } from "../api";
import { h } from "./dom";

export function jobsIndicator(): HTMLElement {
  const el = h("span", { class: "jobs muted small", role: "status", "aria-live": "polite", hidden: true },
    h("span", { class: "spinner", "aria-hidden": "true" }), h("span", { text: "идёт загрузка данных…" }));
  const paint = () => { el.hidden = !dataJobsActive(); };
  onDataJobs(paint);
  paint();
  return el;
}
