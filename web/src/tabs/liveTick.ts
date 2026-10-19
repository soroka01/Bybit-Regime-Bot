// Вкладка «Бот (live)»: «живые» элементы — обратный отсчёт и «N с назад» (data-tk), которые тикер обновляет раз в секунду (чистые построители DOM).
import { h } from "../ui/dom";
import { ago } from "../ui/fmt";
import { fmtCountdown } from "./liveCalc";

/** Элемент, который тикер обновляет раз в секунду: cd — обратный отсчёт до метки времени (мс), age — «N с назад» от ISO-времени. */
export const tk = (kind: "cd" | "age", value: number | string, el: HTMLElement = h("span")): HTMLElement => {
  el.dataset.tk = kind;
  el.dataset.v = String(value);
  el.textContent = kind === "cd" ? fmtCountdown(+value - Date.now()) : ago(String(value));
  return el;
};

export function tickAll(root: ParentNode, now = Date.now()): void {
  root.querySelectorAll<HTMLElement>("[data-tk]").forEach((el) => {
    const v = el.dataset.v ?? "";
    el.textContent = el.dataset.tk === "cd" ? fmtCountdown(+v - now) : ago(v);
  });
}
