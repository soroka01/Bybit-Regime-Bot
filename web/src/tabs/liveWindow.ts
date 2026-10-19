// Окно результата на вкладке «Бот (live)»: какой период реально показывают чип «за …» и график, и чем он подтверждён (чистая логика, без DOM;
// тесты: tests/liveWindow.test.ts). Правило: цифра никогда не выдаётся под подписью более длинного периода, чем у неё есть данных.
// /api/live отдаёт только последние ~2000 точек кошелька (≈ 7 суток при шаге 5 мин), поэтому «7 д» и «Всё» при нехватке данных берутся из
// /api/stats?range=7d|all (то же, что вкладка «Статистика»); если его нет (404 на старом бэкенде / ошибка) — снимок с честной подписью.
import type { Num, StatsResponse } from "../types";
import { datetime, num, usd } from "../ui/fmt";
import { DAY_MS, rangeStart, sliceSeries, totalPl, utcDayStart, type PlToday, type Pt, type RangeKey } from "./liveCalc";
import { ddmmyy } from "./statsLogic";

/** Допуск «окно покрыто»: первая точка снимка может быть позже начала окна не больше чем на разрыв, который бэкенд считает паузой (15 мин). */
export const COVER_TOL_MS = 15 * 60_000;
export const HINT_OLD_BACKEND = "Полная история доступна после обновления бота";

const fin = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Диапазоны, для которых окно может понадобиться из /api/stats. */
export type FullRange = "7d" | "all";

/** Состояние запроса /api/stats для диапазона. Нет записи — ещё не запрашивали (для окна это то же, что «загрузка»). */
export type FeedState =
  | { kind: "loading" }
  | { kind: "ok"; res: StatsResponse }
  | { kind: "missing" }                                 // 404: бэкенд старый, эндпоинта нет
  | { kind: "error"; message?: string };                // сеть, 5xx, available:false (нет БД / сделок / equity)
export type Feeds = Partial<Record<FullRange, FeedState>>;

/** «5 мин», «7 ч», «6,9 дн.» — фактическая длина окна для подписей. */
export function fmtSpan(ms: number): string {
  if (!(ms > 0)) return "0 мин";
  if (ms < 3_600_000) return `${Math.max(1, Math.round(ms / 60_000))} мин`;
  if (ms < DAY_MS) return `${Math.round(ms / 3_600_000)} ч`;
  return `${num(ms / DAY_MS, 1)} дн.`;
}

export interface Coverage {
  /** Снимок покрывает окно целиком (первая точка не позже начала окна + допуск). Для «Всё» всегда false: полноту истории снимок не гарантирует. */
  full: boolean;
  /** Сколько реально есть данных: от первой точки до якоря. */
  spanMs: number;
  firstMs: number | null;
}

export function snapshotCoverage(range: RangeKey, pts: readonly Pt[], anchorMs: number): Coverage {
  const first = pts[0]?.t ?? null;
  const spanMs = first == null ? 0 : Math.max(0, anchorMs - first);
  if (range === "all") return { full: false, spanMs, firstMs: first };
  return { full: first != null && first <= rangeStart(range, anchorMs) + COVER_TOL_MS, spanMs, firstMs: first };
}

/** Нужен ли ответ /api/stats для окна: «Всё» — всегда; «7 д» — только если снимок не покрывает 7 суток; «24 ч» — никогда (хватает снимка, иначе подпись честно сократится). */
export function statsNeeded(range: RangeKey, pts: readonly Pt[], anchorMs: number): FullRange | null {
  if (range === "all") return "all";
  if (range === "7d") return snapshotCoverage(range, pts, anchorMs).full ? null : "7d";
  return null;
}

export interface WindowPl { abs: number; pct: number | null; base: number; cur: number; since: number }

export interface WindowInfo {
  /** ok — окно определено (pl может быть null: мало точек); loading — ждём /api/stats и ничего не показываем под видом полного периода. */
  state: "ok" | "loading";
  pl: WindowPl | null;
  /** Подпись чипа: «за 24 ч», «за 7 дн.», «за всё время · с 06.10.26», «за доступную историю (≈ 6,9 дн.)», «за доступные 6,9 дн.». */
  label: string;
  /** Окно короче выбранного периода (или полноту истории подтвердить нельзя). */
  partial: boolean;
  source: "snapshot" | "stats";
  /** Фактическая длина окна, мс. */
  spanMs: number;
  /** Одна приглушённая строка под переключателем (только для 404). */
  note: string | null;
  /** Точки для графика (мс) и начало окна (для маркеров). */
  series: Pt[];
  start: number;
  /** Пояснение к чипу (title). */
  title: string;
}

const NOTE_TAIL = "Бот торгует на весь баланс счёта; ввод и вывод средств в цифре не отделены.";
const isoS = (sec: number) => new Date(sec * 1000).toISOString();

