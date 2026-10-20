// Цветовые утилиты для графиков: разбор CSS-цвета, смешивание, контраст WCAG, выбор читаемого цвета текста поверх заливки.
// Графики берут цвета только из токенов (cssVar), а эти функции дают производные (прозрачность, середина шкалы, цвет подписи).

type RGBA = [number, number, number, number];

/** Разбор #rgb, #rrggbb, rgb()/rgba() (в т.ч. «rgb(255 255 255 / 6%)»). null — если не распознано. */
export function parseColor(input: string): RGBA | null {
  const s = input.trim().toLowerCase();
  let m = /^#([0-9a-f]{3,8})$/.exec(s);
  if (m) {
    let h = m[1]!;
    if (h.length === 3 || h.length === 4) h = [...h].map((c) => c + c).join("");
    if (h.length !== 6 && h.length !== 8) return null;
    const n = (i: number) => parseInt(h.slice(i, i + 2), 16);
    return [n(0), n(2), n(4), h.length === 8 ? n(6) / 255 : 1];
  }
  m = /^rgba?\(([^)]+)\)$/.exec(s);
  if (m) {
    const parts = m[1]!.split(/[\s,/]+/).filter(Boolean);
    if (parts.length < 3) return null;
    const ch = (p: string) => (p.endsWith("%") ? (parseFloat(p) / 100) * 255 : parseFloat(p));
    const a = parts[3] == null ? 1 : parts[3].endsWith("%") ? parseFloat(parts[3]) / 100 : parseFloat(parts[3]);
    const out: RGBA = [ch(parts[0]!), ch(parts[1]!), ch(parts[2]!), a];
    return out.every(Number.isFinite) ? out : null;
  }
  return null;
}

/** Тот же цвет с заданной прозрачностью (для градиентов и заливок). Нераспознанный цвет возвращается как есть. */
export function withAlpha(color: string, a: number): string {
  const c = parseColor(color);
  return c ? `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${+a.toFixed(3)})` : color;
}

/** Линейное смешивание a→b (t=0 даёт a, t=1 даёт b); результат непрозрачный. */
export function mix(a: string, b: string, t: number): string {
  const x = parseColor(a), y = parseColor(b);
  if (!x || !y) return t < 0.5 ? a : b;
  const k = Math.min(1, Math.max(0, t));
  const ch = (i: number) => Math.round(x[i]! + (y[i]! - x[i]!) * k);
  return `rgb(${ch(0)},${ch(1)},${ch(2)})`;
}

const lin = (c: number) => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
export function luminance(color: string): number {
  const c = parseColor(color);
  return c ? 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]) : 0;
}
export function contrast(a: string, b: string): number {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
/** Какой из двух цветов текста читается на заливке лучше. */
export function inkOn(bg: string, light: string, dark: string): string {
  return contrast(bg, light) >= contrast(bg, dark) ? light : dark;
}
