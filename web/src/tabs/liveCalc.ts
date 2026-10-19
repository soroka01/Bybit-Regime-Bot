// Чистая логика вкладки «Бот (live)» (без DOM; тесты: tests/liveCalc.test.ts).
// Всё, что здесь считается «по оценке» (стоп, ликвидация, P/L за день), на экране помечено как расчётное: источник правды — биржа и бот.
import { stepValueAt } from "../charts/align";
import { MMR } from "../leverage";
import type { LiveSnapshot, Num } from "../types";
import { parseTime } from "../ui/fmt";

export const DAY_MS = 86_400_000;
/** Сигнал жизни старше этого — «нет ответа» (янтарный), старше DEAD_S — красный. */
export const STALE_S = 180;
export const DEAD_S = 600;
/** Порог цвета индикатора лимита: с 60 % — предупреждение, с 85 % — опасно. */
export const RISK_WARN = 0.6;
export const RISK_BAD = 0.85;

const fin = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export const utcDayStart = (ms: number): number => Math.floor(ms / DAY_MS) * DAY_MS;

// ---------- ряды
export interface Pt { t: number; v: number }

/** [[iso, value], …] -> точки (мс), без невалидных, по возрастанию времени. */
export function parseSeries(raw: readonly (readonly [string, Num])[] | null | undefined): Pt[] {
  const out: Pt[] = [];
  for (const [ts, v] of raw ?? []) {
    const d = parseTime(ts);
    if (d && fin(v)) out.push({ t: +d, v });
  }
  return out.sort((a, b) => a.t - b.t);
}

export interface PlToday { base: number; cur: number; abs: number; pct: number | null; since: number }

/**
 * Результат за текущие сутки UTC: текущий капитал минус капитал в ПЕРВОЙ точке этих суток.
 * null — сегодня точек ещё нет (бот молчит) или нет текущего значения.
 */
export function plToday(series: readonly Pt[], equity: Num | undefined, nowMs: number): PlToday | null {
  const start = utcDayStart(nowMs);
  const first = series.find((p) => p.t >= start && p.t <= nowMs + 60_000);
  if (!first) return null;
  const last = series[series.length - 1];
  const cur = fin(equity) ? equity : last?.v;
  if (!fin(cur)) return null;
  const abs = cur - first.v;
  return { base: first.v, cur, abs, pct: first.v > 0 ? abs / first.v : null, since: first.t };
}

export interface PlTotal { base: number; cur: number; abs: number; pct: number; since: number }

/**
 * Результат за период графика: текущий кошелёк минус кошелёк в начале окна (первая точка ряда внутри окна [start, ∞);
 * если до start была точка — берётся её значение на start, как у самого графика). start = -Infinity — вся история.
 * Бот торгует на весь баланс счёта: база — только equity_history. Ввод/вывод средств невидимы.
 * null — истории нет, нет текущего значения или база не положительна (процент не определён).
 */
export function totalPl(series: readonly Pt[] | null | undefined, equity: Num | undefined, start: number = -Infinity): PlTotal | null {
  const win = sliceSeries(series ?? [], start);
  const first = win[0];
  const cur = fin(equity) ? equity : win[win.length - 1]?.v;
  if (!first || !fin(cur) || first.v <= 0) return null;
  const abs = cur - first.v;
  return { base: first.v, cur, abs, pct: abs / first.v, since: first.t };
}

/** Просадка от пика как положительная доля (0 — на пике); null — нет данных. */
export function drawdownFrac(equity: Num | undefined, peak: Num | undefined): number | null {
  if (!fin(equity) || !fin(peak) || peak <= 0) return null;
  return Math.max(0, (peak - equity) / peak);
}

/** Начало окна графика; якорь — время последней точки (или «сейчас»), чтобы у остановившегося бота график не пустел. */
export type RangeKey = "24h" | "7d" | "all";
export const RANGES: { key: RangeKey; label: string }[] = [{ key: "24h", label: "24 ч" }, { key: "7d", label: "7 д" }, { key: "all", label: "Всё" }];
export const isRange = (v: unknown): v is RangeKey => v === "24h" || v === "7d" || v === "all";
export function rangeStart(range: RangeKey, anchorMs: number): number {
  return range === "24h" ? anchorMs - DAY_MS : range === "7d" ? anchorMs - 7 * DAY_MS : -Infinity;
}

/** Точки не раньше start; если до start была точка — добавляется «входное» значение на start (ряд не начинается с пустоты). */
export function sliceSeries(pts: readonly Pt[], start: number): Pt[] {
  if (!Number.isFinite(start)) return [...pts];
  const inside = pts.filter((p) => p.t >= start);
  const before = pts.filter((p) => p.t < start).pop();
  return before && inside.length && inside[0]!.t > start ? [{ t: start, v: before.v }, ...inside] : inside;
}

