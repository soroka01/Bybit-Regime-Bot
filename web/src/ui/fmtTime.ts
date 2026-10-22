// Форматирование моментов времени (unix-секунды, UTC) и длительностей. Без DOM; тесты: tests/statsLogic.test.ts.
import { getLang, t } from "../i18n";
import type { Num } from "../types";
import { isNum as ok } from "./fmt";

/** Длительность: ru — «45 с», «12 мин», «5 ч 20 мин», «3 д 4 ч»; en — «45 s», «12 min», «5 h 20 min», «3 d 4 h». */
export function fmtDuration(sec: Num | undefined): string {
  if (!ok(sec) || sec < 0) return "—";
  const s = Math.round(sec);
  const u = { s: t("time.s"), min: t("time.min"), h: t("time.h"), d: t("time.d") };
  if (s < 60) return `${s} ${u.s}`;
  if (s < 3600) { const m = Math.round(s / 60); return m >= 60 ? `1 ${u.h}` : `${m} ${u.min}`; }     // 59,5+ мин округляется до часа, а не до «60 мин»
  if (s < 86400) {
    const hh = Math.floor(s / 3600), mm = Math.round((s % 3600) / 60);
    return mm === 60 ? `${hh + 1} ${u.h}` : mm ? `${hh} ${u.h} ${mm} ${u.min}` : `${hh} ${u.h}`;
  }
  const d = Math.floor(s / 86400), hh = Math.round((s % 86400) / 3600);
  return hh === 24 ? `${d + 1} ${u.d}` : hh ? `${d} ${u.d} ${hh} ${u.h}` : `${d} ${u.d}`;
}

/** unix-секунды -> «ГГГГ-ММ-ДД ЧЧ:ММ:СС» (UTC) / ISO с «Z». */
export const isoUtc = (t: number): string => new Date(t * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
export const p2 = (n: number) => String(n).padStart(2, "0");
/** День и месяц (UTC): ru — «дд.мм», en — «ММ-ДД». */
export const ddmm = (ts: number): string => {
  const d = new Date(ts * 1000);
  return getLang() === "ru" ? `${p2(d.getUTCDate())}.${p2(d.getUTCMonth() + 1)}` : `${p2(d.getUTCMonth() + 1)}-${p2(d.getUTCDate())}`;
};
/** Дата (UTC): ru — «дд.мм.гг», en — «ГГГГ-ММ-ДД». */
export const ddmmyy = (ts: number): string => {
  const d = new Date(ts * 1000);
  return getLang() === "ru" ? `${ddmm(ts)}.${String(d.getUTCFullYear()).slice(2)}` : `${d.getUTCFullYear()}-${ddmm(ts)}`;
};
export const hhmm = (t: number): string => { const d = new Date(t * 1000); return `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`; };
/** Дата и время (UTC): ru — «дд.мм.гг чч:мм», en — «ГГГГ-ММ-ДД чч:мм». */
export const stamp = (ts: number): string => `${ddmmyy(ts)} ${hhmm(ts)}`;
/** Время суток в UTC с явным суффиксом: «12:03:05 UTC» (для «обновлено …»; браузерное локальное время не показываем — везде в интерфейсе UTC). */
export const clockUtc = (ms: number): string => `${new Date(ms).toISOString().slice(11, 19)} UTC`;
/** «ГГГГ-ММ-ДД» -> ru «дд.мм.гггг», en без изменений. */
export const ymdLocal = (s: string): string => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  return m ? (getLang() === "ru" ? `${m[3]}.${m[2]}.${m[1]}` : `${m[1]}-${m[2]}-${m[3]}`) : s;
};
/** «ГГГГ-ММ-ДД» -> ru «дд.мм», en «ММ-ДД». */
export const mdLocal = (s: string): string => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  return m ? (getLang() === "ru" ? `${m[3]}.${m[2]}` : `${m[2]}-${m[3]}`) : s;
};
/** День и месяц числами (ось графиков): ru — «дд.мм», en — «ММ-ДД». */
export const dmLocal = (day: number, month1: number): string => (getLang() === "ru" ? `${p2(day)}.${p2(month1)}` : `${p2(month1)}-${p2(day)}`);
