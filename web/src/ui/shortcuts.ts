// Клавиатура: 1…N — вкладки, ? — справка по клавишам (popover), Esc — закрыть. Не срабатывает при вводе текста и с модификаторами.
import { t } from "../i18n";
import { currentRoute, ROUTES, setRoute } from "../state";
import { h } from "./dom";
import { icon } from "./icons";
import { kbd } from "./kit";
import { PALETTE_KEYS } from "./palette";

const isTyping = (t: EventTarget | null): boolean => {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
};

export function installShortcuts(titles: Record<string, string>): { button: HTMLButtonElement } {
  let pop: HTMLElement | null = null;
  let opener: HTMLElement | null = null;

  const close = (restore = true) => {
    pop?.remove();
    pop = null;
    if (restore && opener?.isConnected) opener.focus({ preventScroll: true });
    button.setAttribute("aria-expanded", "false");
  };
  const open = () => {
    if (pop) return close();
    opener = document.activeElement instanceof HTMLElement ? document.activeElement : button;
    const row = (label: string, ...keys: string[]) => h("li", null, h("span", { text: label }), h("span", { class: "keys" }, ...keys.map((k) => kbd(k))));
    pop = h("div", { class: "popover", role: "dialog", "aria-label": t("common.keys.title"), tabindex: -1 },
      h("h3", { text: t("common.keys.title") }),
      h("ul", { class: "shortcuts" },
        ...ROUTES.map((r, i) => row(titles[r] ?? r, String(i + 1))),
        row(t("common.keys.prev_next"), "←", "→"),
        row(t("common.keys.palette"), ...PALETTE_KEYS),
        row(t("common.keys.palette_slash"), "/"),
        row(t("common.keys.help"), "?"),
        row(t("common.keys.close"), "Esc")));
    document.body.append(pop);
    pop.focus({ preventScroll: true });
    button.setAttribute("aria-expanded", "true");
  };

  const button = h("button", {
    class: "btn ghost icon-btn", type: "button", "aria-label": t("common.keys.btn_aria"), "data-tip": t("common.keys.btn_tip"), "data-tip-pos": "left",
    "aria-haspopup": "dialog", "aria-expanded": "false", onClick: open,
  }, icon("keyboard", 16));

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && pop) { e.preventDefault(); close(); return; }
    if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target) || document.querySelector("dialog[open]")) return;
    if (e.key === "?") { e.preventDefault(); open(); return; }
    if (/^[1-9]$/.test(e.key)) {
      const r = ROUTES[+e.key - 1];
      if (r && r !== currentRoute()) { e.preventDefault(); setRoute(r); }
    }
  });
  document.addEventListener("pointerdown", (e) => {
    if (pop && !pop.contains(e.target as Node) && !button.contains(e.target as Node)) close(false);
  });
  return { button };
}
