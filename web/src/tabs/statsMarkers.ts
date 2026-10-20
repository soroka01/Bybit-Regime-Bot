// Вкладка «Статистика»: маркеры сделок и единицы оси графика капитала (чистая логика).
import type { Num, StatsTradeRow } from "../types";
import { stepValueAt } from "../charts/align";

interface EqMarker { t: number; p: number; kind: "entry" | "exit"; side: "long" | "short"; active?: boolean }
const MAX_AUTO_MARKERS = 150;

/** Значение ряда на момент t: последняя точка ≤ t (до начала ряда — первая). */
export const valueAt = (t: readonly number[], v: readonly Num[], at: number): number | null => stepValueAt(t.length, (i) => t[i]!, (i) => v[i], at);

/** Маркеры входов/выходов внутри окна; при большом числе сделок — только выбранная. `map` переводит значение капитала в единицы оси (USDT / %). */
export function tradeMarkers(
  rows: readonly StatsTradeRow[], t: readonly number[], v: readonly Num[], from: number, to: number, selected: number | null, map: (x: number) => number = (x) => x,
): EqMarker[] {
  if (!t.length) return [];
  const showAll = rows.length <= MAX_AUTO_MARKERS;
  const out: EqMarker[] = [];
  for (const r of rows) {
    const sel = selected === r.n;
    if (!showAll && !sel) continue;
    for (const [kind, ts] of [["entry", r.entry_ts], ["exit", r.exit_ts]] as const) {
      if (ts < from || ts > to) continue;
      const p = valueAt(t, v, ts);
      if (p == null) continue;
      out.push({ t: ts, p: map(p), kind, side: r.side, active: sel || undefined });
    }
  }
  return out;
}

/** Ряд кошелька в единицах оси: USDT как есть; «% к старту окна» = (v − v₀) / v₀ (v₀ — кошелёк на начале окна; на конце совпадает с ROI счёта). */
export function equityMapper(unit: "usdt" | "pct", base: number | null): (x: number) => number {
  if (unit === "usdt" || base == null || !(base > 0)) return (x) => x;
  return (x) => ((x - base) / base) * 100;
}
