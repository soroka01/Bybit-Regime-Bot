// Плечо в бэктестах дашборда: границы, нелинейная шкала слайдера, пресеты и подсказка о риске ликвидации.
// Чистая логика без DOM (тесты: tests/leverage.test.ts). Константы зеркалят сервер (web/schemas.py, core/engine.py);
// совпадение проверяет tests/test_leverage_market.py::test_ts_constants_match_server.
import { t } from "./i18n";
import { num } from "./ui/fmt";

const MIN_LEVERAGE = 1;
export const MAX_LEVERAGE = 100;         // максимум Bybit для BTCUSDT
export const BOT_MAX_LEVERAGE = 3;       // живой бот ограничен 3× (trading.config.MAX_LEVERAGE) — здесь только для подсказок
export const MMR = 0.005;                // поддерживающая маржа 0.5 % (core.engine.MAINT_MARGIN_RATE)
export const LEV_PRESETS: readonly number[] = [1, 2, 3, 5, 10, 20, 50, 100];
const WARN_AT = 10;               // янтарный с 10×
const DANGER_AT = 25;             // красный с 25×

/** Участки шкалы: [от, до, шаг]. Низкие плечи остаются точными: 0.5 до 5×, 1 до 10×, 5 до 25×, 25 до 100×. */
const SEGMENTS: readonly (readonly [number, number, number])[] = [[1, 5, 0.5], [5, 10, 1], [10, 25, 5], [25, 100, 25]];

/** Значения плеча, соответствующие целым позициям слайдера: 1, 1.5 … 5, 6 … 10, 15, 20, 25, 50, 75, 100. */
export const LEV_STOPS: readonly number[] = (() => {
  const out: number[] = [];
  for (const [from, to, step] of SEGMENTS) {
    const n = Math.round((to - from) / step);
    for (let i = 0; i < n; i++) out.push(from + i * step);
  }
  out.push(MAX_LEVERAGE);
  return out;
})();
export const LEV_POS_MAX = LEV_STOPS.length - 1;

const finite = (v: number, d: number) => (Number.isFinite(v) ? v : d);

/** Допустимое плечо: [1; 100], два знака после запятой (NaN -> 1). */
export const clampLev = (v: number): number => Math.round(Math.min(MAX_LEVERAGE, Math.max(MIN_LEVERAGE, finite(v, MIN_LEVERAGE))) * 100) / 100;

/** Позиция слайдера (целая, 0…LEV_POS_MAX) -> плечо. */
export const levFromPos = (pos: number): number => LEV_STOPS[Math.min(LEV_POS_MAX, Math.max(0, Math.round(finite(pos, 0))))]!;

/** Плечо -> ближайшая позиция слайдера (для значений вне шкалы, например 7.3, — ближайшая отметка). */
export function posFromLev(lev: number): number {
  const v = clampLev(lev);
  let best = 0;
  for (let i = 1; i < LEV_STOPS.length; i++) if (Math.abs(LEV_STOPS[i]! - v) < Math.abs(LEV_STOPS[best]! - v)) best = i;
  return best;
}

/** Движение цены против позиции (в %), после которого срабатывает ликвидация: (1/плечо − MMR) · 100. */
export const liqMovePct = (lev: number): number => (1 / clampLev(lev) - MMR) * 100;

type RiskLevel = "ok" | "warn" | "danger";
export const riskLevel = (lev: number): RiskLevel => (lev >= DANGER_AT ? "danger" : lev >= WARN_AT ? "warn" : "ok");

export const liqHintText = (lev: number): string => t("lab.leverage.liqHint", { pct: num(liqMovePct(lev), 1) });

/** «бот ограничен 3×» — только если плечо выше потолка живого бота. */
export const botLimitNote = (lev: number): string | null => (lev > BOT_MAX_LEVERAGE ? t("lab.leverage.botLimit", { n: BOT_MAX_LEVERAGE }) : null);
/** То же для текста «для бота» (комментарий в протоколе копирования): всегда по-русски, на любом языке интерфейса. */
export const botLimitNoteRu = (lev: number): string | null => (lev > BOT_MAX_LEVERAGE ? `бот ограничен ${BOT_MAX_LEVERAGE}×` : null);
