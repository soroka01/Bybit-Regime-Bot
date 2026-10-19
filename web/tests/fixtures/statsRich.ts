// Рукописный «богатый» ответ /api/stats для тестов вкладки: 40 сделок за 90 дней, реконструированная сделка, разные причины, сравнение с предыдущим окном.
// Цифры согласованы между собой только по структуре (это не расчёт сервера): тесты проверяют отрисовку, а не математику.
import type { StatsResponse, StatsTradeRow } from "../../src/types";
import golden from "./stats_all.json";

const g = golden as unknown as StatsResponse;
const DAY = 86400;
export const RICH_NOW = Date.UTC(2026, 5, 1, 1, 0, 0) / 1000;

export function richTrades(n = 40): StatsTradeRow[] {
  const rows: StatsTradeRow[] = [];
  for (let i = 0; i < n; i++) {
    const entry = RICH_NOW - (n - i) * 2 * DAY;
    const exit = entry + 30 * 3600;
    const win = i % 3 !== 0;
    const net = win ? 4 + (i % 5) : -3 - (i % 4);
    const side = i % 2 ? "short" : "long";
    const recon = i === 7;
    rows.push({
      n: i + 1, entry_ts: entry, exit_ts: exit, side, qty: 0.02, entry_price: 100 + i, exit_price: 100 + i + (win ? 2 : -2), entry_notional: 200,
      gross: net + 0.4, fees: 0.4, funding: i % 4 === 0 ? 0 : 0.05, funding_complete: i !== 38, exact: i % 3 !== 1, net, ret_pct: (net / 200) * 100, ret_margin_pct: (net / 100) * 100,
      equity_at_entry: 1000 + i * 3, ret_account_pct: (net / (1000 + i * 3)) * 100,
      hold_s: 30 * 3600, reason: recon ? "стоп (реконструкция)" : i % 8 === 0 ? "закрытие вручную" : "бар", reason_group: recon ? "стоп (реконструкция)" : i % 8 === 0 ? "закрытие вручную" : "бар",
      reconstructed: recon,
      fills: [
        { id: 2 * i + 1, ts: entry, side: side === "long" ? "buy" : "sell", qty: 0.02, price: 100 + i, reason: "бар", fee: 0.2, split: false, synthetic: false },
        { id: recon ? null : 2 * i + 2, ts: exit, side: side === "long" ? "sell" : "buy", qty: 0.02, price: 100 + i + (win ? 2 : -2), reason: recon ? null : "бар", fee: 0.2, split: false, synthetic: recon, ...(recon ? { basis: "stop_level" as const } : {}) },
      ],
    });
  }
  return rows;
}