function fromSnapshot(range: RangeKey, pts: readonly Pt[], equity: Num | undefined, anchor: number, cov: Coverage, label: string, partial: boolean, note: string | null): WindowInfo {
  const start = rangeStart(range, anchor);
  const pl = totalPl(pts, equity, start);
  const series = sliceSeries(pts, start);
  return {
    state: "ok", pl, label, partial, source: "snapshot", spanMs: pl ? Math.max(0, anchor - pl.since) : cov.spanMs, note, series, start: Number.isFinite(start) ? start : (series[0]?.t ?? -Infinity),
    title: pl ? `Изменение кошелька ${label}: с ${datetime(new Date(pl.since).toISOString())} (${usd(pl.base)}) до сейчас (${usd(pl.cur)}). ${NOTE_TAIL}` : "",
  };
}

function fromStats(range: FullRange, res: StatsResponse): WindowInfo | null {
  const e = res.available ? res.equity : undefined;
  const s = res.series?.equity;
  if (!e || !s || (res.range && res.range !== range)) return null;
  if (!fin(e.eq_start) || !fin(e.eq_end) || !fin(e.eq_start_ts) || !fin(e.eq_end_ts)) return null;
  const series: Pt[] = [];
  for (let i = 0; i < s.t.length; i++) { const v = s.v[i]; if (fin(s.t[i]) && fin(v)) series.push({ t: s.t[i]! * 1000, v }); }
  if (series.length < 2) return null;
  const abs = fin(e.delta) ? e.delta : e.eq_end - e.eq_start;
  const pct = fin(e.wallet_return_pct) ? e.wallet_return_pct / 100 : e.eq_start > 0 ? abs / e.eq_start : null;
  const spanMs = Math.max(0, (e.eq_end_ts - e.eq_start_ts) * 1000);
  const since = e.eq_start_ts * 1000;
  const partial = range === "7d" && !!e.starts_before_data;          // «7 д», а данные equity начинаются позже начала окна: бот работает меньше недели
  const label = range === "all" ? `за всё время · с ${ddmmyy(e.eq_start_ts)}` : partial ? `за доступные ${fmtSpan(spanMs)} · с ${ddmmyy(e.eq_start_ts)}` : "за 7 дн.";
  const what = range === "all" ? "за всё время (вся история кошелька бота)" : partial ? "за всё доступное время (бот работает меньше 7 дней)" : "за 7 суток";
  return {
    state: "ok", pl: { abs, pct, base: e.eq_start, cur: e.eq_end, since }, label, partial, source: "stats", spanMs, note: null, series, start: series[0]!.t,
    title: `Изменение кошелька ${what}: с ${datetime(isoS(e.eq_start_ts))} (${usd(e.eq_start)}) до ${datetime(isoS(e.eq_end_ts))} (${usd(e.eq_end)}), по данным /api/stats. ${NOTE_TAIL}`,
  };
}

/**
 * Окно для чипа результата и графика выбранного периода. pts — точки кошелька из снимка /api/live (мс), feeds — ответы /api/stats.
 * 24 ч / 7 д с полным покрытием — из снимка; 7 д с недостающими данными и «Всё» — из /api/stats; нет его — снимок с подписью «за доступные …».
 */
export function resolveWindow(range: RangeKey, pts: readonly Pt[], equity: Num | undefined, nowMs: number, feeds: Feeds = {}): WindowInfo {
  const anchor = pts[pts.length - 1]?.t ?? nowMs;
  const cov = snapshotCoverage(range, pts, anchor);
  const need = statsNeeded(range, pts, anchor);
  if (!need) {
    return cov.full
      ? fromSnapshot(range, pts, equity, anchor, cov, range === "24h" ? "за 24 ч" : "за 7 дн.", false, null)
      : fromSnapshot(range, pts, equity, anchor, cov, `за доступные ${fmtSpan(cov.spanMs)}`, true, null);       // 24 ч, а данных меньше (бот недавно запущен)
  }
  const f = feeds[need];
  if (!f || f.kind === "loading") {
    return { state: "loading", pl: null, label: need === "all" ? "за всё время" : "за 7 дн.", partial: false, source: "stats", spanMs: 0, note: null, series: [], start: -Infinity, title: "" };
  }
  if (f.kind === "ok") {
    const w = fromStats(need, f.res);
    if (w) return w;
  }
  const label = need === "all" ? `за доступную историю (≈ ${fmtSpan(cov.spanMs)})` : `за доступные ${fmtSpan(cov.spanMs)}`;
  return fromSnapshot(range, pts, equity, anchor, cov, label, true, f.kind === "missing" ? HINT_OLD_BACKEND : null);
}

/** Подпись чипа «сегодня»: база — первая точка текущих суток UTC; если она заметно позже 00:00 (бот запущен днём, снимок короче суток) — время указано. */
export function todayLabel(t: Pick<PlToday, "since">, nowMs: number): string {
  return t.since - utcDayStart(nowMs) <= COVER_TOL_MS ? "сегодня (с 00:00 UTC)" : `сегодня (с ${new Date(t.since).toISOString().slice(11, 16)} UTC)`;
}
