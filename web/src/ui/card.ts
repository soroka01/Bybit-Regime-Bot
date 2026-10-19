import { h } from "./dom";

/** Карточка-секция с заголовком; `extra` — элементы справа в шапке. */
export const card = (title: string, body: (HTMLElement | null)[], extra: HTMLElement[] = []): HTMLElement =>
  h("section", { class: "card" }, h("header", { class: "card-head" }, h("h3", { text: title }), ...extra), ...body);
