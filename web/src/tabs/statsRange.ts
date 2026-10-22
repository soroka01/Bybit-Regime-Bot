// Вкладка «Статистика»: диапазоны окна (чистая логика).
import { t } from "../i18n";
import type { StatsRange } from "../types";

interface RangeDef { key: StatsRange; label: string; title: string; days: number | null }

export const STATS_RANGES: readonly RangeDef[] = [
  { key: "1h", label: t("stats.range.1h.label"), title: t("stats.range.1h.title"), days: 1 / 24 },
  { key: "1d", label: t("stats.range.1d.label"), title: t("stats.range.1d.title"), days: 1 },
  { key: "3d", label: t("stats.range.3d.label"), title: t("stats.range.3d.title"), days: 3 },
  { key: "7d", label: t("stats.range.7d.label"), title: t("stats.range.7d.title"), days: 7 },
  { key: "30d", label: t("stats.range.30d.label"), title: t("stats.range.30d.title"), days: 30 },
  { key: "90d", label: t("stats.range.90d.label"), title: t("stats.range.90d.title"), days: 90 },
  { key: "180d", label: t("stats.range.180d.label"), title: t("stats.range.180d.title"), days: 180 },
  { key: "365d", label: t("stats.range.365d.label"), title: t("stats.range.365d.title"), days: 365 },
  { key: "730d", label: t("stats.range.730d.label"), title: t("stats.range.730d.title"), days: 730 },
  { key: "1095d", label: t("stats.range.1095d.label"), title: t("stats.range.1095d.title"), days: 1095 },
  { key: "all", label: t("stats.range.all.label"), title: t("stats.range.all.title"), days: null },
];
export const DEFAULT_STATS_RANGE: StatsRange = "30d";
export const isStatsRange = (v: unknown): v is StatsRange => STATS_RANGES.some((r) => r.key === v);
export const rangeDef = (k: StatsRange): RangeDef => STATS_RANGES.find((r) => r.key === k) ?? STATS_RANGES[4]!;
export const rangeLabel = (k: StatsRange): string => rangeDef(k).label;
export const rangeTitle = (k: StatsRange): string => rangeDef(k).title;
