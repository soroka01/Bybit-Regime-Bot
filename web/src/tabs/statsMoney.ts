// Вкладка «Статистика»: суммы в долларах по закрытым сделкам, итоги окна, результат сделки (чистая логика).
import { t } from "../i18n";
import type { Num, StatsResponse, StatsTradeRow } from "../types";
import { isNum as ok, spctU, susd, toneOf } from "../ui/fmt";

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
export function moneyLine(s: MoneySummary, label = t("stats.money.closed")): string {
  if (!s.count) return t("stats.money.none");
  return t("stats.money.line", { label, n: s.count, profit: susd(s.profit), loss: susd(s.loss), net: susd(s.net) });
}

/**
 * Итоги окна ответа /api/stats по ВСЕМ сделкам окна (kpis; строки таблицы обрезаны до 500). Нет сумм в kpis — null (по обрезанным строкам не считаем).
 */
export function periodSummary(r: StatsResponse | null | undefined): MoneySummary | null {
  if (!r?.available || !r.kpis) return null;
  const k = r.kpis;
  if (!ok(k.profit_sum_usdt) || !ok(k.loss_sum_usdt) || !ok(k.net_pnl)) return null;
  return { count: k.trades, wins: k.wins, losses: k.losses, profit: k.profit_sum_usdt, loss: k.loss_sum_usdt, net: k.net_pnl };
}

/** Результат сделки: доллары (главное) и «% счёта» (ret_account_pct) — вместе. Нет кошелька на входе — «% счёта: —». */
export function resultText(net: Num | undefined, accountPct: Num | undefined): { usd: string; pct: string; tone: "pos" | "neg" | "" } {
  return { usd: susd(net), pct: ok(accountPct) ? t("stats.money.of_account", { v: spctU(accountPct, 2) }) : t("stats.money.account_na"), tone: toneOf(net, 0.005) };
}

/** Нереализованный результат открытой позиции в % кошелька сейчас (проценты — в «процентных» единицах ответа); null — нет данных. */
export const openAccountPct = (upnl: Num | undefined, wallet: Num | undefined): number | null => (ok(upnl) && ok(wallet) && wallet > 0 ? (upnl / wallet) * 100 : null);

/** Окрашивает результат по ЗНАКУ P/L и никогда по стороне: класс ячейки зависит только от net. */
export const resultTone = (r: Pick<StatsTradeRow, "net">): "pos" | "neg" | "" => toneOf(r.net, 0.005);
