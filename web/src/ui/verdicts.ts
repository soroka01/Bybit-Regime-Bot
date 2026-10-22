// Вердикты стратегий: порядок ярусов, подписи, пороги. Единый источник для бейджей, рейтинга, подбора параметров.
// Тексты — в словаре (i18n/messages/leaderboard.ts, «leaderboard.verdict.*»). Пороги в подсказках (Sharpe OOS > 0,5, зелёных месяцев ≥ 40%, окно OOS < 90 дн.)
// задаются константами ниже и дублируют core/optimize.py: расхождение ловит tests/verdictsContract.test.ts (читает константы из исходника сервера
// и проверяет итоговые тексты обоих языков, а не сам исходный код).
import { t } from "../i18n";
import type { Verdict } from "../types";
import { num } from "./fmt";

interface VerdictInfo { text: string; tone: string; /** знак в бейдже (смысл не только цветом) */ glyph: string; hint: string; warnTitle: string; warnBody: string }

/** Порог Sharpe OOS для вердикта «устойчива» (core/optimize.py: sharpe > 0.5). */
export const SHARPE_MIN = 0.5;
/** Минимальная доля зелёных месяцев в % (core/optimize.py: green_months >= 0.4). */
export const GREEN_MONTHS_MIN_PCT = 40;
/** Минимальная длина окна OOS в днях (core/optimize.py: MIN_OOS_DAYS); короче — вердикт «мало сделок». */
export const MIN_OOS_DAYS = 90;

const thresholds = { sharpe: num(SHARPE_MIN, 1), green: GREEN_MONTHS_MIN_PCT };

export const VERDICTS: Record<Verdict, VerdictInfo> = {
  robust: {
    text: t("leaderboard.verdict.robust.text"), tone: "ok", glyph: "✓",
    hint: t("leaderboard.verdict.robust.hint", thresholds),
    warnTitle: "", warnBody: "",
  },
  overfit: {
    text: t("leaderboard.verdict.overfit.text"), tone: "warn", glyph: "!",
    hint: t("leaderboard.verdict.overfit.hint", thresholds),
    warnTitle: t("leaderboard.verdict.overfit.warn_title"),
    warnBody: t("leaderboard.verdict.overfit.warn_body"),
  },
  few: {
    text: t("leaderboard.verdict.few.text"), tone: "off", glyph: "○", hint: t("leaderboard.verdict.few.hint"),
    warnTitle: t("leaderboard.verdict.few.warn_title"),
    warnBody: t("leaderboard.verdict.few.warn_body"),
  },
  loss: {
    text: t("leaderboard.verdict.loss.text"), tone: "bad", glyph: "✕", hint: t("leaderboard.verdict.loss.hint"),
    warnTitle: t("leaderboard.verdict.loss.warn_title"),
    warnBody: t("leaderboard.verdict.loss.warn_body"),
  },
};

/** Порядок ярусов рейтинга: устойчивые > подгонка > мало сделок > убыточные (эталон — tests/test_optimize.py и web/tests/fixtures/rank.json). */
export const VERDICT_ORDER: readonly Verdict[] = ["robust", "overfit", "few", "loss"];

/** Ярус вердикта (меньше — лучше). Неизвестный/пустой вердикт — в самый конец, без исключений. */
export const tierOf = (v: unknown): number => {
  const i = VERDICT_ORDER.indexOf(v as Verdict);
  return i < 0 ? VERDICT_ORDER.length : i;
};

const UNKNOWN: VerdictInfo = { text: t("leaderboard.verdict.unknown.text"), tone: "off", glyph: "?", hint: t("leaderboard.verdict.unknown.hint"), warnTitle: "", warnBody: "" };
export const verdictInfo = (v: unknown): VerdictInfo =>
  (typeof v === "string" && Object.prototype.hasOwnProperty.call(VERDICTS, v) ? VERDICTS[v as Verdict] : UNKNOWN);

/** «✓ устойчива → ! подгонка → ○ мало сделок → ✕ убыточна» для пояснения порядка в рейтинге. */
export const ORDER_TEXT = t("leaderboard.verdict.order");
