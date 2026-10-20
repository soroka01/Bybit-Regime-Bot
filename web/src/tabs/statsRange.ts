// Вкладка «Статистика»: диапазоны окна (чистая логика).
import type { StatsRange } from "../types";

interface RangeDef { key: StatsRange; label: string; title: string; days: number | null }

export const STATS_RANGES: readonly RangeDef[] = [
  { key: "1h", label: "1ч", title: "последний час", days: 1 / 24 },
  { key: "1d", label: "1д", title: "последние сутки", days: 1 },
  { key: "3d", label: "3д", title: "последние 3 дня", days: 3 },
  { key: "7d", label: "1н", title: "последняя неделя (7 дн.)", days: 7 },
  { key: "30d", label: "1м", title: "последний месяц (30 дн.)", days: 30 },
  { key: "90d", label: "3м", title: "последние 3 месяца (90 дн.)", days: 90 },
  { key: "180d", label: "6м", title: "последние 6 месяцев (180 дн.)", days: 180 },
  { key: "365d", label: "1г", title: "последний год (365 дн.)", days: 365 },
  { key: "730d", label: "2г", title: "последние 2 года (730 дн.)", days: 730 },
  { key: "1095d", label: "3г", title: "последние 3 года (1095 дн.)", days: 1095 },
  { key: "all", label: "Всё", title: "всё время: от первой сделки или точки капитала", days: null },
];
export const DEFAULT_STATS_RANGE: StatsRange = "30d";
export const isStatsRange = (v: unknown): v is StatsRange => STATS_RANGES.some((r) => r.key === v);
export const rangeDef = (k: StatsRange): RangeDef => STATS_RANGES.find((r) => r.key === k) ?? STATS_RANGES[4]!;
export const rangeLabel = (k: StatsRange): string => rangeDef(k).label;
export const rangeTitle = (k: StatsRange): string => rangeDef(k).title;
