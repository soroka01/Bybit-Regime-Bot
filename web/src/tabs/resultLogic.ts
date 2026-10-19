// Чистая логика вида результата: сравнение с Buy & Hold, предупреждение о коротком периоде.
import type { BacktestResult, Num } from "../types";
import { num } from "../ui/fmt";

export interface Versus { tone: "pos" | "neg" | ""; text: string }

const pp = (x: number): string => {
  const s = num(x * 100, Math.abs(x * 100) < 10 ? 1 : 0);
  return (x > 0 && /[1-9]/.test(s) ? "+" : "") + s;
};

/** Доходность стратегии против Buy & Hold, в процентных пунктах («+12 п.п. к B&H»). */
export function vsBenchmark(ret: Num | undefined, bh: Num | undefined): Versus {
  if (ret == null || bh == null || !Number.isFinite(ret) || !Number.isFinite(bh)) return { tone: "", text: "" };
  const d = ret - bh;
  if (Math.abs(d) * 100 < 0.05) return { tone: "", text: "как B&H" };
  return { tone: d > 0 ? "pos" : "neg", text: `${pp(d)} п.п. к B&H` };
}

/** Просадка против Buy & Hold (значения ≤ 0): меньшая по модулю — лучше. */
export function ddVersus(dd: Num | undefined, bhDd: Num | undefined): Versus {
  if (dd == null || bhDd == null || !Number.isFinite(dd) || !Number.isFinite(bhDd)) return { tone: "", text: "" };
  const d = Math.abs(bhDd) - Math.abs(dd);          // > 0: у стратегии просадка мельче
  if (Math.abs(d) * 100 < 0.5) return { tone: "", text: "как у B&H" };
  return { tone: d > 0 ? "pos" : "neg", text: d > 0 ? `на ${num(d * 100, 0)} п.п. мельче B&H` : `на ${num(-d * 100, 0)} п.п. глубже B&H` };
}

export const SHORT_DAYS = 180;
export const periodDays = (r: Pick<BacktestResult, "data_range">): number =>
  (+new Date(r.data_range.end) - +new Date(r.data_range.start)) / 86400000;
export const isShortPeriod = (r: Pick<BacktestResult, "data_range">): boolean => periodDays(r) < SHORT_DAYS;
