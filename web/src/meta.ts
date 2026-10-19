// Общий объект метаданных (/api/meta). После любой загрузки/обновления данных его перечитывают и обновляют НА МЕСТЕ,
// затем оповещают вкладки — каждая сама перестраивает свои селекторы (без потери выбора и прокрутки).
import { api, hooks } from "./api";
import type { Meta } from "./types";

let current: Meta | null = null;
const subs = new Set<() => void>();

export function initMeta(m: Meta): void {
  current = m;
  hooks.afterDataChange = reloadMeta;
}

/** Подписка на обновление метаданных. Возвращает функцию отписки. */
export function onMetaChange(fn: () => void): () => void {
  subs.add(fn);
  return () => { subs.delete(fn); };
}

export async function reloadMeta(): Promise<void> {
  if (!current) return;
  try {
    Object.assign(current, await api.meta(undefined, true)); // fresh: перепроверка у сервера (ETag/304), мимо max-age
  } catch {
    return; // не вышло — остаются прежние метаданные
  }
  subs.forEach((fn) => {
    try { fn(); } catch (e) { console.error("meta subscriber failed", e); }
  });
}
