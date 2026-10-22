// Вкладка «Статистика»: предупреждения качества данных, заметка о комиссии, причины недоступной сверки кошелька (чистая логика).
// Сервер присылает у предупреждения стабильный `code`, параметры (count, trades, total_s, …) и свой русский текст. Подпись, подсказку и текст собираем
// здесь из кода и параметров (на языке интерфейса); серверный текст остаётся запасным — для неизвестного кода или когда нужного параметра нет.
import { t } from "../i18n";
import type { Num, StatsWarning } from "../types";
import { isNum as ok, num } from "../ui/fmt";
import { stamp } from "../ui/fmtTime";

export interface WarnChip { code: string; level: "warn" | "info"; label: string; text: string; hint?: string }

const withCount = (base: string, w: StatsWarning): string => `${base}${w.count != null ? `: ${w.count}` : ""}`;
const WARN_LABEL: Record<string, (w: StatsWarning) => string> = {
  starts_before_data: () => t("stats.warn.label.starts_before_data"),
  few_trades: (w) => `${t("stats.warn.label.few_trades")}${w.count != null ? ` (${w.count})` : ""}`,
  funding_stale: () => t("stats.warn.label.funding_stale"),
  candles_missing: () => t("stats.warn.label.candles_missing"),
  position_mismatch: () => t("stats.warn.label.position_mismatch"),
  reconstructed_trades: (w) => withCount(t("stats.warn.label.reconstructed_trades"), w),
  equity_gaps: (w) => withCount(t("stats.warn.label.equity_gaps"), w),
  bad_fills: (w) => withCount(t("stats.warn.label.bad_fills"), w),
};

export const FUNDING_HINT = t("stats.warn.hint.funding");
const WARN_HINT: Record<string, string> = {
  starts_before_data: t("stats.warn.hint.starts_before_data"),
  few_trades: t("stats.warn.hint.few_trades"),
  funding_stale: FUNDING_HINT,
  candles_missing: t("stats.warn.hint.candles_missing"),
  position_mismatch: t("stats.warn.hint.position_mismatch"),
  reconstructed_trades: t("stats.warn.hint.reconstructed_trades"),
  equity_gaps: t("stats.warn.hint.equity_gaps"),
  bad_fills: t("stats.warn.hint.bad_fills"),
};

const fmtUntil = (ts: Num | undefined): string => (ok(ts) ? stamp(ts) + " UTC" : t("stats.no_data"));

/** «Фандинг для N сделок неизвестен и принят за 0.» (N не передан — «для части сделок»). */
export const fundingSkipped = (trades: number | undefined): string =>
  trades != null ? t("stats.funding.skipped", { n: trades }) : t("stats.funding.skipped_some");

/** Текст предупреждения по коду и параметрам; пустая строка — собрать нельзя (неизвестный код или нет нужного параметра), тогда берётся серверный. */
function warnText(w: StatsWarning): string {
  switch (w.code) {
    case "starts_before_data": return t("stats.warn.text.starts_before_data");
    case "few_trades": return w.count != null ? t("stats.warn.text.few_trades", { n: w.count }) : "";
    case "funding_stale":    // серверный текст отсылает к несуществующей вкладке «Данные», поэтому пишем свой
      return w.trades != null
        ? t("stats.warn.text.funding_stale", { n: w.trades, until: fmtUntil(w.funding_coverage_until) })
        : t("stats.warn.text.funding_stale_some", { until: fmtUntil(w.funding_coverage_until) });
    case "candles_missing":
      // есть поле error — свечей нет совсем (его текст сервера дописывается как есть), есть candles_until — свечи не покрывают окно
      if ("error" in w) return t("stats.warn.text.candles_none") + (w.error ? ` (${w.error})` : "");
      return "candles_until" in w ? t("stats.warn.text.candles_gap") : "";
    case "position_mismatch": return t("stats.warn.text.position_mismatch");
    case "reconstructed_trades": return w.count != null ? t("stats.warn.text.reconstructed_trades", { n: w.count }) : "";
    case "equity_gaps": return w.count != null && ok(w.total_s) ? t("stats.warn.text.equity_gaps", { n: w.count, hours: (w.total_s / 3600).toFixed(1) }) : "";
    case "bad_fills": return w.count != null ? t("stats.warn.text.bad_fills", { n: w.count }) : "";
    default: return "";
  }
}

/** Предупреждения ответа -> чипы: сначала «warn», затем «info». Для funding_stale текст пишем сами (серверный отсылает к несуществующей вкладке «Данные»). */
export function warningChips(ws: readonly StatsWarning[] | undefined): WarnChip[] {
  const chips = (ws ?? []).map((w): WarnChip => {
    const text = warnText(w) || w.text;
    return { code: w.code, level: w.level === "info" ? "info" : "warn", label: WARN_LABEL[w.code]?.(w) ?? String(w.code), text, hint: WARN_HINT[w.code] };
  });
  return [...chips.filter((c) => c.level === "warn"), ...chips.filter((c) => c.level === "info")];
}

// ---------------------------------------------------------------- комиссия
export const BACKTEST_TAKER = 0.001;   // = core.engine.DEFAULT_PERP_FEE (проверяется tests/test_leverage_market.py)
const fmtRate = (r: number): string => `${num(r * 100, r * 100 < 0.1 ? 3 : 2)}%`;
/** Информационная заметка, если реальная ставка тейкера отличается от ставки бэктестов; иначе null. */
export function feeNote(taker: Num | undefined): string | null {
  if (!ok(taker) || Math.abs(taker - BACKTEST_TAKER) < 1e-7) return null;
  return t("stats.fee_note", { real: fmtRate(taker), bt: fmtRate(BACKTEST_TAKER) });
}

// ---------------------------------------------------------------- сверка кошелька
export const RECON_REASON: Record<string, string> = {
  position_mismatch: t("stats.recon.reason.position_mismatch"),
  no_start_price: t("stats.recon.reason.no_start_price"),
  no_start_trade: t("stats.recon.reason.no_start_trade"),
};
