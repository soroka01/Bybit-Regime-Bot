// Вкладка «Статистика»: чистая логика (диапазоны, форматирование, KPI, календарь, гистограмма, сделки, предупреждения, выбор состояния).
// Без DOM и сети — тесты: tests/statsLogic.test.ts. Единицы в ответе: *_pct — проценты (1.5 = 1.5 %), время — unix-секунды UTC, деньги — USDT.
import type {
  Num, StatsCalendarCell, StatsHistogram, StatsPnlBucket, StatsRange, StatsResponse, StatsTradeRow, StatsWarning,
} from "../types";
import { stepValueAt } from "../charts/align";
import { agoMs, isNum, MONTHS, num, pctU, snum, spctU, susd, toneOf, usd } from "../ui/fmt";
import { csvCell } from "./tradesLogic";

// ---------------------------------------------------------------- диапазоны
export interface RangeDef { key: StatsRange; label: string; title: string; days: number | null }

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
/** Диапазоны, для которых сервер считает сравнение с предыдущим окном (от 7 дней, кроме «Всё»). */
export const compareSupported = (k: StatsRange): boolean => { const d = rangeDef(k).days; return d != null && d >= 7; };

// ---------------------------------------------------------------- форматирование (проценты — в «процентных» единицах ответа)
const ok = isNum;

// Форматтеры живут в ui/fmt.ts; прежние имена оставлены как совместимые алиасы (тесты и внешние импорты).
// Проценты здесь — в «процентных» единицах ответа (1,5 = 1,5 %), поэтому pctU, а не pct.
export { agoMs, pctU as fp, spctU as fsp, susd as fsusd, toneOf };

/** Длительность по-русски: «45 с», «12 мин», «5 ч 20 мин», «3 д 4 ч». */
export function fmtDuration(sec: Num | undefined): string {
  if (!ok(sec) || sec < 0) return "—";
  const s = Math.round(sec);
  if (s < 60) return `${s} с`;
  if (s < 3600) return `${Math.round(s / 60)} мин`;
  if (s < 86400) {
    const hh = Math.floor(s / 3600), mm = Math.round((s % 3600) / 60);
    return mm === 60 ? `${hh + 1} ч` : mm ? `${hh} ч ${mm} мин` : `${hh} ч`;
  }
  const d = Math.floor(s / 86400), hh = Math.round((s % 86400) / 3600);
  return hh === 24 ? `${d + 1} д` : hh ? `${d} д ${hh} ч` : `${d} д`;
}

/** unix-секунды -> «ГГГГ-ММ-ДД ЧЧ:ММ:СС» (UTC) / ISO с «Z». */
export const isoUtc = (t: number): string => new Date(t * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
const MONTHS_FULL = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
const WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const p2 = (n: number) => String(n).padStart(2, "0");
export const ddmm = (t: number): string => { const d = new Date(t * 1000); return `${p2(d.getUTCDate())}.${p2(d.getUTCMonth() + 1)}`; };
export const ddmmyy = (t: number): string => { const d = new Date(t * 1000); return `${ddmm(t)}.${String(d.getUTCFullYear()).slice(2)}`; };
export const hhmm = (t: number): string => { const d = new Date(t * 1000); return `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`; };
/** «дд.мм.гг чч:мм» (UTC). */
export const stamp = (t: number): string => `${ddmmyy(t)} ${hhmm(t)}`;
/** «ГГГГ-ММ-ДД» -> «дд.мм.гггг». */
export const ymdRu = (s: string): string => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s); return m ? `${m[3]}.${m[2]}.${m[1]}` : s; };

// ---------------------------------------------------------------- корзины P/L
export type BucketKind = "5min" | "hour" | "day" | "week" | "month";
export const BUCKET_TITLE: Record<BucketKind, string> = {
  "5min": "по 5 минутам", hour: "по часам", day: "по дням", week: "по неделям", month: "по месяцам",
};

