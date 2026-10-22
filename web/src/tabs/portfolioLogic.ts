// Чистая логика вкладки «Портфель»: сумма весов и предупреждения, импорт ног живого бота из снимка /api/live.
import { t, tDyn } from "../i18n";
import type { LegCfg, LiveSnapshot, StrategyMeta } from "../types";
import { num, pct } from "../ui/fmt";

/** Живой бот ограничивает суммарный вес ног до 100 %. */
export const LIVE_WEIGHT_CAP = 1;
export const MAX_LEGS = 8;
/** Верхняя граница веса одной ноги (как у слайдера «Вес»). */
export const LEG_WEIGHT_MAX = 1.5;
const EPS = 1e-9;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

const isFiniteNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Нога из localStorage годится по форме: строки strategy/tf, конечный вес, boolean long_only, params — объект «имя → конечное число» (не массив, не null). */
export function isLegCfg(l: unknown): l is LegCfg {
  if (!l || typeof l !== "object" || Array.isArray(l)) return false;
  const o = l as Record<string, unknown>;
  if (typeof o.strategy !== "string" || typeof o.tf !== "string" || !isFiniteNum(o.weight) || typeof o.long_only !== "boolean") return false;
  const p = o.params;
  return !!p && typeof p === "object" && !Array.isArray(p) && Object.values(p).every(isFiniteNum);
}

/** Вес ноги в допустимых пределах [0, LEG_WEIGHT_MAX]. */
export const clampWeight = (w: number): number => Math.min(LEG_WEIGHT_MAX, Math.max(0, w));

/** Параметры ноги по спецификации стратегии: лишние отбрасываются, недостающие/нечисловые — по умолчанию, значения зажимаются в [min, max]. */
export function normalizeParams(st: StrategyMeta, raw: Record<string, unknown> | null | undefined): Record<string, number> {
  const params: Record<string, number> = {};
  for (const [k, sp] of Object.entries(st.params)) {
    const v = raw?.[k];
    params[k] = isFiniteNum(v) ? clamp(v, sp.min, sp.max) : sp.default;
  }
  return params;
}

export const weightSum = (legs: readonly { weight: number }[]): number => legs.reduce((a, l) => a + (Number.isFinite(l.weight) ? l.weight : 0), 0);

export interface WeightReport {
  sum: number;
  tone: "ok" | "warn" | "bad";
  over: boolean;
  /** Индексы ног с нулевым весом. */
  zero: number[];
  headline: string;
  lines: string[];
}

const listLegs = (idx: number[]): string => idx.map((i) => i + 1).join(", ");

export function weightReport(legs: readonly { weight: number }[], leverage: number): WeightReport {
  const sum = weightSum(legs);
  const zero = legs.map((l, i) => (l.weight <= EPS ? i : -1)).filter((i) => i >= 0);
  const over = sum > LIVE_WEIGHT_CAP + EPS;
  const lines: string[] = [];
  let tone: WeightReport["tone"] = "ok";
  if (sum <= EPS) {
    tone = "bad";
    lines.push(t("portfolio.wsum.all_zero"));
  } else if (over) {
    tone = "warn";
    lines.push(t("portfolio.wsum.over", { sum: pct(sum, 0), exp: num(sum * leverage, 2) }));
  } else if (sum < LIVE_WEIGHT_CAP - 0.005) {
    lines.push(t("portfolio.wsum.free", { free: pct(LIVE_WEIGHT_CAP - sum, 0) }));
  }
  if (zero.length && sum > EPS) {
    lines.push(t("portfolio.wsum.zero_legs", { n: zero.length, list: listLegs(zero) }));
    if (tone === "ok") tone = "warn";
  }
  return { sum, tone, over, zero, headline: t("portfolio.wsum.headline", { sum: pct(sum, 0) }), lines };
}

/**
 * Ноги, прочитанные из localStorage, -> безопасные: неизвестные стратегия/таймфрейм отбрасываются, вес зажимается в [0, LEG_WEIGHT_MAX],
 * параметры приводятся к спецификации стратегии. Остаться может пустой список — вызывающий подставляет ноги по умолчанию.
 */
export function sanitizeLegs(legs: readonly LegCfg[], strategies: ReadonlyMap<string, StrategyMeta>, tfs: readonly string[]): LegCfg[] {
  const out: LegCfg[] = [];
  for (const l of legs) {
    const st = strategies.get(l.strategy);
    if (!st || !tfs.includes(l.tf) || out.length >= MAX_LEGS) continue;
    out.push({ strategy: l.strategy, tf: l.tf, params: normalizeParams(st, l.params), weight: clampWeight(l.weight), long_only: l.long_only });
  }
  return out;
}

interface ImportResult {
  ok: boolean;
  legs: LegCfg[];
  /** Ноги, которые не удалось перенести (причина — в тексте). */
  skipped: string[];
  /** Сколько выключенных ног пропущено. */
  disabled: number;
  reason?: string;
}

/**
 * Ноги живого бота -> ноги портфеля. Берутся только включённые; параметры — по спецификации стратегии (лишние отбрасываются, недостающие — по умолчанию,
 * значения зажимаются в диапазон). long_only берётся из снимка как есть.
 */
export function importLiveLegs(
  snap: LiveSnapshot | null | undefined, strategies: ReadonlyMap<string, StrategyMeta>, tfs: readonly string[],
): ImportResult {
  // причина с сервера: известный код («no_snapshot») переводится, любой другой текст показывается как есть
  if (!snap || !snap.available) return { ok: false, legs: [], skipped: [], disabled: 0, reason: snap?.reason ? (tDyn(`portfolio.live_reason.${snap.reason}`) ?? snap.reason) : t("portfolio.import.no_snapshot") };
  const src = snap.legs ?? [];
  if (!src.length) return { ok: false, legs: [], skipped: [], disabled: 0, reason: t("portfolio.import.no_legs") };
  const legs: LegCfg[] = [];
  const skipped: string[] = [];
  let disabled = 0;
  for (const l of src) {
    const label = `${l.strategy}@${l.tf}`;
    if (!l.enabled) { disabled++; continue; }
    const st = strategies.get(l.strategy);
    if (!st) { skipped.push(t("portfolio.import.skip_unknown", { label })); continue; }
    if (!tfs.includes(l.tf)) { skipped.push(t("portfolio.import.skip_tf", { label })); continue; }
    if (!Number.isFinite(l.weight) || l.weight < 0) { skipped.push(t("portfolio.import.skip_weight", { label })); continue; }
    if (legs.length >= MAX_LEGS) { skipped.push(t("portfolio.import.skip_max", { label, n: MAX_LEGS })); continue; }
    const params = normalizeParams(st, l.params);
    legs.push({ strategy: l.strategy, tf: l.tf, params, weight: Math.min(LEG_WEIGHT_MAX, +l.weight.toFixed(4)), long_only: l.long_only });
  }
  if (!legs.length) return { ok: false, legs, skipped, disabled, reason: t("portfolio.import.none_enabled") };
  return { ok: true, legs, skipped, disabled };
}
