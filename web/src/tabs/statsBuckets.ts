// Вкладка «Статистика»: подписи и значения корзин P/L для столбчатой диаграммы (чистая логика).
import { t, tList } from "../i18n";
import type { Num, StatsPnlBucket } from "../types";
import { MONTHS } from "../ui/fmt";
import { ddmm, ddmmyy, hhmm, p2, stamp } from "../ui/fmtTime";

export type BucketKind = "5min" | "hour" | "day" | "week" | "month";
export const BUCKET_TITLE: Record<BucketKind, string> = {
  "5min": t("stats.bucket.5min"), hour: t("stats.bucket.hour"), day: t("stats.bucket.day"), week: t("stats.bucket.week"), month: t("stats.bucket.month"),
};

/** Короткая подпись корзины для оси. */
export function bucketLabel(kind: BucketKind, ts: number): string {
  const d = new Date(ts * 1000);
  switch (kind) {
    case "5min": return hhmm(ts);
    case "hour": return d.getUTCHours() === 0 ? ddmm(ts) : `${p2(d.getUTCHours())}:00`;
    case "day": case "week": return ddmm(ts);
    default: return `${MONTHS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
  }
}
/** Полное описание корзины для подсказки. */
export function bucketTitle(kind: BucketKind, ts: number): string {
  const d = new Date(ts * 1000);
  switch (kind) {
    case "5min": case "hour": return `${stamp(ts)} UTC`;
    case "day": return `${tList("date.weekdays_short")[(d.getUTCDay() + 6) % 7]} ${ddmmyy(ts)}`;
    case "week": return t("stats.bucket.week_from", { date: ddmmyy(ts) });
    default: return `${tList("date.months_full")[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }
}
/** Значения столбцов: пустые корзины (нет сделок) — разрыв (null), а не нулевой столбец. */
export const bucketValues = (rows: readonly StatsPnlBucket[]): Num[] => rows.map((r) => (r.n > 0 ? r.net : null));
