// Чистая логика таблицы сделок: фильтр (Все / Лонг / Шорт / Прибыльные / Убыточные) и выгрузка в CSV (на клиенте, без сети).
import type { TradeRow } from "../types";

export type TradeFilter = "all" | "long" | "short" | "win" | "loss";

export const TRADE_FILTERS: { key: TradeFilter; label: string }[] = [
  { key: "all", label: "Все" },
  { key: "long", label: "Лонг" },
  { key: "short", label: "Шорт" },
  { key: "win", label: "Прибыльные" },
  { key: "loss", label: "Убыточные" },
];

const matches = (r: TradeRow, f: TradeFilter): boolean => {
  switch (f) {
    case "long": return r.side === "long";
    case "short": return r.side === "short";
    case "win": return r.ret != null && r.ret > 0;
    case "loss": return r.ret != null && r.ret < 0;
    default: return true;
  }
};

export const filterTrades = (rows: readonly TradeRow[], f: TradeFilter): TradeRow[] => (f === "all" ? [...rows] : rows.filter((r) => matches(r, f)));

export function tradeCounts(rows: readonly TradeRow[]): Record<TradeFilter, number> {
  const c: Record<TradeFilter, number> = { all: rows.length, long: 0, short: 0, win: 0, loss: 0 };
  for (const r of rows) for (const f of ["long", "short", "win", "loss"] as const) if (matches(r, f)) c[f]++;
  return c;
}

/** Ячейка CSV (RFC 4180): кавычки при запятой, кавычке или переводе строки; кавычки удваиваются. null/NaN — пусто. */
export function csvCell(v: string | number | null | undefined): string {
  if (v == null || (typeof v === "number" && !Number.isFinite(v))) return "";
  const s = String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Ячейка CSV для текста (в т.ч. от сервера): защита от формул (=, +, -, @, таб, CR в начале) + RFC 4180. Числа — через csvCell: их минус не экранируется. */
export const csvText = (s: string | null | undefined): string => csvCell(s != null && /^[=+\-@\t\r]/.test(s) ? "'" + s : s);

const CSV_HEADER = ["entry", "exit", "side", "return", "bars", "entry_price", "exit_price"] as const;

/** CSV сделок: машинный формат (точка в числах, доходность — доля, время — как пришло от сервера, UTC), разделитель строк CRLF. */
export function tradesToCsv(rows: readonly TradeRow[]): string {
  const lines: string[] = [CSV_HEADER.join(",")];
  for (const r of rows) lines.push([csvText(r.entry), csvText(r.exit), csvText(r.side), csvCell(r.ret), csvCell(r.bars), csvCell(r.entry_price), csvCell(r.exit_price)].join(","));
  return lines.join("\r\n") + "\r\n";
}

export const csvFileName = (parts: (string | number | null | undefined)[]): string =>
  parts.filter((p) => p != null && p !== "").join("_").replace(/[^\w.\-а-яА-Я]+/g, "-").replace(/-+/g, "-") + ".csv";