/** Короткая подпись корзины для оси. */
export function bucketLabel(kind: BucketKind, t: number): string {
  const d = new Date(t * 1000);
  switch (kind) {
    case "5min": return hhmm(t);
    case "hour": return d.getUTCHours() === 0 ? ddmm(t) : `${p2(d.getUTCHours())}:00`;
    case "day": case "week": return ddmm(t);
    default: return `${MONTHS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
  }
}
/** Полное описание корзины для подсказки. */
export function bucketTitle(kind: BucketKind, t: number): string {
  const d = new Date(t * 1000);
  switch (kind) {
    case "5min": case "hour": return `${stamp(t)} UTC`;
    case "day": return `${WD[(d.getUTCDay() + 6) % 7]} ${ddmmyy(t)}`;
    case "week": return `Неделя с ${ddmmyy(t)}`;
    default: return `${MONTHS_FULL[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }
}
/** Значения столбцов: пустые корзины (нет сделок) — разрыв (null), а не нулевой столбец. */
export const bucketValues = (rows: readonly StatsPnlBucket[]): Num[] => rows.map((r) => (r.n > 0 ? r.net : null));

// ---------------------------------------------------------------- календарь P/L (понедельник — первый день недели)
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

// ---------------------------------------------------------------- гистограмма доходностей сделок
export type BinTone = "neg" | "pos" | "mixed";
export interface HistBin { lo: number; hi: number; count: number; tone: BinTone; label: string; share: number }
export interface HistModel { bins: HistBin[]; min: number; max: number; zeroPos: number | null; total: number; maxCount: number }

export const binTone = (lo: number, hi: number): BinTone => (hi <= 0 ? "neg" : lo >= 0 ? "pos" : "mixed");
export function binLabel(lo: number, hi: number): string {
  const step = hi - lo;
  const d = step >= 1 ? 1 : step >= 0.1 ? 2 : 3;
  return `${num(lo, d)}…${num(hi, d)}%`;
}
export function histModel(h: StatsHistogram | undefined): HistModel | null {
  if (!h || h.counts.length === 0 || h.edges.length !== h.counts.length + 1) return null;
  const edges = h.edges.map((x) => (ok(x) ? x : NaN));
  if (edges.some((x) => Number.isNaN(x))) return null;
  const total = h.counts.reduce((a, b) => a + b, 0);
  const bins = h.counts.map((count, i) => ({
    lo: edges[i]!, hi: edges[i + 1]!, count, tone: binTone(edges[i]!, edges[i + 1]!), label: binLabel(edges[i]!, edges[i + 1]!), share: total ? count / total : 0,
  }));
  const min = edges[0]!, max = edges[edges.length - 1]!;
  const zeroPos = min < 0 && max > 0 ? (0 - min) / (max - min) : null;
  return { bins, min, max, zeroPos, total, maxCount: Math.max(0, ...h.counts) };
}

// ---------------------------------------------------------------- сделки: фильтр, подсчёт, CSV
export type StTradeFilter = "all" | "long" | "short" | "win" | "loss" | "recon";
export const ST_TRADE_FILTERS: { key: StTradeFilter; label: string }[] = [
  { key: "all", label: "Все" }, { key: "long", label: "Лонг" }, { key: "short", label: "Шорт" },
  { key: "win", label: "Прибыльные" }, { key: "loss", label: "Убыточные" }, { key: "recon", label: "Реконструированные" },
];
export const isStTradeFilter = (v: unknown): v is StTradeFilter => ST_TRADE_FILTERS.some((f) => f.key === v);

const tradeMatches = (r: StatsTradeRow, f: StTradeFilter): boolean => {
  switch (f) {
    case "long": return r.side === "long";
    case "short": return r.side === "short";
    case "win": return r.net != null && r.net > 0;
    case "loss": return r.net != null && r.net < 0;
    case "recon": return r.reconstructed;
    default: return true;
  }
};
export const filterStTrades = (rows: readonly StatsTradeRow[], f: StTradeFilter): StatsTradeRow[] => (f === "all" ? [...rows] : rows.filter((r) => tradeMatches(r, f)));
export function stTradeCounts(rows: readonly StatsTradeRow[]): Record<StTradeFilter, number> {
  const c: Record<StTradeFilter, number> = { all: rows.length, long: 0, short: 0, win: 0, loss: 0, recon: 0 };
  for (const r of rows) for (const f of ["long", "short", "win", "loss", "recon"] as const) if (tradeMatches(r, f)) c[f]++;
  return c;
}

/** Ячейка CSV для свободного текста: защита от формул (=, +, -, @ в начале) + RFC 4180. */
export const csvText = (s: string | null | undefined): string => csvCell(s != null && /^[=+\-@\t\r]/.test(s) ? "'" + s : s);

export const ST_CSV_HEADER = [
  "n", "entry_utc", "exit_utc", "side", "qty", "entry_price", "exit_price", "gross", "fees_est", "funding_est", "net", "ret_pct", "ret_margin_pct",
  "hold_s", "reason", "reconstructed", "funding_complete", "equity_at_entry", "ret_account_pct",
] as const;
/** CSV сделок: машинный формат (точка в числах, проценты — в процентах, время — ISO UTC), разделитель строк CRLF. */
export function stTradesToCsv(rows: readonly StatsTradeRow[]): string {
  const lines: string[] = [ST_CSV_HEADER.join(",")];
  for (const r of rows) {
    lines.push([
      csvCell(r.n), isoUtc(r.entry_ts), isoUtc(r.exit_ts), r.side, csvCell(r.qty), csvCell(r.entry_price), csvCell(r.exit_price), csvCell(r.gross),
      csvCell(r.fees), csvCell(r.funding), csvCell(r.net), csvCell(r.ret_pct), csvCell(r.ret_margin_pct), csvCell(r.hold_s),
      csvText(r.reason), r.reconstructed ? "1" : "0", r.funding_complete ? "1" : "0", csvCell(r.equity_at_entry), csvCell(r.ret_account_pct),
    ].join(","));
  }
  return lines.join("\r\n") + "\r\n";
}

// ---------------------------------------------------------------- деньги: фиксированные суммы по закрытым сделкам
/** Сводка в долларах: заработано (Σ Net > 0), потеряно (Σ Net < 0, число ≤ 0), итого (Σ Net). Проценты сделок не складываются, поэтому здесь их нет. */
export interface MoneySummary { count: number; wins: number; losses: number; profit: number; loss: number; net: number }

export function moneySummary(rows: readonly StatsTradeRow[]): MoneySummary {
  const s: MoneySummary = { count: rows.length, wins: 0, losses: 0, profit: 0, loss: 0, net: 0 };
  for (const r of rows) {
    if (!ok(r.net)) continue;
    if (r.net > 0) { s.wins++; s.profit += r.net; } else if (r.net < 0) { s.losses++; s.loss += r.net; }
    s.net += r.net;
  }
  return s;
}

/** «Закрыто 3: заработано +12,00 $, потеряно −4,00 $, итого +8,00 $»; без сделок — «Закрытых сделок нет». */
export function moneyLine(s: MoneySummary, label = "Закрыто"): string {
  if (!s.count) return "Закрытых сделок нет";
  return `${label} ${s.count}: заработано ${susd(s.profit)}, потеряно ${susd(s.loss)}, итого ${susd(s.net)}`;
}

/**
 * Итоги окна ответа /api/stats по ВСЕМ сделкам окна (kpis; строки таблицы обрезаны до 500). Нет полей kpis (старый ответ) — считаем по строкам,
 * если они не обрезаны; иначе null.
 */
export function periodSummary(r: StatsResponse | null | undefined): MoneySummary | null {
  if (!r?.available || !r.kpis) return null;
  const k = r.kpis;
  if (ok(k.profit_sum_usdt) && ok(k.loss_sum_usdt) && ok(k.net_pnl)) {
    return { count: k.trades, wins: k.wins, losses: k.losses, profit: k.profit_sum_usdt, loss: k.loss_sum_usdt, net: k.net_pnl };
  }
  const t = r.trades;
  return t && !t.truncated ? moneySummary(t.rows) : null;
}

/** «За 30 дн.: закрыто 12 · плюс +40,00 $ · минус −15,00 $ · итого +25,00 $» (части — отдельно, чтобы вёрстка могла красить числа). */
export function periodParts(s: MoneySummary): { count: string; profit: string; loss: string; net: string } {
  return { count: String(s.count), profit: susd(s.profit), loss: susd(s.loss), net: susd(s.net) };
}

/** Результат сделки: доллары (главное) и «% счёта» (ret_account_pct) — вместе. Нет кошелька на входе — «% счёта: —». */
export function resultText(net: Num | undefined, accountPct: Num | undefined): { usd: string; pct: string; tone: "pos" | "neg" | "" } {
  return { usd: susd(net), pct: ok(accountPct) ? `${spctU(accountPct, 2)} счёта` : "% счёта: —", tone: toneOf(net, 0.005) };
}

/** Нереализованный результат открытой позиции в % кошелька сейчас (проценты — в «процентных» единицах ответа); null — нет данных. */
export const openAccountPct = (upnl: Num | undefined, wallet: Num | undefined): number | null => (ok(upnl) && ok(wallet) && wallet > 0 ? (upnl / wallet) * 100 : null);

/** Окрашивает результат по ЗНАКУ P/L и никогда по стороне: класс ячейки зависит только от net. */
export const resultTone = (r: Pick<StatsTradeRow, "net">): "pos" | "neg" | "" => toneOf(r.net, 0.005);

export const FILL_BASIS: Record<string, string> = {
  stop_level: "по стоп-уровню", candle_close: "по закрытию свечи", candle_open: "по открытию свечи", entry: "по цене входа",
};
export const fillBasisText = (b: string | undefined): string => (b ? (FILL_BASIS[b] ?? b) : "");

// ---------------------------------------------------------------- маркеры сделок на графике капитала
export interface EqMarker { t: number; p: number; kind: "entry" | "exit"; side: "long" | "short"; active?: boolean }
export const MAX_AUTO_MARKERS = 150;

/** Значение ряда на момент t: последняя точка ≤ t (до начала ряда — первая). */
export const valueAt = (t: readonly number[], v: readonly Num[], at: number): number | null => stepValueAt(t.length, (i) => t[i]!, (i) => v[i], at);

/** Маркеры входов/выходов внутри окна; при большом числе сделок — только выбранная. `map` переводит значение капитала в единицы оси (USDT / %). */
export function tradeMarkers(
  rows: readonly StatsTradeRow[], t: readonly number[], v: readonly Num[], from: number, to: number, selected: number | null, map: (x: number) => number = (x) => x,
): EqMarker[] {
  if (!t.length) return [];
  const showAll = rows.length <= MAX_AUTO_MARKERS;
  const out: EqMarker[] = [];
  for (const r of rows) {
    const sel = selected === r.n;
    if (!showAll && !sel) continue;
    for (const [kind, ts] of [["entry", r.entry_ts], ["exit", r.exit_ts]] as const) {
      if (ts < from || ts > to) continue;
      const p = valueAt(t, v, ts);
      if (p == null) continue;
      out.push({ t: ts, p: map(p), kind, side: r.side, active: sel || undefined });
    }
  }
  return out;
}

/** Ряд кошелька в единицах оси: USDT как есть; «% к старту окна» = (v − v₀) / v₀ (v₀ — кошелёк на начале окна; на конце совпадает с ROI счёта). */
export function equityMapper(unit: "usdt" | "pct", base: number | null): (x: number) => number {
  if (unit === "usdt" || base == null || !(base > 0)) return (x) => x;
  return (x) => ((x - base) / base) * 100;
}

// ---------------------------------------------------------------- KPI
export type CmpGroup = "kpis" | "equity";
export interface CmpSpec { group: CmpGroup; field: string; kind: "pp" | "ratio" | "usd" | "count"; better: "up" | "none" }
export interface Kpi {
  key: string; label: string; def: string; value: string; tone: "pos" | "neg" | ""; sub?: string;
  /** Почему значения нет (подсказка к «—»). */
  why?: string; est?: boolean; cmp?: CmpSpec; big?: boolean;
}

const CMP: Record<string, CmpSpec> = {
  roi: { group: "equity", field: "wallet_return_pct", kind: "pp", better: "up" },
  win: { group: "kpis", field: "win_rate_pct", kind: "pp", better: "up" },
  pf: { group: "kpis", field: "profit_factor", kind: "ratio", better: "up" },
  dd: { group: "equity", field: "max_drawdown_pct", kind: "pp", better: "up" },
  sharpe: { group: "equity", field: "sharpe", kind: "ratio", better: "up" },
  trades: { group: "kpis", field: "trades", kind: "count", better: "none" },
  costs: { group: "kpis", field: "fees_total", kind: "usd", better: "none" },
};

export const NEED_SHARPE = "нужно ≥ 14 торговых дней (дневных доходностей кошелька)";
export const NEED_CALMAR = "нужно ≥ 30 торговых дней и ненулевая просадка";
export const NEED_SORTINO = "нужно ≥ 14 торговых дней и хотя бы один убыточный день";
export const NEED_PF = "нет убытков (или нет сделок): делить не на что";

/** Тексты определений для метрик, которых нет в `definitions` сервера (локальные запасные). */
export const LOCAL_DEFS: Record<string, string> = {
  avg_win: "Средний Net прибыльной сделки, USDT (комиссии и фандинг вычтены; по данным Bybit, где они есть).",
  avg_loss: "Средний Net убыточной сделки, USDT.",
  best_trade: "Сделка с наибольшим Net в окне.",
  worst_trade: "Сделка с наименьшим Net в окне.",
  hold_mean: "Среднее время удержания позиции в закрытых сделках окна.",
  hold_median: "Медиана времени удержания: половина сделок короче.",
  streaks: "Наибольшая серия прибыльных / убыточных сделок подряд.",
  gross_pnl: "Σ валового P/L сделок до комиссий и фандинга.",
  best_day: "День UTC с наибольшей доходностью кошелька.",
  worst_day: "День UTC с наименьшей доходностью кошелька.",
  side_counts: "Число закрытых сделок в лонг и в шорт.",
  net_pnl: "Σ Net закрытых сделок окна: валовый P/L − комиссии − фандинг (по данным Bybit, где они есть, иначе оценка бота). Открытая позиция сюда не входит.",
  trades: "Закрытые сделки окна (по времени выхода).",
  costs: "Комиссии + фандинг, уплаченные по сделкам окна (по данным Bybit, где они есть, иначе оценка; отрицательный фандинг — получен).",
  vs_bh: "Разница между ROI счёта и доходностью Buy&Hold BTC за то же окно, п.п. ROI считается на весь кошелёк, B&H — на цену BTC без плеча, так что сравнение приблизительное.",
};

export const defText = (defs: Record<string, string> | undefined, key: string): string => defs?.[key] ?? LOCAL_DEFS[key] ?? "";

const longShort = (r: StatsResponse): { long: number; short: number } => {
  const by = r.breakdown?.by_side ?? [];
  return { long: by.find((x) => x.key === "long")?.count ?? 0, short: by.find((x) => x.key === "short")?.count ?? 0 };
};

/** Главные плитки (герой). Для не-доступного ответа — пустой список. */
export function heroKpis(r: StatsResponse): Kpi[] {
  const k = r.kpis, e = r.equity;
  if (!r.available || !k || !e) return [];
  const pfNull = k.profit_factor == null;
  const pf = pfNull ? (k.trades > 0 && k.losses === 0 && k.wins > 0 ? "∞" : "—") : num(k.profit_factor, 2);
  const ls = longShort(r);
  const bh = k.buy_hold_pct;
  const vs = ok(e.wallet_return_pct) && ok(bh) ? e.wallet_return_pct - bh : null;
  const costs = ok(k.fees_total) || ok(k.funding_total) ? (k.fees_total ?? 0) + (k.funding_total ?? 0) : null;
  return [
    {
      key: "roi", label: "ROI счёта", def: "wallet_return_pct", value: spctU(e.wallet_return_pct, 2), tone: toneOf(e.wallet_return_pct, 0.005), big: true, cmp: CMP.roi,
      sub: `Net P/L сделок ${susd(k.net_pnl)} · Δ кошелька ${susd(e.delta)}`,
    },
    {
      key: "win", label: "Win rate", def: "win_rate_pct", value: k.trades ? pctU(k.win_rate_pct, 1) : "—", tone: "", cmp: CMP.win,
      sub: k.trades ? `${k.wins} прибыльных / ${k.losses} убыточных${k.breakeven ? ` / ${k.breakeven} в ноль` : ""}` : "сделок нет",
    },
    {
      key: "pf", label: "Profit factor", def: "profit_factor", value: pf, tone: "", cmp: CMP.pf, why: pfNull && pf === "—" ? NEED_PF : undefined,
      sub: pf === "∞" ? "убытков нет" : "прибыль / убыток", est: true,
    },
    {
      key: "dd", label: "Макс. просадка", def: "max_drawdown_pct", value: pctU(e.max_drawdown_pct, 2), tone: ok(e.max_drawdown_pct) && e.max_drawdown_pct < -0.005 ? "neg" : "", cmp: CMP.dd,
      sub: e.max_drawdown_peak_ts && e.max_drawdown_trough_ts && (e.max_drawdown_pct ?? 0) < 0 ? `пик ${ddmm(e.max_drawdown_peak_ts)} → дно ${ddmm(e.max_drawdown_trough_ts)}` : "кошелька в окне",
    },
    {
      key: "sharpe", label: "Sharpe", def: "sharpe", value: ok(e.sharpe) ? num(e.sharpe, 2) : "—", tone: toneOf(e.sharpe, 0.005), cmp: CMP.sharpe,
      why: ok(e.sharpe) ? undefined : NEED_SHARPE, sub: ok(e.sharpe) ? `по ${e.daily_points} дн.` : "нужно ≥ 14 торг. дней",
    },
    { key: "trades", label: "Сделок", def: "trades", value: String(k.trades), tone: "", cmp: CMP.trades, sub: `лонг ${ls.long} · шорт ${ls.short}` },
    {
      key: "costs", label: "Комиссии + фандинг", def: "fees_est", value: costs == null ? "—" : usd(costs, 2), tone: "", cmp: CMP.costs, est: true,
      sub: `комиссии ${usd(k.fees_total, 2)} · фандинг ${usd(k.funding_total, 2)}`,
    },
    {
      key: "vsbh", label: "vs Buy & Hold", def: "vs_bh", value: vs == null ? "—" : `${snum(vs, 2)} п.п.`, tone: toneOf(vs, 0.005),
      why: vs == null ? "нет свечей BTC для Buy & Hold за это окно" : undefined, sub: ok(bh) ? `B&H BTC ${spctU(bh, 2)}` : "B&H недоступен",
    },
  ];
}

/** Вторичные метрики («Все метрики»). */
export function miniKpis(r: StatsResponse): Kpi[] {
  const k = r.kpis, e = r.equity;
  if (!r.available || !k || !e) return [];
  const ls = longShort(r);
  const ref = (t: { n: number; net: Num; ret_pct: Num } | null) => (t ? `#${t.n} · ${spctU(t.ret_pct, 2)}` : undefined);
  const day = (d: { date: string; ret_pct: Num } | null) => (d ? `${ymdRu(d.date).slice(0, 5)} · ${spctU(d.ret_pct, 2)}` : "—");
  const m = (key: string, label: string, def: string, value: string, tone: "pos" | "neg" | "" = "", extra: Partial<Kpi> = {}): Kpi => ({ key, label, def, value, tone, ...extra });
  return [
    m("avg_win", "Ср. выигрыш", "avg_win", susd(k.avg_win), toneOf(k.avg_win, 0.005), { est: true }),
    m("avg_loss", "Ср. проигрыш", "avg_loss", susd(k.avg_loss), toneOf(k.avg_loss, 0.005), { est: true }),
    m("payoff", "Payoff", "payoff", ok(k.payoff_ratio) ? num(k.payoff_ratio, 2) : "—"),
    m("exp_usd", "Матожидание, $", "expectancy", susd(k.expectancy_usdt), toneOf(k.expectancy_usdt, 0.005), { est: true }),
    m("exp_pct", "Матожидание, %", "expectancy", spctU(k.expectancy_pct, 2), toneOf(k.expectancy_pct, 0.005), { est: true }),
    m("best", "Лучшая сделка", "best_trade", k.best_trade ? susd(k.best_trade.net) : "—", toneOf(k.best_trade?.net, 0.005), { sub: ref(k.best_trade) }),
    m("worst", "Худшая сделка", "worst_trade", k.worst_trade ? susd(k.worst_trade.net) : "—", toneOf(k.worst_trade?.net, 0.005), { sub: ref(k.worst_trade) }),
    m("hold_mean", "Ср. удержание", "hold_mean", fmtDuration(k.hold_mean_s)),
    m("hold_median", "Медиана удержания", "hold_median", fmtDuration(k.hold_median_s)),
    m("streaks", "Серии (поб. / убыт.)", "streaks", k.trades ? `${k.max_win_streak} / ${k.max_loss_streak}` : "—"),
    m("tim", "Время в рынке", "time_in_market_pct", pctU(k.time_in_market_pct, 1)),
    m("turnover", "Оборот", "turnover", usd(k.turnover, 0), "", { est: true }),
    m("fees_pct", "Комиссии / вал. прибыль", "fees_pct_of_gross_profit", pctU(k.fees_pct_of_gross_profit, 1), "", { est: true }),
    m("gross", "Валовый P/L", "gross_pnl", susd(k.gross_pnl), toneOf(k.gross_pnl, 0.005)),
    m("sortino", "Sortino", "sortino", ok(e.sortino) ? num(e.sortino, 2) : "—", toneOf(e.sortino, 0.005), { why: ok(e.sortino) ? undefined : NEED_SORTINO }),
    m("calmar", "Calmar", "calmar", ok(e.calmar) ? num(e.calmar, 2) : "—", toneOf(e.calmar, 0.005), { why: ok(e.calmar) ? undefined : NEED_CALMAR }),
    m("vol", "Волатильность (год.)", "volatility_pct", pctU(e.volatility_pct, 1), "", { why: ok(e.volatility_pct) ? undefined : NEED_SHARPE }),
    m("best_day", "Лучший день", "best_day", day(e.best_day), toneOf(e.best_day?.ret_pct, 0.005)),
    m("worst_day", "Худший день", "worst_day", day(e.worst_day), toneOf(e.worst_day?.ret_pct, 0.005)),
    m("sides", "Лонг / шорт (сделок)", "side_counts", `${ls.long} / ${ls.short}`),
  ];
}

// ---------------------------------------------------------------- сравнение с предыдущим окном
export type CmpView =
  | { kind: "chip"; dir: "up" | "down" | "flat"; good: "pos" | "neg" | ""; text: string; title: string }
  | { kind: "na"; title: string };

export const CMP_REASON: Record<string, string> = {
  range_too_short: "Сравнение с предыдущим окном доступно от диапазона 1н",
  all_range: "Для диапазона «Всё» предыдущего окна нет",
  no_previous_data: "Нет данных за предыдущее окно такой же длины",
};

export function cmpView(r: StatsResponse, spec: CmpSpec | undefined): CmpView | null {
  if (!spec || !r.available) return null;
  const c = r.comparison;
  if (!c || !c.available) return { kind: "na", title: CMP_REASON[c?.reason ?? ""] ?? "Сравнение с предыдущим окном недоступно" };
  const row = (spec.group === "kpis" ? c.kpis : c.equity)?.[spec.field];
  if (!row || !ok(row.delta)) return { kind: "na", title: "Нет значения за предыдущее окно (например, не хватило дней для метрики)" };
  const d = row.delta;
  const eps = spec.kind === "count" ? 0.5 : 0.005;
  const flat = Math.abs(d) < eps;
  const dir = flat ? "flat" : d > 0 ? "up" : "down";
  const good = flat || spec.better === "none" ? "" : d > 0 ? "pos" : "neg";
  const text = flat ? "без изменений"
    : spec.kind === "pp" ? `${snum(d, 1)} п.п.` : spec.kind === "usd" ? susd(d) : spec.kind === "count" ? snum(d, 0) : snum(d, 2);
  const prev = (x: Num) => (spec.kind === "pp" ? pctU(x, 2) : spec.kind === "usd" ? usd(x) : num(x, spec.kind === "count" ? 0 : 2));
  const period = c.prev_start && c.prev_end ? ` (${ddmmyy(c.prev_start)} – ${ddmmyy(c.prev_end)})` : "";
  return { kind: "chip", dir, good, text, title: `Было в предыдущем окне такой же длины${period}: ${prev(row.prev)}; сейчас: ${prev(row.cur)}` };
}

// ---------------------------------------------------------------- предупреждения качества данных
export interface WarnChip { code: string; level: "warn" | "info"; label: string; text: string; hint?: string }

const WARN_LABEL: Record<string, (w: StatsWarning) => string> = {
  starts_before_data: () => "Данные не с начала окна",
  few_trades: (w) => `Мало сделок${w.count != null ? ` (${w.count})` : ""}`,
  funding_stale: () => "Фандинг устарел",
  candles_missing: () => "Нет свечей",
  position_mismatch: () => "Позиция не сходится",
  reconstructed_trades: (w) => `Реконструкция${w.count != null ? `: ${w.count}` : ""}`,
  env_mixed: () => "Смешанные среды",
  equity_gaps: (w) => `Пропуски капитала${w.count != null ? `: ${w.count}` : ""}`,
  bad_fills: (w) => `Битые записи${w.count != null ? `: ${w.count}` : ""}`,
};

export const FUNDING_HINT = "Нажмите «Подгрузить новые свечи» в полосе свежести данных вверху страницы: она докачает свечи и фандинг с Bybit (публичный API), и статистика пересчитается.";
const WARN_HINT: Record<string, string> = {
  starts_before_data: "ROI и просадка считаются от первой точки капитала, а не от начала выбранного окна — реальный период короче.",
  few_trades: "Win rate, profit factor и другие метрики при малой выборке почти случайны: не делайте выводов, пока закрытых сделок не станет хотя бы несколько десятков.",
  funding_stale: FUNDING_HINT,
  candles_missing: "Без свечей BTCUSDT 1h не считается Buy & Hold, фандинг и цена реконструированных стопов. Подгрузите свечи кнопкой в полосе свежести данных.",
  position_mismatch: "Журнал сделок неполон: закрытия по стопу на бирже и неподтверждённые ордера в него не попадают. Открытая позиция показана по данным бота.",
  reconstructed_trades: "Цена выхода такой сделки — оценка (стоп-уровень или цена закрытия свечи), точность ниже, чем у обычных сделок.",
  env_mixed: "В журнале есть запуски бота в разных средах (mainnet/testnet): цифры могут смешивать их.",
  equity_gaps: "В эти промежутки бот не работал и капитал не записывался: просадка и волатильность могут быть занижены.",
  bad_fills: "Эти записи таблицы trades пропущены при расчёте (нулевая цена/объём или неизвестная сторона).",
};

const fmtUntil = (t: Num | undefined): string => (ok(t) ? stamp(t) + " UTC" : "нет данных");

/** Предупреждения ответа -> чипы: сначала «warn», затем «info». Для funding_stale текст пишем сами (серверный отсылает к несуществующей вкладке «Данные»). */
export function warningChips(ws: readonly StatsWarning[] | undefined): WarnChip[] {
  const chips = (ws ?? []).map((w): WarnChip => {
    let text = w.text;
    if (w.code === "funding_stale") text = `Локальные данные фандинга устарели или отсутствуют (до ${fmtUntil(w.funding_coverage_until)}): для ${w.trades ?? "части"} сделок фандинг неизвестен и принят за 0.`;
    return { code: w.code, level: w.level === "info" ? "info" : "warn", label: WARN_LABEL[w.code]?.(w) ?? String(w.code), text, hint: WARN_HINT[w.code] };
  });
  return [...chips.filter((c) => c.level === "warn"), ...chips.filter((c) => c.level === "info")];
}

// ---------------------------------------------------------------- состояние страницы
export interface ApiErrLike { status: number; code: string; message: string }
export type PageState =
  | { kind: "loading" }
  | { kind: "old_backend" }
  | { kind: "error"; message: string; code: string }
  | { kind: "unavailable"; reason: string; message: string }
  | { kind: "ready" };

export function selectState(input: { res: StatsResponse | null; err: ApiErrLike | null; loading?: boolean }): PageState {
  const { res, err } = input;
  if (err) {
    if (err.status === 404) return { kind: "old_backend" };
    return { kind: "error", message: err.message, code: err.code };
  }
  if (!res) return { kind: "loading" };
  if (!res.available) return { kind: "unavailable", reason: res.reason ?? "error", message: res.message ?? "" };
  return { kind: "ready" };
}

export const UNAVAILABLE_COPY: Record<string, { title: string; hint: string; icon: string }> = {
  no_db: { title: "База бота не найдена", hint: "Бот ещё не запускался. Запустите его (run.bat): он создаст storage/state/bot.db и начнёт записывать сделки и капитал — статистика появится сама.", icon: "power" },
  no_trades: { title: "Закрытых сделок пока нет", hint: "Статистика строится по сделкам бота. Она появится после первой завершённой сделки (вход и выход).", icon: "chart" },
  no_equity: { title: "Нет истории капитала", hint: "Бот записывает капитал кошелька в таблицу equity; подождите несколько минут работы бота — график и ROI появятся.", icon: "chart" },
  db_error: { title: "Не удалось прочитать базу бота", hint: "Файл базы занят или повреждён. Подождите минуту и нажмите «Обновить»; если не проходит — проверьте журнал бота.", icon: "warn" },
  error: { title: "Не удалось посчитать статистику", hint: "На сервере возникла ошибка расчёта. Нажмите «Обновить»; подробности — в журнале сервера дашборда.", icon: "warn" },
};
export const unavailableCopy = (reason: string): { title: string; hint: string; icon: string } => UNAVAILABLE_COPY[reason] ?? UNAVAILABLE_COPY.error!;

/** Сообщение сервера без повтора заголовка: «База бота не найдена: бот ещё не запускался.» под заголовком «База бота не найдена» -> «Бот ещё не запускался.» */
export function trimMessage(title: string, message: string): string {
  const m = (message ?? "").trim();
  if (!m) return "";
  if (m.toLowerCase().startsWith(title.toLowerCase())) {
    const rest = m.slice(title.length).replace(/^[\s:—–-]+/, "");
    return rest ? rest[0]!.toUpperCase() + rest.slice(1) : "";
  }
  return m;
}

// ---------------------------------------------------------------- комиссия
export const BACKTEST_TAKER = 0.00055;
const fmtRate = (r: number): string => `${num(r * 100, r * 100 < 0.1 ? 3 : 2)}%`;
/** Информационная заметка, если реальная ставка тейкера отличается от ставки бэктестов; иначе null. */
export function feeNote(taker: Num | undefined): string | null {
  if (!ok(taker) || Math.abs(taker - BACKTEST_TAKER) < 1e-7) return null;
  return `Реальная комиссия счёта ${fmtRate(taker)} против ${fmtRate(BACKTEST_TAKER)} в бэктестах — результаты стратегий на сайте оптимистичнее`;
}

// ---------------------------------------------------------------- сверка кошелька
export const RECON_REASON: Record<string, string> = {
  position_mismatch: "Журнал сделок не совпадает с позицией бота, поэтому вклад бота оценить нельзя.",
  no_start_price: "Нет свечей BTCUSDT на начало окна — нельзя оценить вклад позиции, открытой до начала окна.",
  no_start_trade: "Позиция была открыта до начала окна, а сделки её открытия в журнале нет — вклад оценить нельзя.",
};
