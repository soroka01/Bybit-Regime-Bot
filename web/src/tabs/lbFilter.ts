// Фильтры рейтинга «Лучшие стратегии» по вердикту: счётчики, фильтр, «скрыть слабые», группировка. Чистые функции (тесты: tests/lbFilter.test.ts).
// Порядок карточек НЕ меняется: он задан lbRank.splitRanked (ярус вердикта -> метрика), здесь только отбор и разбиение на подряд идущие группы.
import type { LbItem, Verdict } from "../types";
import { VERDICT_ORDER } from "../ui/verdicts";

export type VFilter = Verdict | "";
export const isVFilter = (v: unknown): v is VFilter => v === "" || (typeof v === "string" && (VERDICT_ORDER as readonly string[]).includes(v));

/** Слабые вердикты: мало сделок и убыточные (идут в конце рейтинга). */
export const WEAK: readonly Verdict[] = ["few", "loss"];
export const isWeak = (v: unknown): boolean => (WEAK as readonly unknown[]).includes(v);

export interface RankedLike { it: LbItem }
const verdictOf = (x: RankedLike): string => String(x.it.result?.verdict ?? "");

export type VerdictCounts = Record<Verdict, number> & { other: number };

export function countVerdicts(ranked: readonly RankedLike[]): VerdictCounts {
  const c: VerdictCounts = { robust: 0, overfit: 0, few: 0, loss: 0, other: 0 };
  for (const x of ranked) {
    const v = verdictOf(x);
    if (v in c && v !== "other") c[v as Verdict]++; else c.other++;
  }
  return c;
}

/**
 * Что показывать: явный фильтр по вердикту главнее всего; иначе при «скрыть слабые» отбрасываются few/loss.
 * Относительный порядок элементов сохраняется.
 */
export function applyVerdictFilter<T extends RankedLike>(ranked: readonly T[], filter: VFilter, hideWeak: boolean): T[] {
  if (filter) return ranked.filter((x) => verdictOf(x) === filter);
  return hideWeak ? ranked.filter((x) => !isWeak(verdictOf(x))) : [...ranked];
}

/** Подряд идущие группы по вердикту (в порядке следования; ранжированный список уже отсортирован по ярусам). */
export function groupByVerdict<T extends RankedLike>(ranked: readonly T[]): { verdict: string; items: T[] }[] {
  const out: { verdict: string; items: T[] }[] = [];
  for (const x of ranked) {
    const v = verdictOf(x);
    const last = out[out.length - 1];
    if (last && last.verdict === v) last.items.push(x); else out.push({ verdict: v, items: [x] });
  }
  return out;
}