/** Значение ряда на момент t (шагом вперёд: последняя точка не позже t; до первой — первая). */
export const valueAt = (pts: readonly Pt[], t: number): number | null => stepValueAt(pts.length, (i) => pts[i]!.t, (i) => pts[i]!.v, t);

// ---------- сигнал жизни
export type HbTone = "ok" | "warn" | "bad";
export interface Heartbeat { ageS: number | null; tone: HbTone }

/** Свежесть heartbeat: < 3 мин — зелёный, 3–10 мин — янтарный, дольше (или нет данных) — красный. */
export function heartbeatState(hb: string | null | undefined, nowMs: number): Heartbeat {
  const d = parseTime(hb ?? null);
  if (!d) return { ageS: null, tone: "bad" };
  const ageS = Math.max(0, (nowMs - +d) / 1000);
  return { ageS, tone: ageS < STALE_S ? "ok" : ageS < DEAD_S ? "warn" : "bad" };
}

// ---------- таймфреймы и отсчёт до закрытия свечи
const UNIT_MS: Record<string, number> = { m: 60_000, h: 3_600_000, d: DAY_MS, w: 7 * DAY_MS };

/** «1h» / «4h» / «6h» / «15m» / «1d» / «1w» -> длительность свечи в мс; null — не распознано. */
export function tfMs(tf: string): number | null {
  const m = /^\s*(\d+)\s*([mhdw])\s*$/i.exec(tf);
  if (!m) return null;
  const n = +m[1]!;
  return n > 0 ? n * UNIT_MS[m[2]!.toLowerCase()]! : null;
}

/** Ближайшая граница закрытия свечи таймфрейма tf строго после now (мс, UTC). Недельные свечи Bybit начинаются в понедельник 00:00 UTC. */
export function nextClose(tf: string, nowMs: number): number | null {
  const p = tfMs(tf);
  if (p == null) return null;
  const off = /w\s*$/i.test(tf) ? 4 * DAY_MS : 0;      // 1 января 1970 — четверг; понедельник = +4 дня
  return Math.floor((nowMs - off) / p) * p + off + p;
}

/** «0:23:14», «3:05:09», «1 д 02:10:00»; < 1 ч — «23:14». */
export function fmtCountdown(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  const p2 = (n: number) => String(n).padStart(2, "0");
  if (d > 0) return `${d} д ${p2(hh)}:${p2(mm)}:${p2(ss)}`;
  if (hh > 0) return `${hh}:${p2(mm)}:${p2(ss)}`;
  return `${p2(mm)}:${p2(ss)}`;
}

// ---------- позиция
/** Сторона в любом написании сервера/биржи (long/short, Buy/Sell). */
export function isLongSide(side: string | null | undefined): boolean {
  return /^\s*(b|l)/i.test(side ?? "");
}

export interface PosView {
  long: boolean;
  qty: number;
  entry: number;
  mark: number;
  upnl: number;
  notional: number;
  leverage: number | null;
  /** Маржа ≈ номинал / плечо. */
  margin: number | null;
  /** Нереализованный результат в % от маржи. */
  upnlPct: number | null;
  /** Цена стопа, выведенная из цены входа и stop_pct (как в executor.stop_price). */
  stop: number | null;
  /** Запас до стопа от текущей цены в долях (отрицательный — цена уже за стопом). */
  stopDist: number | null;
  /** ОЦЕНКА цены ликвидации: вход × (1 ∓ (1/плечо − поддерживающая маржа 0,5 %)). */
  liq: number | null;
  liqDist: number | null;
  /** Стоп дальше ликвидации — позиция ликвидируется раньше, чем сработает стоп. */
  stopBeyondLiq: boolean;
}

export function positionView(pos: LiveSnapshot["position"], settings: LiveSnapshot["settings"] | undefined): PosView | null {
  if (!pos || !fin(pos.qty) || !pos.qty) return null;
  const long = isLongSide(pos.side);
  const qty = Math.abs(pos.qty);
  const entry = fin(pos.entry) ? pos.entry : NaN;
  const mark = fin(pos.mark) && pos.mark > 0 ? pos.mark : entry;
  const notional = fin(mark) ? qty * mark : NaN;
  const lev = settings && fin(settings.leverage) && settings.leverage >= 1 ? settings.leverage : null;
  const margin = lev && fin(notional) ? notional / lev : null;
  const upnl = fin(pos.upnl) ? pos.upnl : 0;
  const sp = settings?.stop_pct;
  const stop = fin(sp) && sp > 0 && fin(entry) ? entry * (long ? 1 - sp : 1 + sp) : null;
  const room = lev ? 1 / lev - MMR : null;
  const liq = room != null && room > 0 && fin(entry) ? entry * (long ? 1 - room : 1 + room) : null;
  const dist = (lvl: number | null) => (lvl == null || !fin(mark) || mark <= 0 ? null : long ? (mark - lvl) / mark : (lvl - mark) / mark);
  return {
    long, qty, entry, mark, upnl, notional, leverage: lev, margin,
    upnlPct: margin && margin > 0 ? upnl / margin : null,
    stop, stopDist: dist(stop), liq, liqDist: dist(liq),
    stopBeyondLiq: stop != null && liq != null && (long ? stop < liq : stop > liq),
  };
}

