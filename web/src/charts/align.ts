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

/** Равномерное прореживание для длинных рядов. */
export function stride<T>(a: T[], max = 1500): T[] {
  if (a.length <= max) return a;
  const k = Math.ceil(a.length / max);
  const out = a.filter((_, i) => i % k === 0);
  if (out[out.length - 1] !== a[a.length - 1]) out.push(a[a.length - 1]!);
  return out;
}
