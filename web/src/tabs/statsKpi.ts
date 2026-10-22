// Вкладка «Статистика»: плитки KPI, определения метрик и сравнение с предыдущим окном (чистая логика).
import { getLang, t, tDyn } from "../i18n";
import { metricDefinition } from "../i18n/serverText";
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

const NEED_SHARPE = t("stats.need.sharpe");
const NEED_CALMAR = t("stats.need.calmar");
const NEED_SORTINO = t("stats.need.sortino");
const NEED_PF = t("stats.need.pf");
const PP = t("stats.unit.pp");

/** Определение метрики. Сервер присылает `defs` (русский текст): на русском показываем его как есть (русская копия в definitions.json совпадает с ним),
 *  на другом языке — перевод по ключу, а если перевода нет, серверный текст. Метрики, которых нет в `defs` (локальные запасные, stats.def.*), пишем сами.
 *  `fee` — ставка тейкера из config (подставляется в определение комиссий). */
export function defText(defs: Record<string, string> | undefined, key: string, fee?: Num): string {
  const server = defs?.[key];
  if (server !== undefined) return getLang() === "ru" ? server : metricDefinition(key, server, { fee: `${((ok(fee) ? fee : 0.001) * 100).toFixed(3)}%` });
  return tDyn(`stats.def.${key}`) ?? "";
}

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
      key: "roi", label: t("stats.kpi.roi"), def: "wallet_return_pct", value: spctU(e.wallet_return_pct, 2), tone: toneOf(e.wallet_return_pct, 0.005), big: true, cmp: CMP.roi,
      sub: t("stats.kpi.roi_sub", { net: susd(k.net_pnl), delta: susd(e.delta) }),
    },
    {
      key: "win", label: "Win rate", def: "win_rate_pct", value: k.trades ? pctU(k.win_rate_pct, 1) : "—", tone: "", cmp: CMP.win,
      sub: k.trades ? (k.breakeven ? t("stats.kpi.win_sub_be", { w: k.wins, l: k.losses, b: k.breakeven }) : t("stats.kpi.win_sub", { w: k.wins, l: k.losses })) : t("stats.kpi.no_trades"),
    },
    {
      key: "pf", label: "Profit factor", def: "profit_factor", value: pf, tone: "", cmp: CMP.pf, why: pfNull && pf === "—" ? NEED_PF : undefined,
      sub: pf === "∞" ? t("stats.kpi.no_losses") : t("stats.kpi.profit_loss"), est: true,
    },
    {
      key: "dd", label: t("stats.kpi.dd"), def: "max_drawdown_pct", value: pctU(e.max_drawdown_pct, 2), tone: ok(e.max_drawdown_pct) && e.max_drawdown_pct < -0.005 ? "neg" : "", cmp: CMP.dd,
      sub: e.max_drawdown_peak_ts && e.max_drawdown_trough_ts && (e.max_drawdown_pct ?? 0) < 0 ? t("stats.kpi.dd_sub", { a: ddmm(e.max_drawdown_peak_ts), b: ddmm(e.max_drawdown_trough_ts) }) : t("stats.kpi.dd_sub_wallet"),
    },
    {
      key: "sharpe", label: "Sharpe", def: "sharpe", value: ok(e.sharpe) ? num(e.sharpe, 2) : "—", tone: toneOf(e.sharpe, 0.005), cmp: CMP.sharpe,
      why: ok(e.sharpe) ? undefined : NEED_SHARPE, sub: ok(e.sharpe) ? t("stats.kpi.sharpe_sub", { n: e.daily_points }) : t("stats.kpi.sharpe_need"),
    },
    { key: "trades", label: t("stats.kpi.trades"), def: "trades", value: String(k.trades), tone: "", cmp: CMP.trades, sub: t("stats.kpi.trades_sub", { l: ls.long, s: ls.short }) },
    {
      key: "costs", label: t("stats.kpi.costs"), def: "fees_est", value: costs == null ? "—" : usd(costs, 2), tone: "", cmp: CMP.costs, est: true,
      sub: t("stats.kpi.costs_sub", { fees: usd(k.fees_total, 2), funding: usd(k.funding_total, 2) }),
    },
    {
      key: "vsbh", label: "vs Buy & Hold", def: "vs_bh", value: vs == null ? "—" : `${snum(vs, 2)} ${PP}`, tone: toneOf(vs, 0.005),
      why: vs == null ? t("stats.kpi.vs_bh_why") : undefined, sub: ok(bh) ? t("stats.kpi.bh_sub", { v: spctU(bh, 2) }) : t("stats.kpi.bh_na"),
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
    m("avg_win", t("stats.kpi.avg_win"), "avg_win", susd(k.avg_win), toneOf(k.avg_win, 0.005), { est: true }),
    m("avg_loss", t("stats.kpi.avg_loss"), "avg_loss", susd(k.avg_loss), toneOf(k.avg_loss, 0.005), { est: true }),
    m("payoff", "Payoff", "payoff", ok(k.payoff_ratio) ? num(k.payoff_ratio, 2) : "—"),
    m("exp_usd", t("stats.kpi.exp_usd"), "expectancy", susd(k.expectancy_usdt), toneOf(k.expectancy_usdt, 0.005), { est: true }),
    m("exp_pct", t("stats.kpi.exp_pct"), "expectancy", spctU(k.expectancy_pct, 2), toneOf(k.expectancy_pct, 0.005), { est: true }),
    m("best", t("stats.kpi.best"), "best_trade", k.best_trade ? susd(k.best_trade.net) : "—", toneOf(k.best_trade?.net, 0.005), { sub: ref(k.best_trade) }),
    m("worst", t("stats.kpi.worst"), "worst_trade", k.worst_trade ? susd(k.worst_trade.net) : "—", toneOf(k.worst_trade?.net, 0.005), { sub: ref(k.worst_trade) }),
    m("hold_mean", t("stats.kpi.hold_mean"), "hold_mean", fmtDuration(k.hold_mean_s)),
    m("hold_median", t("stats.kpi.hold_median"), "hold_median", fmtDuration(k.hold_median_s)),
    m("streaks", t("stats.kpi.streaks"), "streaks", k.trades ? `${k.max_win_streak} / ${k.max_loss_streak}` : "—"),
    m("tim", t("stats.kpi.tim"), "time_in_market_pct", pctU(k.time_in_market_pct, 1)),
    m("turnover", t("stats.kpi.turnover"), "turnover", usd(k.turnover, 0), "", { est: true }),
    m("fees_pct", t("stats.kpi.fees_pct"), "fees_pct_of_gross_profit", pctU(k.fees_pct_of_gross_profit, 1), "", { est: true }),
    m("gross", t("stats.kpi.gross"), "gross_pnl", susd(k.gross_pnl), toneOf(k.gross_pnl, 0.005)),
    m("sortino", "Sortino", "sortino", ok(e.sortino) ? num(e.sortino, 2) : "—", toneOf(e.sortino, 0.005), { why: ok(e.sortino) ? undefined : NEED_SORTINO }),
    m("calmar", "Calmar", "calmar", ok(e.calmar) ? num(e.calmar, 2) : "—", toneOf(e.calmar, 0.005), { why: ok(e.calmar) ? undefined : NEED_CALMAR }),
    m("vol", t("stats.kpi.vol"), "volatility_pct", pctU(e.volatility_pct, 1), "", { why: ok(e.volatility_pct) ? undefined : NEED_SHARPE }),
    m("best_day", t("stats.kpi.best_day"), "best_day", day(e.best_day), toneOf(e.best_day?.ret_pct, 0.005)),
    m("worst_day", t("stats.kpi.worst_day"), "worst_day", day(e.worst_day), toneOf(e.worst_day?.ret_pct, 0.005)),
    m("sides", t("stats.kpi.sides"), "side_counts", `${ls.long} / ${ls.short}`),
  ];
}

