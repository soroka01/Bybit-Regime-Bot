// Общий «кэш последнего ответа /api/stats»: вкладка «Статистика» кладёт сюда каждый успешный ответ, вкладка «Бот (live)» берёт свежий
// (чтобы не дёргать сервер дважды в минуту ради одних и тех же сделок) или запрашивает сама. Дедуплицирует параллельные запросы.
import { api } from "../api";
import type { StatsRange, StatsResponse } from "../types";

interface Entry { at: number; res: StatsResponse }
const cache = new Map<StatsRange, Entry>();
const inflight = new Map<StatsRange, Promise<StatsResponse>>();

export function rememberStats(res: StatsResponse, at = Date.now()): void {
  if (res?.range) cache.set(res.range, { at, res });
}

/** Ответ не старше maxAgeMs или null. */
export function freshStats(range: StatsRange, maxAgeMs: number, now = Date.now()): StatsResponse | null {
  const e = cache.get(range);
  return e && now - e.at <= maxAgeMs ? e.res : null;
}

/** Свежий кэш или сетевой запрос (один на диапазон одновременно). Ошибки (в т.ч. 404 старого бэкенда) пробрасываются как ApiError. */
export async function loadStats(range: StatsRange, o: { maxAgeMs?: number; now?: number } = {}): Promise<StatsResponse> {
  const hit = freshStats(range, o.maxAgeMs ?? 30_000, o.now);
  if (hit) return hit;
  let p = inflight.get(range);
  if (!p) {
    p = api.stats(range).then((r) => { rememberStats(r); return r; }).finally(() => { inflight.delete(range); });
    inflight.set(range, p);
  }
  return p;
}

export function resetStatsFeed(): void { cache.clear(); inflight.clear(); }
