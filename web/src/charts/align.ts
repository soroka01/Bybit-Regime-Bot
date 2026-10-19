import type { Num } from "../types";

/** Приводит несколько рядов с разными временными сетками к общей оси X (значения «step-вперёд», до старта — null). */
export function alignSeries(series: { t: number[]; v: Num[] }[]): { x: number[]; ys: Num[][] } {
  const x = [...new Set(series.flatMap((s) => s.t))].sort((a, b) => a - b);
  const ys = series.map((s) => {
    const out: Num[] = new Array(x.length);
    let j = 0;
    let last: Num = null;
    for (let i = 0; i < x.length; i++) {
      while (j < s.t.length && s.t[j]! <= x[i]!) { last = s.v[j] ?? null; j++; }
      out[i] = j === 0 ? null : last;
    }
    return out;
  });
  return { x, ys };
}

/**
 * Значение ряда на момент at (step-вперёд): последняя точка ≤ at (бинарный поиск; ряд отсортирован по времени); до начала ряда — первая.
 * Пропуски (null/NaN) обходятся: сначала назад, затем вперёд. Нет ни одного значения — null. Ряд задаётся аксессорами (параллельные массивы или точки).
 */
export function stepValueAt(len: number, timeAt: (i: number) => number, valueAt: (i: number) => Num | undefined, at: number): number | null {
  if (!len) return null;
  let lo = 0, hi = len - 1, i = 0;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (timeAt(m) <= at) { i = m; lo = m + 1; } else hi = m - 1; }
  const fin = (j: number): number | null => { const v = valueAt(j); return v != null && Number.isFinite(v) ? v : null; };
  for (let j = i; j >= 0; j--) { const v = fin(j); if (v != null) return v; }
  for (let j = i + 1; j < len; j++) { const v = fin(j); if (v != null) return v; }
  return null;
}

/** Равномерное прореживание для длинных рядов. */
export function stride<T>(a: T[], max = 1500): T[] {
  if (a.length <= max) return a;
  const k = Math.ceil(a.length / max);
  const out = a.filter((_, i) => i % k === 0);
  if (out[out.length - 1] !== a[a.length - 1]) out.push(a[a.length - 1]!);
  return out;
}
