// РћР±С‰РёР№ В«РєСЌС€ РїРѕСЃР»РµРґРЅРµРіРѕ РѕС‚РІРµС‚Р° /api/statsВ»: РІРєР»Р°РґРєР° В«РЎС‚Р°С‚РёСЃС‚РёРєР°В» РєР»Р°РґС‘С‚ СЃСЋРґР° РєР°Р¶РґС‹Р№ СѓСЃРїРµС€РЅС‹Р№ РѕС‚РІРµС‚, РІРєР»Р°РґРєР° В«Р‘РѕС‚ (live)В» Р±РµСЂС‘С‚ СЃРІРµР¶РёР№
// (С‡С‚РѕР±С‹ РЅРµ РґС‘СЂРіР°С‚СЊ СЃРµСЂРІРµСЂ РґРІР°Р¶РґС‹ РІ РјРёРЅСѓС‚Сѓ СЂР°РґРё РѕРґРЅРёС… Рё С‚РµС… Р¶Рµ СЃРґРµР»РѕРє) РёР»Рё Р·Р°РїСЂР°С€РёРІР°РµС‚ СЃР°РјР°. Р”РµРґСѓРїР»РёС†РёСЂСѓРµС‚ РїР°СЂР°Р»Р»РµР»СЊРЅС‹Рµ Р·Р°РїСЂРѕСЃС‹.
import { api } from "../api";
import type { StatsRange, StatsResponse } from "../types";

interface Entry { at: number; res: StatsResponse }
const cache = new Map<StatsRange, Entry>();
const inflight = new Map<StatsRange, Promise<StatsResponse>>();

export function rememberStats(res: StatsResponse, at = Date.now()): void {
  if (res?.range) cache.set(res.range, { at, res });
}

/** РћС‚РІРµС‚ РЅРµ СЃС‚Р°СЂС€Рµ maxAgeMs РёР»Рё null. */
export function freshStats(range: StatsRange, maxAgeMs: number, now = Date.now()): StatsResponse | null {
  const e = cache.get(range);
  return e && now - e.at <= maxAgeMs ? e.res : null;
}

/** РЎРІРµР¶РёР№ РєСЌС€ РёР»Рё СЃРµС‚РµРІРѕР№ Р·Р°РїСЂРѕСЃ (РѕРґРёРЅ РЅР° РґРёР°РїР°Р·РѕРЅ РѕРґРЅРѕРІСЂРµРјРµРЅРЅРѕ). РћС€РёР±РєРё (РІ С‚.С‡. 404 СЃС‚Р°СЂРѕРіРѕ Р±СЌРєРµРЅРґР°) РїСЂРѕР±СЂР°СЃС‹РІР°СЋС‚СЃСЏ РєР°Рє ApiError. */
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
