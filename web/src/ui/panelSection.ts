// Секция боковой панели с иконкой и однострочной сводкой, видимой в свёрнутом виде. Состояние (открыта/свёрнута) — в localStorage["tb.disc.<key>"].
import { h } from "./dom";
import { icon } from "./icons";

export interface PanelSection {
  el: HTMLDetailsElement;
  setSummary(text: string): void;
}

export function panelSection(o: { key: string; icon: string; title: string; summary: string; body: (Node | null)[]; open?: boolean }): PanelSection {
  const k = `tb.disc.${o.key}`;
  let open = o.open ?? false;
  try { const v = localStorage.getItem(k); if (v === "1" || v === "0") open = v === "1"; } catch { /* приватный режим */ }
  const sum = h("span", { class: "ps-sum", text: o.summary });
  const el = h("details", { class: "panel-section ps", open },
    h("summary", null, h("span", { class: "ps-ico", "aria-hidden": "true" }, icon(o.icon, 15)), h("span", { class: "ps-title", text: o.title }), sum),
    h("div", { class: "ps-body" }, ...o.body));
  el.addEventListener("toggle", () => { try { localStorage.setItem(k, el.open ? "1" : "0"); } catch { /* ignore */ } });
  return { el, setSummary: (t) => { sum.textContent = t; sum.title = t; } };
}
