// Форматирование моментов времени (unix-секунды, UTC) и длительностей. Без DOM; тесты: tests/statsLogic.test.ts.
import type { Num } from "../types";
import { isNum as ok } from "./fmt";

/** Длительность по-русски: «45 с», «12 мин», «5 ч 20 мин», «3 д 4 ч». */
export function fmtDuration(sec: Num | undefined): string {
  if (!ok(sec) || sec < 0) return "—";
  const s = Math.round(sec);
  if (s < 60) return `${s} с`;
  if (s < 3600) { const m = Math.round(s / 60); return m >= 60 ? "1 ч" : `${m} мин`; }     // 59,5+ мин округляется до часа, а не до «60 мин»
  if (s < 86400) {
    const hh = Math.floor(s / 3600), mm = Math.round((s % 3600) / 60);
    return mm === 60 ? `${hh + 1} ч` : mm ? `${hh} ч ${mm} мин` : `${hh} ч`;
  }
  const d = Math.floor(s / 86400), hh = Math.round((s % 86400) / 3600);
  return hh === 24 ? `${d + 1} д` : hh ? `${d} д ${hh} ч` : `${d} д`;
}

/** unix-секунды -> «ГГГГ-ММ-ДД ЧЧ:ММ:СС» (UTC) / ISO с «Z». */
export const isoUtc = (t: number): string => new Date(t * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
export const p2 = (n: number) => String(n).padStart(2, "0");
export const ddmm = (t: number): string => { const d = new Date(t * 1000); return `${p2(d.getUTCDate())}.${p2(d.getUTCMonth() + 1)}`; };
export const ddmmyy = (t: number): string => { const d = new Date(t * 1000); return `${ddmm(t)}.${String(d.getUTCFullYear()).slice(2)}`; };
export const hhmm = (t: number): string => { const d = new Date(t * 1000); return `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`; };
/** «дд.мм.гг чч:мм» (UTC). */
export const stamp = (t: number): string => `${ddmmyy(t)} ${hhmm(t)}`;
/** Время суток в UTC с явным суффиксом: «12:03:05 UTC» (для «обновлено …»; браузерное локальное время не показываем — везде в интерфейсе UTC). */
export const clockUtc = (ms: number): string => `${new Date(ms).toISOString().slice(11, 19)} UTC`;
/** «ГГГГ-ММ-ДД» -> «дд.мм.гггг». */
export const ymdRu = (s: string): string => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s); return m ? `${m[3]}.${m[2]}.${m[1]}` : s; };
