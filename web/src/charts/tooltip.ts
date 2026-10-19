// Плавающая карточка подсказки для всех графиков (линии, столбцы, полосы, тепловые карты, профиль параметров).
// Значения ведут, подписи вторичны; ключ ряда — короткая линия цвета ряда. Весь текст — через textContent (подписи рядов — недоверенные данные).
import { h } from "../ui/dom";

export interface TipRow {
  label: string;
  value: string;
  /** Цвет ключа (CSS-цвет). */
  color?: string;
  dash?: boolean;
  /** Ключ-квадрат вместо линии (столбцы, ячейки). */
  box?: boolean;
  /** "pos" | "neg" — окрашивает значение. */
  tone?: string;
}
export interface TipData { title?: string; rows: TipRow[]; note?: string }

export interface ChartTip {
  el: HTMLElement;
  /** Показать карточку у точки (x, y) в координатах host. */
  show(d: TipData, x: number, y: number): void;
  hide(): void;
}

/** host должен быть position:relative (.chart / .heat-wrap / .strips-wrap) — карточка позиционируется внутри него. */
export function chartTip(host: HTMLElement): ChartTip {
  const el = h("div", { class: "chart-tip", role: "presentation" });
  let key = "";
  const build = (d: TipData) => {
    el.replaceChildren(
      ...(d.title ? [h("div", { class: "tt-title", text: d.title })] : []),
      ...d.rows.map((r) => {
        const k = h("i", { class: `tt-key${r.dash ? " dash" : ""}${r.box ? " box" : ""}` });
        if (r.color) k.style.setProperty("--k", r.color);
        return h("div", { class: "tt-row" }, k, h("span", { class: "tt-label", text: r.label }), h("span", { class: `tt-val ${r.tone ?? ""}`, text: r.value }));
      }),
      ...(d.note ? [h("div", { class: "tt-note", text: d.note })] : []));
  };
  return {
    el,
    show(d, x, y) {
      if (!el.isConnected) host.append(el);
      const k = JSON.stringify(d);
      if (k !== key) { key = k; build(d); }
      const hw = host.clientWidth, hh = host.clientHeight;
      const w = el.offsetWidth, ht = el.offsetHeight;
      let left = x + 16;
      if (left + w > hw - 4) left = x - 16 - w;           // у правого края — слева от курсора
      left = Math.max(4, Math.min(left, Math.max(4, hw - w - 4)));
      const top = Math.max(4, Math.min(y - ht / 2, Math.max(4, hh - ht - 4)));
      el.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
      el.classList.add("on");
    },
    hide() { el.classList.remove("on"); },
  };
}
