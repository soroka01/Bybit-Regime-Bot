// Ранжирование карточек «Лучших стратегий». Чистые функции без DOM: порядок = ярус вердикта, внутри — метрика по OOS
// (зеркало core.optimize.rank_items на сервере). Ничего здесь не бросает исключений: неизвестные значения получают запасные.
import { metricAt, type LbItem } from "../types";
import { tierOf } from "../ui/verdicts";

export type Rank = "calmar" | "sharpe" | "return" | "dd";

export const RANKS: { value: Rank; label: string; key: string }[] = [
  { value: "calmar", label: "Calmar (OOS)", key: "calmar" },
  { value: "sharpe", label: "Sharpe (OOS)", key: "sharpe" },
  { value: "return", label: "Доходность (OOS)", key: "total_return" },
  { value: "dd", label: "Просадка (OOS), меньше — лучше", key: "max_drawdown" },
];

export const isRank = (v: unknown): v is Rank => RANKS.some((r) => r.value === v);

/** Ключ метрики OOS для выбранного критерия; для неизвестного критерия — Calmar. */
export const rankKey = (rank: unknown): string => RANKS.find((r) => r.value === rank)?.key ?? "calmar";

/** Значение критерия для карточки; null — карточка не участвует в рейтинге. */
export function rankValue(it: LbItem | null | undefined, rank: unknown): number | null {
  const o = it?.result?.oos;
  if (!o) return null;
  if (rank === "dd" && !(o.trades ?? 0)) return null;
  const v = metricAt(o, rankKey(rank));
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

interface Ranked { name: string; it: LbItem; v: number; tier: number }

/** Делит стратегии на ранжированные (с метрикой) и «остальные» (ожидают/ошибка/пропуск); ранжированные отсортированы. */
export function splitRanked(order: readonly string[], itemOf: (name: string) => LbItem | null, rank: unknown): { ranked: Ranked[]; rest: string[] } {
  const ranked: Ranked[] = [];
  const rest: string[] = [];
  for (const name of order) {
    const it = itemOf(name);
    const v = rankValue(it, rank);
    if (it && v != null) ranked.push({ name, it, v, tier: tierOf(it.result?.verdict) });
    else rest.push(name);
  }
  // ярусы вердикта (устойчива > подгонка > мало сделок > убыточна), внутри яруса — выбранный критерий по OOS; сортировка стабильна
  ranked.sort((a, b) => a.tier - b.tier || b.v - a.v);
  return { ranked, rest };
}
