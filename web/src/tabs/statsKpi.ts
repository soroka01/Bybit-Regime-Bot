// Вкладка «Статистика»: плитки KPI, определения метрик и сравнение с предыдущим окном (чистая логика).
import type { Num, StatsResponse } from "../types";
import { isNum as ok, num, pctU, snum, spctU, susd, toneOf, usd } from "../ui/fmt";
import { ddmm, ddmmyy, fmtDuration, mdLocal } from "../ui/fmtTime";

type CmpGroup = "kpis" | "equity";
interface CmpSpec { group: CmpGroup; field: string; kind: "pp" | "ratio" | "usd" | "count"; better: "up" | "none" }
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

const NEED_SHARPE = "нужно ≥ 14 торговых дней (дневных доходностей кошелька)";
const NEED_CALMAR = "нужно ≥ 30 торговых дней и ненулевая просадка";
const NEED_SORTINO = "нужно ≥ 14 торговых дней и хотя бы один убыточный день";
const NEED_PF = "нет убытков (или нет сделок): делить не на что";

/** Тексты определений для метрик, которых нет в `definitions` сервера (локальные запасные). */
const LOCAL_DEFS: Record<string, string> = {
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
  const day = (d: { date: string; ret_pct: Num } | null) => (d ? `${mdLocal(d.date)} · ${spctU(d.ret_pct, 2)}` : "—");
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

const CMP_REASON: Record<string, string> = {
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
