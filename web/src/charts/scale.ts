// «Красивые» деления оси: шаг из {1, 2, 2.5, 5}·10^k, границы кратны шагу, ноль попадает на деление, если лежит внутри диапазона.

export interface NiceScale { lo: number; hi: number; step: number; ticks: number[] }

export function niceScale(min: number, max: number, target = 5): NiceScale {
  if (!isFinite(min) || !isFinite(max)) return { lo: 0, hi: 1, step: 0.25, ticks: [0, 0.25, 0.5, 0.75, 1] };
  if (max === min) { max = min + (Math.abs(min) || 1); }
  const raw = (max - min) / Math.max(1, target - 1);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const f = raw / mag;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * mag;
  const lo = Math.floor(min / step + 1e-9) * step, hi = Math.ceil(max / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let v = lo; v <= hi + step * 1e-6; v += step) ticks.push(Math.abs(v) < step * 1e-9 ? 0 : +v.toPrecision(12));
  return { lo, hi, step, ticks };
}
