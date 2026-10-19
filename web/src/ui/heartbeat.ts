// Свежесть сигнала жизни бота и сторона позиции: общие для плашки в шапке (ui/botStatus.ts) и вкладки «Бот (live)» (tabs/liveCalc.ts, реэкспорт).
import { parseTime } from "./fmt";

/** Сигнал жизни старше этого — «нет ответа» (янтарный), старше DEAD_S — красный. */
export const STALE_S = 180;
export const DEAD_S = 600;

export type HbTone = "ok" | "warn" | "bad";
export interface Heartbeat { ageS: number | null; tone: HbTone }

/** Свежесть heartbeat: < 3 мин — зелёный, 3–10 мин — янтарный, дольше (или нет данных) — красный. */
export function heartbeatState(hb: string | null | undefined, nowMs: number): Heartbeat {
  const d = parseTime(hb ?? null);
  if (!d) return { ageS: null, tone: "bad" };
  const ageS = Math.max(0, (nowMs - +d) / 1000);
  return { ageS, tone: ageS < STALE_S ? "ok" : ageS < DEAD_S ? "warn" : "bad" };
}

/** Сторона в любом написании сервера/биржи (long/short, Buy/Sell), без учёта регистра. */
export function isLongSide(side: string | null | undefined): boolean {
  return /^\s*(b|l)/i.test(side ?? "");
}