// ---------------------------------------------------------------- сравнение с предыдущим окном
export type CmpView =
  | { kind: "chip"; dir: "up" | "down" | "flat"; good: "pos" | "neg" | ""; text: string; title: string }
  | { kind: "na"; title: string };

const CMP_REASON: Record<string, string> = {
  range_too_short: t("stats.cmp.reason.range_too_short"),
  all_range: t("stats.cmp.reason.all_range"),
  no_previous_data: t("stats.cmp.reason.no_previous_data"),
};

export function cmpView(r: StatsResponse, spec: CmpSpec | undefined): CmpView | null {
  if (!spec || !r.available) return null;
  const c = r.comparison;
  if (!c || !c.available) return { kind: "na", title: CMP_REASON[c?.reason ?? ""] ?? t("stats.cmp.na") };
  const row = (spec.group === "kpis" ? c.kpis : c.equity)?.[spec.field];
  if (!row || !ok(row.delta)) return { kind: "na", title: t("stats.cmp.no_prev_value") };
  const d = row.delta;
  const eps = spec.kind === "count" ? 0.5 : 0.005;
  const flat = Math.abs(d) < eps;
  const dir = flat ? "flat" : d > 0 ? "up" : "down";
  const good = flat || spec.better === "none" ? "" : d > 0 ? "pos" : "neg";
  const text = flat ? t("stats.cmp.unchanged")
    : spec.kind === "pp" ? `${snum(d, 1)} ${PP}` : spec.kind === "usd" ? susd(d) : spec.kind === "count" ? snum(d, 0) : snum(d, 2);
  const prev = (x: Num) => (spec.kind === "pp" ? pctU(x, 2) : spec.kind === "usd" ? usd(x) : num(x, spec.kind === "count" ? 0 : 2));
  const period = c.prev_start && c.prev_end ? ` (${ddmmyy(c.prev_start)} – ${ddmmyy(c.prev_end)})` : "";
  return { kind: "chip", dir, good, text, title: t("stats.cmp.title", { period, prev: prev(row.prev), cur: prev(row.cur) }) };
}
