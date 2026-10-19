// Крошечные DOM-хелперы (без фреймворка).

type Child = Node | string | number | null | undefined | false;
type Props = {
  class?: string;
  text?: string;
  html?: never; // innerHTML намеренно запрещён — только textContent
  style?: Partial<CSSStyleDeclaration> | string;
  [k: string]: unknown;
};

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K, props?: Props | null, ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = String(v);
      else if (k === "text") el.textContent = String(v);
      else if (k === "style") {
        if (typeof v === "string") el.setAttribute("style", v);
        else Object.assign(el.style, v);
      } else if (k.startsWith("on") && typeof v === "function") {
        el.addEventListener(k.slice(2).toLowerCase(), v as EventListener);
      } else if (k in el && typeof v !== "object" && !k.startsWith("aria") && !k.startsWith("data")) {
        (el as unknown as Record<string, unknown>)[k] = v;
      } else el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  append(el, children);
  return el;
}

export function append(el: Element, children: Child[]): void {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    el.append(typeof c === "object" ? c : String(c));
  }
}

export const NS = "http://www.w3.org/2000/svg";
export function svg<K extends keyof SVGElementTagNameMap>(
  tag: K, attrs: Record<string, string | number> = {}, ...children: (Node | string)[]
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  el.append(...children);
  return el;
}

/** Следит за размером элемента. Возвращает функцию отписки. */
export function observeWidth(el: Element, cb: (w: number) => void): () => void {
  const ro = new ResizeObserver((es) => {
    for (const e of es) cb(Math.floor(e.contentRect.width));
  });
  ro.observe(el);
  return () => ro.disconnect();
}

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
