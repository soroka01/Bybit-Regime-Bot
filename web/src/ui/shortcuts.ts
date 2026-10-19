// Клавиатура: 1…N — вкладки, ? — справка по клавишам (popover), Esc — закрыть. Не срабатывает при вводе текста и с модификаторами.
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

export function installShortcuts(titles: Record<string, string>, onSwitched?: (route: string) => void): { button: HTMLButtonElement } {
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
    pop = h("div", { class: "popover", role: "dialog", "aria-label": "Горячие клавиши", tabindex: -1 },
      h("h3", { text: "Горячие клавиши" }),
      h("ul", { class: "shortcuts" },
        ...ROUTES.map((r, i) => row(titles[r] ?? r, String(i + 1))),
        row("Предыдущая / следующая вкладка (фокус на вкладках)", "←", "→"),
        row("Командная палитра: вкладки, стратегии, тема", ...PALETTE_KEYS),
        row("То же, когда фокус не в поле ввода", "/"),
        row("Эта справка", "?"),
        row("Закрыть окно или справку", "Esc")));
    document.body.append(pop);
    pop.focus({ preventScroll: true });
    button.setAttribute("aria-expanded", "true");
  };

  const button = h("button", {
    class: "btn ghost icon-btn", type: "button", "aria-label": "Горячие клавиши (?)", "data-tip": "Горячие клавиши · ?", "data-tip-pos": "left",
    "aria-haspopup": "dialog", "aria-expanded": "false", onClick: open,
  }, icon("keyboard", 16));

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && pop) { e.preventDefault(); close(); return; }
    if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target) || document.querySelector("dialog[open]")) return;
    if (e.key === "?") { e.preventDefault(); open(); return; }
    if (/^[1-9]$/.test(e.key)) {
      const r = ROUTES[+e.key - 1];
      if (r && r !== currentRoute()) { e.preventDefault(); setRoute(r); onSwitched?.(r); }
    }
  });
  document.addEventListener("pointerdown", (e) => {
    if (pop && !pop.contains(e.target as Node) && !button.contains(e.target as Node)) close(false);
  });
  return { button };
}
