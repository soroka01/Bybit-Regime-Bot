// Патчинг списка по ключам: элементы с тем же data-key и той же подписью (data-sig) переиспользуются как есть
// (сохраняются состояние фокуса/анимации), остальные пересоздаются; порядок приводится к заданному минимальным числом перестановок.
export interface KeyedItem { key: string; sig: string; build(): HTMLElement }

export function patchKeyed(host: HTMLElement, items: KeyedItem[]): void {
  const old = new Map<string, HTMLElement>();
  for (const ch of Array.from(host.children) as HTMLElement[]) {
    const k = ch.dataset.key;
    if (k !== undefined && !old.has(k)) old.set(k, ch);
    else ch.remove();
  }
  const want: HTMLElement[] = items.map((it) => {
    const cur = old.get(it.key);
    if (cur && cur.dataset.sig === it.sig) return cur;
    const el = it.build();
    el.dataset.key = it.key;
    el.dataset.sig = it.sig;
    return el;
  });
  const keep = new Set(want);
  for (const el of old.values()) if (!keep.has(el)) el.remove();
  want.forEach((el, i) => {
    if (host.children[i] !== el) host.insertBefore(el, host.children[i] ?? null);
  });
}

// ---------- секции страницы: замена на месте без потери фокуса, прокрутки и озвучивания
const FOCUSABLE = "a[href], button, input, select, textarea, [tabindex]";
const SCROLLERS = ".table-wrap, .lv-timeline, [tabindex='0']";

/**
 * Подпись содержимого секции для сравнения: outerHTML без текста «живых» элементов [data-tk] (его правит тикер раз в секунду, он не считается изменением).
 * Одинаковая подпись = пользователь не увидит разницы.
 */
export function sectionSig(el: Element): string {
  const c = el.cloneNode(true) as Element;
  c.querySelectorAll("[data-tk]").forEach((n) => { n.textContent = ""; });
  return c.outerHTML.replace(/\bsp\d+\b/g, "sp");       // id градиентов sparkline нумеруются при каждой сборке — на вид не влияют
}

/**
 * Приводит содержимое слота (контейнер с одной секцией) к next. Если подпись не изменилась — DOM не трогается вовсе (фокус, прокрутка, анимации,
 * role=alert/status не переозвучиваются). Иначе секция заменяется, а фокус и прокрутка внутри неё переносятся на тот же по порядку элемент.
 * next === null — убрать секцию. Возвращает true, если DOM изменился.
 */
export function patchSection(slot: HTMLElement, next: HTMLElement | null): boolean {
  const cur = slot.firstElementChild as HTMLElement | null;
  if (!next) {
    if (!cur) return false;
    slot.replaceChildren();
    return true;
  }
  if (!cur) { slot.replaceChildren(next); return true; }
  if (sectionSig(cur) === sectionSig(next)) return false;
  const ae = document.activeElement as HTMLElement | null;
  const focusIdx = ae && cur.contains(ae) ? [cur, ...Array.from(cur.querySelectorAll<HTMLElement>(FOCUSABLE))].indexOf(ae) : -1;
  const scrolls = [cur, ...Array.from(cur.querySelectorAll<HTMLElement>(SCROLLERS))].map((e) => [e.scrollTop, e.scrollLeft] as const);
  cur.replaceWith(next);
  [next, ...Array.from(next.querySelectorAll<HTMLElement>(SCROLLERS))].forEach((e, i) => {
    const s = scrolls[i];
    if (s && (s[0] || s[1])) { e.scrollTop = s[0]; e.scrollLeft = s[1]; }
  });
  if (focusIdx >= 0) {
    const list = [next, ...Array.from(next.querySelectorAll<HTMLElement>(FOCUSABLE))];
    const t = list[Math.min(focusIdx, list.length - 1)];
    if (t && t.tabIndex >= 0) t.focus({ preventScroll: true });
  }
  return true;
}
