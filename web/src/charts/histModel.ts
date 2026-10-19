// Модель гистограммы доходностей сделок: корзины, тон, подписи (чистая логика, без DOM; рисует charts/histogram.ts).
import type { StatsHistogram } from "../types";
import { isNum as ok, num } from "../ui/fmt";

export type BinTone = "neg" | "pos" | "mixed";
export interface HistBin { lo: number; hi: number; count: number; tone: BinTone; label: string; share: number }
export interface HistModel { bins: HistBin[]; min: number; max: number; zeroPos: number | null; total: number; maxCount: number }

export const binTone = (lo: number, hi: number): BinTone => (hi <= 0 ? "neg" : lo >= 0 ? "pos" : "mixed");
export function binLabel(lo: number, hi: number): string {
  const step = hi - lo;
  const d = step >= 1 ? 1 : step >= 0.1 ? 2 : 3;
  return `${num(lo, d)}…${num(hi, d)}%`;
}
export function histModel(h: StatsHistogram | undefined): HistModel | null {
  if (!h || h.counts.length === 0 || h.edges.length !== h.counts.length + 1) return null;
  const edges = h.edges.map((x) => (ok(x) ? x : NaN));
  if (edges.some((x) => Number.isNaN(x))) return null;
  const total = h.counts.reduce((a, b) => a + b, 0);
  const bins = h.counts.map((count, i) => ({
    lo: edges[i]!, hi: edges[i + 1]!, count, tone: binTone(edges[i]!, edges[i + 1]!), label: binLabel(edges[i]!, edges[i + 1]!), share: total ? count / total : 0,
  }));
  const min = edges[0]!, max = edges[edges.length - 1]!;
  const zeroPos = min < 0 && max > 0 ? (0 - min) / (max - min) : null;
  return { bins, min, max, zeroPos, total, maxCount: Math.max(0, ...h.counts) };
}
