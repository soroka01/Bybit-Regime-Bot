// Вкладка «Статистика»: фильтры сделок, счётчики, CSV и подписи базы цены исполнения (чистая логика).
import type { StatsTradeRow } from "../types";
import { isoUtc } from "../ui/fmtTime";
import { csvCell, csvText } from "./tradesLogic";

export { csvText };

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

export const ST_CSV_HEADER = [
  "n", "entry_utc", "exit_utc", "side", "qty", "entry_price", "exit_price", "gross", "fees_est", "funding_est", "net", "ret_pct", "ret_margin_pct",
  "hold_s", "reason", "reconstructed", "funding_complete", "equity_at_entry", "ret_account_pct",
] as const;
/** CSV сделок: машинный формат (точка в числах, проценты — в процентах, время — ISO UTC), разделитель строк CRLF. */
export function stTradesToCsv(rows: readonly StatsTradeRow[]): string {
  const lines: string[] = [ST_CSV_HEADER.join(",")];
  for (const r of rows) {
    lines.push([
      csvCell(r.n), csvText(isoUtc(r.entry_ts)), csvText(isoUtc(r.exit_ts)), csvText(r.side), csvCell(r.qty), csvCell(r.entry_price), csvCell(r.exit_price), csvCell(r.gross),
      csvCell(r.fees), csvCell(r.funding), csvCell(r.net), csvCell(r.ret_pct), csvCell(r.ret_margin_pct), csvCell(r.hold_s),
      csvText(r.reason), r.reconstructed ? "1" : "0", r.funding_complete ? "1" : "0", csvCell(r.equity_at_entry), csvCell(r.ret_account_pct),
    ].join(","));
  }
  return lines.join("\r\n") + "\r\n";
}

const FILL_BASIS: Record<string, string> = {
  stop_level: "по стоп-уровню", candle_close: "по закрытию свечи", candle_open: "по открытию свечи", entry: "по цене входа",
};
export const fillBasisText = (b: string | undefined): string => (b ? (FILL_BASIS[b] ?? b) : "");