export function richStats(over: Partial<StatsResponse> = {}): StatsResponse {
  const rows = richTrades();
  const wins = rows.filter((r) => (r.net ?? 0) > 0);
  const losses = rows.filter((r) => (r.net ?? 0) < 0);
  const t0 = RICH_NOW - 90 * DAY;
  const eqT = Array.from({ length: 120 }, (_, i) => t0 + (i * 90 * DAY) / 119);
  const eqV = eqT.map((_, i) => 1000 + i * 1.5 + Math.sin(i / 5) * 8);
  const out = JSON.parse(JSON.stringify(g)) as StatsResponse;
  Object.assign(out, {
    range: "90d", computed_at: RICH_NOW,
    window: { start: t0, effective_start: t0, end: RICH_NOW, start_date: "2026-03-03", end_date: "2026-06-01", length_s: 90 * DAY },
    config: { symbol: "BTCUSDT", env: "mainnet", taker_fee_est: 0.001, leverage: 2, stop_pct: 0.05 },
    equity: { ...g.equity!, eq_start: eqV[0]!, eq_end: eqV[119]!, delta: 190, wallet_return_pct: 19, max_drawdown_pct: -4.2, sharpe: 1.3, sortino: 2.1, calmar: 3.5, starts_before_data: false, daily_points: 90 },
    kpis: {
      ...g.kpis!, trades: rows.length, wins: wins.length, losses: losses.length, breakeven: 0, win_rate_pct: (wins.length / rows.length) * 100, profit_factor: 1.4,
      net_pnl: rows.reduce((a, r) => a + (r.net ?? 0), 0), profit_sum_usdt: wins.reduce((a, r) => a + (r.net ?? 0), 0), loss_sum_usdt: losses.reduce((a, r) => a + (r.net ?? 0), 0), fees_total: 16, funding_total: 1.5, buy_hold_pct: 8,
    },
    open_position: { side: "short", qty: 0.02, entry: 130, mark: 128, upnl: 0.4, notional: 2.56, opened_ts: RICH_NOW - 7200, fees_paid_est: 0.2, funding_est: 0, funding_complete: true, net_est: 0.2, heartbeat: null },
    position_mismatch: null,
    comparison: {
      available: true, prev_start: t0 - 90 * DAY, prev_end: t0,
      kpis: { net_pnl: { cur: 50, prev: 30, delta: 20 }, trades: { cur: 40, prev: 35, delta: 5 }, win_rate_pct: { cur: 66, prev: 60, delta: 6 }, profit_factor: { cur: 1.4, prev: 1.1, delta: 0.3 }, expectancy_usdt: { cur: 1, prev: 0.5, delta: 0.5 }, fees_total: { cur: 16, prev: 14, delta: 2 } },
      equity: { wallet_return_pct: { cur: 19, prev: 11, delta: 8 }, max_drawdown_pct: { cur: -4.2, prev: -6, delta: 1.8 }, sharpe: { cur: 1.3, prev: 0.9, delta: 0.4 } },
    },
    series: {
      equity: { t: eqT, v: eqV, lo: eqV.map((x) => x - 3), hi: eqV.map((x) => x + 3) },
      drawdown: { t: eqT, dd_pct: eqV.map((_, i) => -Math.abs(Math.sin(i / 9)) * 4) },
      pnl_buckets: { kind: "week", rows: Array.from({ length: 13 }, (_, i) => ({ t: t0 + i * 7 * DAY, n: i % 4 === 0 ? 0 : 3, wins: 2, net: i % 4 === 0 ? 0 : (i % 2 ? -4 : 9) })) },
      calendar: rows.map((r) => ({ date: new Date(r.exit_ts * 1000).toISOString().slice(0, 10), net: r.net, trades: 1 })),
      daily_returns: Array.from({ length: 30 }, (_, i) => ({ date: new Date((RICH_NOW - (30 - i) * DAY) * 1000).toISOString().slice(0, 10), ret_pct: Math.sin(i) })),
      histogram: { edges: [-6, -4, -2, 0, 2, 4, 6, 8, 10], counts: [2, 4, 6, 0, 3, 9, 10, 6] },
    },
    breakdown: {
      by_reason: [
        { key: "бар", count: 34, win_rate_pct: 70, net: 60, avg_net: 1.76 },
        { key: "закрытие вручную", count: 5, win_rate_pct: 40, net: -4, avg_net: -0.8 },
        { key: "стоп (реконструкция)", count: 1, win_rate_pct: 0, net: -3, avg_net: -3 },
      ],
      by_side: [{ key: "long", count: 20, win_rate_pct: 70, net: 40, avg_net: 2 }, { key: "short", count: 20, win_rate_pct: 60, net: 13, avg_net: 0.65 }],
    },
    trades: { total: rows.length, truncated: false, rows },
    reconciliation: { wallet_delta: 190, bot_pnl_est: 53, unexplained: 137, reason: null, flat_at_start: true },
    warnings: [
      { code: "reconstructed_trades", level: "warn", text: "1 сделок реконструированы по событию «Позиция исчезла» (стоп/ликвидация): цена выхода — оценка.", count: 1 },
      { code: "funding_stale", level: "warn", text: "Локальные данные фандинга устарели или отсутствуют: для 1 сделок фандинг неизвестен.", trades: 1, funding_coverage_until: RICH_NOW - 3 * DAY },
      { code: "equity_gaps", level: "info", text: "В окне есть разрывы equity > 15 мин (бот не работал): 2 шт., всего 3.0 ч.", count: 2, total_s: 10800 },
    ],
  } satisfies Partial<StatsResponse>);
  return Object.assign(out, over);
}
