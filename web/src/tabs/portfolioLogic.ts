// Чистая логика вкладки «Портфель»: сумма весов и предупреждения, импорт ног живого бота из снимка /api/live.
import type { LegCfg, LiveSnapshot, StrategyMeta } from "../types";
import { num, pct } from "../ui/fmt";

/** Живой бот ограничивает суммарный вес ног до 100 %. */
export const LIVE_WEIGHT_CAP = 1;
export const MAX_LEGS = 8;
const EPS = 1e-9;

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
    lines.push("Все веса нулевые: портфель не держит позиций, результат пустой.");
  } else if (over) {
    tone = "warn";
    lines.push(`Живой бот ограничивает суммарный вес 100 %. При Σ = ${pct(sum, 0)} бэктест здесь оптимистичнее бота (макс. экспозиция ≈ ${num(sum * leverage, 2)}× капитала).`);
  } else if (sum < LIVE_WEIGHT_CAP - 0.005) {
    lines.push(`Свободно ${pct(LIVE_WEIGHT_CAP - sum, 0)} капитала: эта доля остаётся в деньгах.`);
  }
  if (zero.length && sum > EPS) {
    lines.push(`${zero.length === 1 ? "Нога" : "Ноги"} ${listLegs(zero)} с весом 0 — не торгу${zero.length === 1 ? "ет" : "ют"} ни здесь, ни в боте.`);
    if (tone === "ok") tone = "warn";
  }
  return { sum, tone, over, zero, headline: `Σ весов ${pct(sum, 0)}`, lines };
}

export interface ImportResult {
  ok: boolean;
  legs: LegCfg[];
  /** Ноги, которые не удалось перенести (причина — в тексте). */
  skipped: string[];
  /** Сколько выключенных ног пропущено. */
  disabled: number;
  /** Есть перенесённые ноги, у которых в снимке не было признака long_only (старый бот): он взят из набора по умолчанию. */
  legacyLongOnly?: boolean;
  reason?: string;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * Ноги живого бота -> ноги портфеля. Берутся только включённые; параметры — по спецификации стратегии (лишние отбрасываются, недостающие — по умолчанию,
 * значения зажимаются в диапазон). long_only берётся из снимка; у старых снимков (без поля) — у одноимённой ноги по умолчанию (strategy+tf), иначе false (`ImportResult.legacyLongOnly`).
 */
export function importLiveLegs(
  snap: LiveSnapshot | null | undefined, strategies: ReadonlyMap<string, StrategyMeta>, tfs: readonly string[], defaults: readonly LegCfg[],
): ImportResult {
  if (!snap || !snap.available) return { ok: false, legs: [], skipped: [], disabled: 0, reason: snap?.reason ?? "Снимок бота недоступен: бот не запущен или база состояния не найдена." };
  const src = snap.legs ?? [];
  if (!src.length) return { ok: false, legs: [], skipped: [], disabled: 0, reason: "В снимке бота нет ног." };
  const legs: LegCfg[] = [];
  const skipped: string[] = [];
  let disabled = 0;
  let legacyLongOnly = false;
  for (const l of src) {
    const label = `${l.strategy}@${l.tf}`;
    if (!l.enabled) { disabled++; continue; }
    const st = strategies.get(l.strategy);
    if (!st) { skipped.push(`${label}: нет такой стратегии в дашборде`); continue; }
    if (!tfs.includes(l.tf)) { skipped.push(`${label}: таймфрейм не поддерживается портфелем`); continue; }
    if (!Number.isFinite(l.weight) || l.weight < 0) { skipped.push(`${label}: некорректный вес`); continue; }
    if (legs.length >= MAX_LEGS) { skipped.push(`${label}: не больше ${MAX_LEGS} ног`); continue; }
    const params: Record<string, number> = {};
    for (const [k, sp] of Object.entries(st.params)) {
      const v = l.params?.[k];
      params[k] = typeof v === "number" && Number.isFinite(v) ? clamp(v, sp.min, sp.max) : sp.default;
    }
    const twin = defaults.find((d) => d.strategy === l.strategy && d.tf === l.tf);
    if (typeof l.long_only !== "boolean") legacyLongOnly = true;
    legs.push({ strategy: l.strategy, tf: l.tf, params, weight: Math.min(1.5, +l.weight.toFixed(4)), long_only: typeof l.long_only === "boolean" ? l.long_only : (twin?.long_only ?? false) });
  }
  if (!legs.length) return { ok: false, legs, skipped, disabled, reason: "Нет включённых ног, которые можно перенести." };
  return { ok: true, legs, skipped, disabled, legacyLongOnly };
}
