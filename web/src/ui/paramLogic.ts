// Параметры стратегий: отличие значения от умолчания (для пометки «изменено» и сводки секции).
export interface ParamSpecLite { default: number; step: number }

/** Значение считается изменённым, если оно отличается от умолчания больше чем на полшага (шум округления не в счёт). */
export const isChanged = (value: number, def: number, step: number): boolean =>
  Number.isFinite(value) && Math.abs(value - def) > Math.max(step, 1e-9) / 2;

export function changedParams(values: Record<string, number>, specs: Record<string, ParamSpecLite>): string[] {
  return Object.entries(specs).filter(([k, sp]) => k in values && isChanged(values[k]!, sp.default, sp.step)).map(([k]) => k);
}