// ---------- лимиты риска
export type RiskTone = "ok" | "warn" | "bad";
export interface RiskMeter { ratio: number; tone: RiskTone; left: number }

/** Заполнение шкалы лимита: ratio = value/limit (может быть > 1), tone по порогам 60 % / 85 %, left — запас до лимита в долях. */
export function riskMeter(value: number | null | undefined, limit: number | null | undefined): RiskMeter | null {
  if (!fin(value) || !fin(limit) || limit <= 0) return null;
  const v = Math.max(0, value);
  const ratio = v / limit;
  return { ratio, tone: ratio >= RISK_BAD ? "bad" : ratio >= RISK_WARN ? "warn" : "ok", left: limit - v };
}

// ---------- стоп × плечо × веса
/** Сумма весов включённых ног (enabled === true); null — ног в снимке нет (список отсутствует). */
export function sumWeights(legs: LiveSnapshot["legs"] | null | undefined): number | null {
  if (!Array.isArray(legs)) return null;
  return legs.reduce((a, l) => (l?.enabled === true && fin(l.weight) && l.weight > 0 ? a + l.weight : a), 0);
}

/**
 * Доля счёта, которую теряет полностью загруженная позиция, если сработает биржевой стоп: stop_pct × плечо × Σ весов
 * (номинал = счёт × плечо × Σ весов, стоп уходит на stop_pct от номинала). Оценка без проскальзывания и комиссий.
 * null — нет стопа/плеча/весов (нечего считать).
 */
export function stopRisk(stopPct: Num | undefined, leverage: Num | undefined, weights: Num | undefined): number | null {
  if (!fin(stopPct) || !fin(leverage) || !fin(weights) || stopPct <= 0 || leverage <= 0 || weights <= 0) return null;
  return stopPct * leverage * weights;
}

export interface StopRiskWarning {
  stopPct: number; leverage: number; weights: number;
  /** Потеря счёта при срабатывании стопа, доля. */
  risk: number;
  /** Лимит kill-switch, который стоп пробивает раньше всего (меньший из kill_dd / kill_daily), и его название. */
  limit: number; limitKey: "kill_dd" | "kill_daily"; limitLabel: string;
}

/** Предупреждение показывается, когда stopRisk >= min(kill_dd, kill_daily): биржевой стоп сработает раньше kill-switch. Нет данных — null. */
export function stopRiskWarning(settings: LiveSnapshot["settings"] | null | undefined, legs: LiveSnapshot["legs"] | null | undefined): StopRiskWarning | null {
  const st = settings ?? {};
  const weights = sumWeights(legs);
  const risk = stopRisk(st.stop_pct, st.leverage, weights);
  if (risk == null || weights == null) return null;
  let lim: { k: "kill_dd" | "kill_daily"; label: string; v: number } | null = null;     // при равенстве лимитов — просадка (как в боте)
  for (const [k, label] of [["kill_dd", "просадка"], ["kill_daily", "убыток за день"]] as const) {
    const v = st[k];
    if (fin(v) && v > 0 && (!lim || v < lim.v)) lim = { k, label, v };
  }
  if (!lim || risk < lim.v - 1e-9) return null;
  return { stopPct: st.stop_pct as number, leverage: st.leverage as number, weights, risk, limit: lim.v, limitKey: lim.k, limitLabel: lim.label };
}

// ---------- ноги
export interface LegRow {
  key: string; name: string; tf: string; params: Record<string, number>; weight: number; enabled: boolean;
  signal: number | null; traded: boolean; contribution: number | null; updated: string | null; nextClose: number | null;
}
export type SignalWord = "long" | "short" | "flat" | "none";
export const signalWord = (s: Num | undefined): SignalWord => (!fin(s) ? "none" : s > 0 ? "long" : s < 0 ? "short" : "flat");

