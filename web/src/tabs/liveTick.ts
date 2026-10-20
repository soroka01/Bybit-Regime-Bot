// Вкладка «Бот (live)»: «живые» элементы — обратный отсчёт и «N с назад» (data-tk), которые тикер обновляет раз в секунду (чистые построители DOM).
import { h } from "../ui/dom";
import { ago } from "../ui/fmt";
import { fmtDuration } from "../ui/fmtTime";
import { fmtCountdown } from "./liveCalc";

type TkKind = "cd" | "age" | "dur";

/** Текст «живого» элемента на момент now: cd — обратный отсчёт до метки (мс), age — «N с назад» от ISO-времени, dur — длительность с метки (мс) до now. */
const tkText = (kind: string | undefined, v: string, now: number): string =>
  kind === "cd" ? fmtCountdown(+v - now) : kind === "dur" ? fmtDuration((now - +v) / 1000) : ago(v);

/**
 * Элемент, который тикер обновляет раз в секунду. Любой текст, зависящий от текущего времени («N мин назад», отсчёт, «держится …»), делается так:
 * тогда он не требует перерисовки секции, а подпись секции (sectionSig) его игнорирует.
 */
export const tk = (kind: TkKind, value: number | string, el: HTMLElement = h("span")): HTMLElement => {
  el.dataset.tk = kind;
  el.dataset.v = String(value);
  el.textContent = tkText(kind, String(value), Date.now());
  return el;
};

export function tickAll(root: ParentNode, now = Date.now()): void {
  root.querySelectorAll<HTMLElement>("[data-tk]").forEach((el) => {
    el.textContent = tkText(el.dataset.tk, el.dataset.v ?? "", now);
  });
}
