// Вкладка «Статистика»: предупреждения качества данных, заметка о комиссии, причины недоступной сверки кошелька (чистая логика; barrel — statsLogic.ts).
import type { Num, StatsWarning } from "../types";
import { isNum as ok, num } from "../ui/fmt";
import { stamp } from "../ui/fmtTime";

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
