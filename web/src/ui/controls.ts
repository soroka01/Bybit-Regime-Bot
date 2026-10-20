// Элементы управления: слайдер+число, select, переключатель, сегментированный выбор.
import { h } from "./dom";
import { isChanged } from "./paramLogic";

export interface Control<T> {
  el: HTMLElement;
  set(v: T): void;
}

/** Заполненная часть дорожки ползунка: CSS-переменная --fill (проценты), которую читает styles/components.css. */
export function syncRange(r: HTMLInputElement): void {
  const min = parseFloat(r.min || "0"), max = parseFloat(r.max || "100"), v = parseFloat(r.value);
  const p = max > min && isFinite(v) ? ((v - min) / (max - min)) * 100 : 0;
  r.style.setProperty("--fill", `${Math.min(100, Math.max(0, p)).toFixed(2)}%`);
}

let uid = 0;
const nextId = () => `c${++uid}`;

const decimals = (step: number) => {
  const s = String(step);
  return s.includes(".") ? s.split(".")[1]!.length : 0;
};

interface SliderOpts {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  unit?: string;
  hint?: string;
  /** Значение по умолчанию: включает пометку «изменено», кнопку «по умолч.» и строку min · по умолч. · max. */
  def?: number;
  onInput(v: number): void;
}

export function slider(o: SliderOpts): Control<number> {
  const id = nextId();
  const d = decimals(o.step);
  const range = h("input", { type: "range", id, min: o.min, max: o.max, step: o.step, value: o.value });
  const box = h("input", {
    type: "number", min: o.min, max: o.max, step: o.step, value: o.value.toFixed(d),
    "aria-label": o.label, class: "num-input",
  });
  const hasDef = o.def !== undefined;
  const fmt = (v: number) => String(+v.toFixed(d));
  const dot = h("i", { class: "chg-dot", "aria-hidden": "true", hidden: true });
  const reset = hasDef ? h("button", {
    type: "button", class: "def-btn", "aria-label": `${o.label}: вернуть значение по умолчанию (${fmt(o.def!)})`,
    onClick: () => { apply(o.def!, false); box.focus({ preventScroll: true }); },
  }, "по умолч. ", h("b", { text: fmt(o.def!) })) : null;
  const lim = hasDef ? h("div", { class: "field-range" },
    h("span", { class: "fr-min", text: fmt(o.min) }), reset, h("span", { class: "fr-max", text: fmt(o.max) })) : null;
  let el: HTMLElement;
  const mark = (v: number) => {
    if (!hasDef) return;
    const ch = isChanged(v, o.def!, o.step);
    el.classList.toggle("is-changed", ch);
    dot.hidden = !ch;
    if (reset) reset.disabled = !ch;
  };
  const apply = (raw: number, fromBox: boolean) => {
    if (!isFinite(raw)) return;
    const v = Math.min(o.max, Math.max(o.min, raw));
    range.value = String(v);
    syncRange(range);
    if (!fromBox) box.value = v.toFixed(d);
    mark(v);
    o.onInput(v);
  };
  range.addEventListener("input", () => apply(parseFloat(range.value), false));
  syncRange(range);
  box.addEventListener("input", () => { if (box.value !== "") apply(parseFloat(box.value), true); });
  box.addEventListener("change", () => apply(parseFloat(box.value), false));
  el = h("div", { class: `field slider${hasDef ? " param" : ""}`, title: o.hint ?? "" },
    h("div", { class: "field-head" },
      h("span", { class: "fl" }, h("label", { for: id, text: o.label }), dot),
      h("span", { class: "field-val" }, box, o.unit ? h("span", { class: "unit", text: o.unit }) : null)),
    range, lim);
  mark(o.value);
  return {
    el,
    set(v) {
      range.value = String(v);
      syncRange(range);
      box.value = v.toFixed(d);
      mark(v);
    },
  };
}

interface Option { value: string; label: string; disabled?: boolean }

export function select(o: { label: string; options: Option[]; value: string; onChange(v: string): void }): Control<string> & { setOptions(opts: Option[], value?: string): void } {
  const id = nextId();
  const sel = h("select", { id });
  const fill = (opts: Option[], value: string) => {
    sel.replaceChildren(...opts.map((x) => h("option", { value: x.value, text: x.label, disabled: x.disabled })));
    sel.value = value;
  };
  fill(o.options, o.value);
  sel.addEventListener("change", () => o.onChange(sel.value));
  return {
    el: h("div", { class: "field" }, h("div", { class: "field-head" }, h("label", { for: id, text: o.label })), sel),
    set: (v) => { sel.value = v; },
    setOptions: fill,
  };
}

export function toggle(o: { label: string; checked: boolean; hint?: string; onChange(v: boolean): void }): Control<boolean> & {
  /** Заблокировать переключатель (значение не меняется; why — подсказка, почему). */
  disable(on: boolean, why?: string): void;
} {
  const id = nextId();
  const cb = h("input", { type: "checkbox", id, checked: o.checked, role: "switch" });
  cb.addEventListener("change", () => o.onChange(cb.checked));
  const el = h("label", { class: "toggle", for: id, title: o.hint ?? "" }, cb, h("span", { class: "track" }), h("span", { text: o.label }));
  return {
    el,
    set: (v) => { cb.checked = v; },
    disable(on, why) {
      cb.disabled = on;
      el.classList.toggle("locked", on);
      el.title = on ? (why ?? "") : (o.hint ?? "");
    },
  };
}

export function segmented(o: { label: string; options: Option[]; value: string; onChange(v: string): void }): Control<string> & {
  /** reason(value) -> текст-подсказка, если вариант недоступен, иначе null. */
  disable(reason: (v: string) => string | null): void;
} {
  const btns = new Map<string, HTMLButtonElement>();
  const mark = (v: string) => btns.forEach((b, k) => b.setAttribute("aria-pressed", String(k === v)));
  const el = h("div", { class: "segmented", role: "group", "aria-label": o.label });
  for (const opt of o.options) {
    const b = h("button", {
      type: "button", text: opt.label, disabled: opt.disabled,
      onClick: () => { mark(opt.value); o.onChange(opt.value); },
    });
    btns.set(opt.value, b);
    el.append(b);
  }
  mark(o.value);
  return {
    el, set: mark,
    disable(reason) {
      btns.forEach((b, k) => {
        const why = reason(k);
        b.disabled = why !== null;
        b.title = why ?? "";
      });
    },
  };
}
