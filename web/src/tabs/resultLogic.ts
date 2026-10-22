// Чистая логика вида результата: сравнение с Buy & Hold, предупреждение о коротком периоде.
import { t } from "../i18n";
import type { BacktestResult, Num } from "../types";
import { num, parseTime } from "../ui/fmt";

interface Versus { tone: "pos" | "neg" | ""; text: string }

const pp = (x: number): string => {
  const s = num(x * 100, Math.abs(x * 100) < 10 ? 1 : 0);
  return (x > 0 && /[1-9]/.test(s) ? "+" : "") + s;
};

/** Доходность стратегии против Buy & Hold, в процентных пунктах («+12 п.п. к B&H»). */
export function vsBenchmark(ret: Num | undefined, bh: Num | undefined): Versus {
  if (ret == null || bh == null || !Number.isFinite(ret) || !Number.isFinite(bh)) return { tone: "", text: "" };
  const d = ret - bh;
  if (Math.abs(d) * 100 < 0.05) return { tone: "", text: t("lab.vs.same") };
  return { tone: d > 0 ? "pos" : "neg", text: t("lab.vs.pp", { pp: pp(d) }) };
}

/** Просадка против Buy & Hold (значения ≤ 0): меньшая по модулю — лучше. */
export function ddVersus(dd: Num | undefined, bhDd: Num | undefined): Versus {
  if (dd == null || bhDd == null || !Number.isFinite(dd) || !Number.isFinite(bhDd)) return { tone: "", text: "" };
  const d = Math.abs(bhDd) - Math.abs(dd);          // > 0: у стратегии просадка мельче
  if (Math.abs(d) * 100 < 0.5) return { tone: "", text: t("lab.vs.ddSame") };
  return { tone: d > 0 ? "pos" : "neg", text: d > 0 ? t("lab.vs.shallower", { pp: num(d * 100, 0) }) : t("lab.vs.deeper", { pp: num(-d * 100, 0) }) };
}

const SHORT_DAYS = 180;
/** Длина периода данных в сутках; строки без часового пояса — UTC (parseTime), иначе браузер читал бы их как местное время и сдвигал на DST. */
export const periodDays = (r: Pick<BacktestResult, "data_range">): number =>
  (+(parseTime(r.data_range.end) ?? NaN) - +(parseTime(r.data_range.start) ?? NaN)) / 86400000;
export const isShortPeriod = (r: Pick<BacktestResult, "data_range">): boolean => periodDays(r) < SHORT_DAYS;
