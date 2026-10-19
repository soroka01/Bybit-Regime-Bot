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
