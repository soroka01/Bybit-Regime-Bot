// Модель календаря дневного P/L: сетка недель, уровни цвета, годовые секции, компактный список (чистая логика, без DOM; рисует charts/calendar.ts).
import type { StatsCalendarCell } from "../types";
import { isNum as ok, MONTHS } from "../ui/fmt";

export interface CalDay { date: string; row: number; col: number; net: number | null; trades: number; inRange: boolean }
export interface CalGrid { cols: number; days: CalDay[]; months: { col: number; label: string }[]; maxAbs: number }

const DAY = 86400000;
const ymdToMs = (s: string): number => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s); return m ? Date.UTC(+m[1]!, +m[2]! - 1, +m[3]!) : NaN; };
const msToYmd = (ms: number): string => new Date(ms).toISOString().slice(0, 10);
const wdMon0 = (ms: number): number => (new Date(ms).getUTCDay() + 6) % 7;

export function calendarGrid(startDate: string, endDate: string, cells: readonly StatsCalendarCell[]): CalGrid {
  const s = ymdToMs(startDate), e = ymdToMs(endDate);
  if (!Number.isFinite(s) || !Number.isFinite(e) || e < s) return { cols: 0, days: [], months: [], maxAbs: 0 };
  const byDate = new Map(cells.map((c) => [c.date, c]));
  const g0 = s - wdMon0(s) * DAY;
  const g1 = e + (6 - wdMon0(e)) * DAY;
  const days: CalDay[] = [];
  let maxAbs = 0;
  for (let t = g0; t <= g1; t += DAY) {
    const date = msToYmd(t);
    const idx = Math.round((t - g0) / DAY);
    const inRange = t >= s && t <= e;
    const c = inRange ? byDate.get(date) : undefined;
    const net = c && ok(c.net) ? c.net : null;
    if (net != null) maxAbs = Math.max(maxAbs, Math.abs(net));
    days.push({ date, row: idx % 7, col: Math.floor(idx / 7), net, trades: c?.trades ?? 0, inRange });
  }
  const cols = days.length ? days[days.length - 1]!.col + 1 : 0;
  // подписи месяцев: колонка, в которой лежит 1-е число месяца (и первая колонка для неполного первого месяца); подписи ближе 3 колонок не ставим
  const months: { col: number; label: string }[] = [];
  const push = (col: number, label: string) => {
    const last = months[months.length - 1];
    if (last && col - last.col < 3) {
      if (last.col === 0) months.pop(); else return;      // неполный первый месяц уступает следующему
    }
    months.push({ col, label });
  };
  const firstIn = days.find((d) => d.inRange);
  // первая колонка: если в ней уже начался следующий месяц (1-е число), подписываем им — короткий «хвост» прошлого месяца не важен
  const first01 = days.find((d) => d.col === 0 && d.inRange && d.date.endsWith("-01"));
  const lab0 = first01 ?? firstIn;
  if (lab0) push(0, MONTHS[+lab0.date.slice(5, 7) - 1]!);
  for (const d of days) if (d.inRange && d.date.endsWith("-01") && d.col > 0) push(d.col, MONTHS[+d.date.slice(5, 7) - 1]!);
  return { cols, days, months, maxAbs };
}

/** Уровень цвета ячейки −1…+1 (знак — прибыль/убыток, модуль — величина; ненулевые не бледнее 0,22). 0 — нет сделок или нулевой итог. */
export function calLevel(net: number | null, maxAbs: number): number {
  if (net == null || net === 0 || !(maxAbs > 0)) return 0;
  const k = Math.min(1, Math.pow(Math.abs(net) / maxAbs, 0.6));
  return Math.sign(net) * Math.max(0.22, k);
}

export interface CalSection { year: number | null; grid: CalGrid }
/** Периоды до ~12 месяцев — одна сетка; длиннее — по календарному году (свежий год сверху), чтобы клетки оставались читаемыми. */
export function calendarSections(startDate: string, endDate: string, cells: readonly StatsCalendarCell[]): CalSection[] {
  const s = ymdToMs(startDate), e = ymdToMs(endDate);
  if (!Number.isFinite(s) || !Number.isFinite(e) || e < s) return [];
  if ((e - s) / DAY <= 372) return [{ year: null, grid: calendarGrid(startDate, endDate, cells) }];
  const out: CalSection[] = [];
  // годы раньше первой торговой даты не показываем (пустые «простыни» ни о чём не говорят)
  const firstTrade = cells.filter((c) => c.trades > 0).map((c) => ymdToMs(c.date)).filter(Number.isFinite).sort((x, y) => x - y)[0];
  const from = firstTrade == null ? new Date(e).getUTCFullYear() : new Date(Math.max(s, firstTrade)).getUTCFullYear();
  for (let y = new Date(e).getUTCFullYear(); y >= from; y--) {
    const a = Math.max(s, Date.UTC(y, 0, 1)), b = Math.min(e, Date.UTC(y, 11, 31));
    out.push({ year: y, grid: calendarGrid(msToYmd(a), msToYmd(b), cells) });
  }
  return out;
}
/** Окно короче 14 суток — вместо сетки компактный список по дням. */
export const calendarIsCompact = (startDate: string, endDate: string): boolean => {
  const s = ymdToMs(startDate), e = ymdToMs(endDate);
  return !(Number.isFinite(s) && Number.isFinite(e)) || (e - s) / DAY + 1 < 14;
};
/** Список дней окна (для компактного вида): все сутки от начала до конца, новые сверху. */
export function calendarList(startDate: string, endDate: string, cells: readonly StatsCalendarCell[]): { date: string; net: number | null; trades: number }[] {
  const s = ymdToMs(startDate), e = ymdToMs(endDate);
  if (!Number.isFinite(s) || !Number.isFinite(e) || e < s) return [];
  const by = new Map(cells.map((c) => [c.date, c]));
  const out: { date: string; net: number | null; trades: number }[] = [];
  for (let t = e; t >= s; t -= DAY) { const d = msToYmd(t), c = by.get(d); out.push({ date: d, net: c && ok(c.net) ? c.net : null, trades: c?.trades ?? 0 }); }
  return out;
}
