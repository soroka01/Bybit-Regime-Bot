// Вкладка «Статистика»: подписи и значения корзин P/L для столбчатой диаграммы (чистая логика).
import type { Num, StatsPnlBucket } from "../types";
import { MONTHS } from "../ui/fmt";
import { ddmm, ddmmyy, hhmm, p2, stamp } from "../ui/fmtTime";

const MONTHS_FULL = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
const WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

export type BucketKind = "5min" | "hour" | "day" | "week" | "month";
export const BUCKET_TITLE: Record<BucketKind, string> = {
  "5min": "по 5 минутам", hour: "по часам", day: "по дням", week: "по неделям", month: "по месяцам",
};

/** Короткая подпись корзины для оси. */
export function bucketLabel(kind: BucketKind, t: number): string {
  const d = new Date(t * 1000);
  switch (kind) {
    case "5min": return hhmm(t);
    case "hour": return d.getUTCHours() === 0 ? ddmm(t) : `${p2(d.getUTCHours())}:00`;
    case "day": case "week": return ddmm(t);
    default: return `${MONTHS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
  }
}
/** Полное описание корзины для подсказки. */
export function bucketTitle(kind: BucketKind, t: number): string {
  const d = new Date(t * 1000);
  switch (kind) {
    case "5min": case "hour": return `${stamp(t)} UTC`;
    case "day": return `${WD[(d.getUTCDay() + 6) % 7]} ${ddmmyy(t)}`;
    case "week": return `Неделя с ${ddmmyy(t)}`;
    default: return `${MONTHS_FULL[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }
}
/** Значения столбцов: пустые корзины (нет сделок) — разрыв (null), а не нулевой столбец. */
export const bucketValues = (rows: readonly StatsPnlBucket[]): Num[] => rows.map((r) => (r.n > 0 ? r.net : null));
