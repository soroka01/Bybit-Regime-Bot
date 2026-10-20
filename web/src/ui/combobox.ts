// Поле выбора с поиском (ARIA 1.2 «combobox» со списком): набор текста фильтрует, ↑/↓/Home/End двигают подсветку, Enter выбирает, Esc закрывает.
// Список — позиционированный блок под полем (position: absolute), без внешних зависимостей. Стили — styles/tab-lab.css (.combo).
import type { Control } from "./controls";
import { h } from "./dom";
import { badge } from "./kit";

interface ComboItem { value: string; label: string; desc?: string; tag?: string }

interface ComboOpts {
  label: string;
  items: readonly ComboItem[];
  value: string;
  /** Фильтр по запросу (по умолчанию — подстрока в названии). */
  filter?(items: readonly ComboItem[], query: string): ComboItem[];
  placeholder?: string;
  onChange(v: string): void;
}

let uid = 0;

export function combobox(o: ComboOpts): Control<string> {
  const n = ++uid;
  const inputId = `cb${n}`, listId = `cbl${n}`;
  let value = o.value;
  let open = false;
  let active = -1;
  let shown: ComboItem[] = [];
  const find = (v: string) => o.items.find((i) => i.value === v);
  const filt = o.filter ?? ((items, q) => items.filter((i) => i.label.toLowerCase().includes(q.trim().toLowerCase())));

  const input = h("input", {
    type: "text", id: inputId, class: "combo-input", role: "combobox", autocomplete: "off", spellcheck: false,
    "aria-autocomplete": "list", "aria-expanded": "false", "aria-controls": listId, "aria-haspopup": "listbox",
    placeholder: o.placeholder ?? "Найти стратегию…",
  });
  input.setAttribute("autocapitalize", "off");
  const list = h("ul", { class: "combo-list", id: listId, role: "listbox", "aria-label": o.label, hidden: true });
  const caret = h("span", { class: "combo-caret", "aria-hidden": "true" });
  const wrap = h("div", { class: "combo" }, input, caret, list);
  const el = h("div", { class: "field" }, h("div", { class: "field-head" }, h("label", { for: inputId, text: o.label })), wrap);

  const text = () => find(value)?.label ?? "";
  const optId = (i: number) => `${listId}-${i}`;

  function paint() {
    if (!shown.length) {
      list.replaceChildren(h("li", { class: "combo-empty", role: "presentation", text: "Ничего не найдено" }));
      input.removeAttribute("aria-activedescendant");
      return;
    }
    list.replaceChildren(...shown.map((it, i) => h("li", {
      class: `combo-opt${i === active ? " is-active" : ""}${it.value === value ? " is-current" : ""}`, role: "option", id: optId(i),
      "aria-selected": String(it.value === value),
      onMousedown: (e: Event) => { e.preventDefault(); choose(it); },
      onMousemove: () => { if (active !== i) setActive(i, false); },
    },
    h("span", { class: "co-top" }, h("span", { class: "co-name", text: it.label }), it.tag ? badge(it.tag, "info") : null, it.value === value ? h("span", { class: "co-check", "aria-hidden": "true", text: "✓" }) : null),
    it.desc ? h("span", { class: "co-desc", text: it.desc }) : null)));
    if (active >= 0) input.setAttribute("aria-activedescendant", optId(active)); else input.removeAttribute("aria-activedescendant");
  }

  function setActive(i: number, scroll = true) {
    active = i;
    [...list.children].forEach((c, k) => c.classList.toggle("is-active", k === i));
    if (i >= 0) {
      input.setAttribute("aria-activedescendant", optId(i));
      if (scroll) (list.children[i] as HTMLElement | undefined)?.scrollIntoView?.({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
  }

  function openList(query = "") {
    shown = filt(o.items, query);
    const cur = shown.findIndex((i) => i.value === value);
    active = cur >= 0 ? cur : shown.length ? 0 : -1;
    open = true;
    list.hidden = false;
    wrap.classList.add("open");
    input.setAttribute("aria-expanded", "true");
    paint();
    if (active >= 0) (list.children[active] as HTMLElement | undefined)?.scrollIntoView?.({ block: "nearest" });
  }
  function closeList(restore = true) {
    open = false;
    list.hidden = true;
    wrap.classList.remove("open");
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    if (restore) input.value = text();
  }
  function choose(it: ComboItem) {
    const changed = it.value !== value;
    value = it.value;
    input.value = text();
    closeList(false);
    if (changed) o.onChange(value);
  }

  input.addEventListener("focus", () => { input.select(); if (!open) openList(); });
  input.addEventListener("click", () => { if (!open) openList(); });
  input.addEventListener("input", () => openList(input.value));
  input.addEventListener("blur", () => { if (open) closeList(); });
  input.addEventListener("keydown", (e) => {
    const k = e.key;
    if (k === "ArrowDown" || k === "ArrowUp") {
      e.preventDefault();
      if (!open) { openList(); return; }
      if (!shown.length) return;
      const d = k === "ArrowDown" ? 1 : -1;
      setActive(active < 0 ? (d > 0 ? 0 : shown.length - 1) : (active + d + shown.length) % shown.length);
    } else if (k === "Home" && open && shown.length) { e.preventDefault(); setActive(0); }
    else if (k === "End" && open && shown.length) { e.preventDefault(); setActive(shown.length - 1); }
    else if (k === "Enter") {
      if (open) { e.preventDefault(); const it = shown[active]; if (it) choose(it); }
    } else if (k === "Escape") {
      if (open) { e.preventDefault(); e.stopPropagation(); closeList(); }
    } else if (k === "Tab" && open) closeList();
  });

  input.value = text();
  return {
    el,
    set(v) { value = v; if (!open) input.value = text(); },
  };
}