export function legRows(legs: LiveSnapshot["legs"], nowMs: number): LegRow[] {
  return (legs ?? []).map((l) => {
    const weight = fin(l.weight) ? l.weight : 0;
    const signal = fin(l.signal) ? l.signal : null;
    const traded = !!l.enabled && weight > 0;
    return {
      key: l.key, name: l.strategy, tf: l.tf, params: l.params ?? {}, weight, enabled: !!l.enabled, signal, traded,
      contribution: signal == null || !traded ? null : signal * weight, updated: l.updated ?? null, nextClose: nextClose(l.tf, nowMs),
    };
  });
}

/** Рабочие ноги (traded) и «спящие» (вес 0 или выключены): спящие в основной таблице только мешают. */
export function splitLegs(rows: readonly LegRow[]): { active: LegRow[]; idle: LegRow[] } {
  return { active: rows.filter((r) => r.traded), idle: rows.filter((r) => !r.traded) };
}

export function legTotals(rows: readonly LegRow[]): { weights: number; contribution: number } {
  return rows.reduce((a, r) => (r.traded ? { weights: a.weights + r.weight, contribution: a.contribution + (r.contribution ?? 0) } : a), { weights: 0, contribution: 0 });
}

// ---------- события и сделки
export type EventKind = "info" | "trade" | "signal" | "warn" | "error" | "daily";
export type EventFilter = "all" | "alerts" | "trades";
export const isEventFilter = (v: unknown): v is EventFilter => v === "all" || v === "alerts" || v === "trades";

export function eventKind(level: string | null | undefined): EventKind {
  const l = (level ?? "").toLowerCase();
  if (l === "error" || l === "critical" || l === "fatal") return "error";
  if (l === "warn" || l === "warning") return "warn";
  if (l === "trade" || l === "signal" || l === "daily") return l;
  return "info";
}
/** Служебный шум: «Действую с опозданием …» после перезапуска — показываем приглушённо. */
export const isNoiseEvent = (text: string): boolean => /^\s*Действую с опозданием/i.test(text);

export type LiveEvent = NonNullable<LiveSnapshot["events"]>[number];
export function filterEvents(events: readonly LiveEvent[] | null | undefined, mode: EventFilter): LiveEvent[] {
  const ts = (e: LiveEvent) => +(parseTime(e.ts) ?? 0);
  return [...(events ?? [])]
    .filter((e) => { const k = eventKind(e.level); return mode === "all" || (mode === "alerts" ? k === "warn" || k === "error" : k === "trade"); })
    .sort((a, b) => ts(b) - ts(a));
}

export type LiveTrade = NonNullable<LiveSnapshot["trades"]>[number];
export const isBuy = (side: string | null | undefined): boolean => /^\s*(buy|long)/i.test(side ?? "");

/** Маркеры сделок на линии капитала: покупка — ▲ (лонг-цвет), продажа — ▼ (шорт-цвет); только внутри окна [start, ∞) и при наличии ряда. */
export function tradeMarkers(trades: readonly LiveTrade[] | null | undefined, eq: readonly Pt[], start: number): { t: number; p: number; kind: "entry"; side: "long" | "short" }[] {
  const out: { t: number; p: number; kind: "entry"; side: "long" | "short" }[] = [];
  for (const tr of trades ?? []) {
    const d = parseTime(tr.ts);
    if (!d || +d < start) continue;
    const p = valueAt(eq, +d);
    if (p == null) continue;
    out.push({ t: Math.floor(+d / 1000), p, kind: "entry", side: isBuy(tr.side) ? "long" : "short" });
  }
  return out;
}

/** Цель экспозиции -> положение маркера на шкале −1…+1 в долях ширины (0…1). */
export const gaugePos = (target: number): number => (Math.max(-1, Math.min(1, target)) + 1) / 2;

// ---------- закрытые сделки (round trips из /api/stats) на вкладке live
/** Ключ закрытой сделки, стабильный между опросами (номер n может сдвинуться при реплее, времена — нет). */
export const closedKey = (r: { entry_ts: number; exit_ts: number }): string => `${r.entry_ts}-${r.exit_ts}`;

/** Ключи сделок, которых не было в прошлом опросе. prev === null — первый опрос: ничего не «новое» (иначе подсветилась бы вся таблица). */
export function newClosedKeys(prev: ReadonlySet<string> | null, rows: readonly { entry_ts: number; exit_ts: number }[]): string[] {
  if (prev == null) return [];
  return rows.map(closedKey).filter((k) => !prev.has(k));
}

/** Нереализованный результат открытой позиции в долях текущего кошелька (upnl / equity); null — нет данных. */
export function upnlAccountFrac(upnl: Num | undefined, equity: Num | undefined): number | null {
  return fin(upnl) && fin(equity) && equity > 0 ? upnl / equity : null;
}
