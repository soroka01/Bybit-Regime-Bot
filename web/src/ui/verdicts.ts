// Вердикты стратегий: порядок ярусов, подписи, пороги. Единый источник для бейджей, рейтинга, подбора параметров.
// TODO: тексты порогов (Sharpe > 0,5, зелёных месяцев ≥ 40%, минимум сделок и т.д.) шаблонизировать из /api/meta,
// чтобы не расходились с core.optimize на сервере.
import type { Verdict } from "../types";

interface VerdictInfo { text: string; tone: string; /** знак в бейдже (смысл не только цветом) */ glyph: string; hint: string; warnTitle: string; warnBody: string }

export const VERDICTS: Record<Verdict, VerdictInfo> = {
  robust: {
    text: "устойчива", tone: "ok", glyph: "✓",
    hint: "OOS в плюсе, сделок достаточно, Sharpe OOS > 0,5, средний месяц > 0, зелёных месяцев ≥ 40% и результат не намного хуже IS",
    warnTitle: "", warnBody: "",
  },
  overfit: {
    text: "подозрение на подгонку", tone: "warn", glyph: "!",
    hint: "OOS в плюсе, но заметно слабее IS, Sharpe OOS ≤ 0,5 или на подборе (IS) параметры не заработали",
    warnTitle: "Подозрение на подгонку. ",
    warnBody: "На OOS результат заметно хуже, чем на IS, Sharpe OOS низкий или нет стабильных зелёных месяцев. Не доверяйте цифрам IS.",
  },
  few: {
    text: "мало сделок", tone: "off", glyph: "○", hint: "слишком мало сделок для выводов",
    warnTitle: "Слишком мало сделок для выводов. ",
    warnBody: "На OOS-периоде сделок меньше минимума — цифры OOS статистически ничего не доказывают.",
  },
  loss: {
    text: "убыточна OOS", tone: "bad", glyph: "✕", hint: "На out-of-sample периоде стратегия не заработала",
    warnTitle: "Лучшая комбинация убыточна вне выборки. ",
    warnBody: "На IS она выглядит хорошо, но на OOS не заработала — скорее всего, это подгонка под историю.",
  },
};

/** Порядок ярусов рейтинга: устойчивые > подгонка > мало сделок > убыточные (зеркало core.optimize.rank_items). */
export const VERDICT_ORDER: readonly Verdict[] = ["robust", "overfit", "few", "loss"];

/** Ярус вердикта (меньше — лучше). Неизвестный/пустой вердикт — в самый конец, без исключений. */
export const tierOf = (v: unknown): number => {
  const i = VERDICT_ORDER.indexOf(v as Verdict);
  return i < 0 ? VERDICT_ORDER.length : i;
};

const UNKNOWN: VerdictInfo = { text: "неизвестно", tone: "off", glyph: "?", hint: "неизвестный вердикт", warnTitle: "", warnBody: "" };
export const verdictInfo = (v: unknown): VerdictInfo =>
  (typeof v === "string" && Object.prototype.hasOwnProperty.call(VERDICTS, v) ? VERDICTS[v as Verdict] : UNKNOWN);

/** «✓ устойчива → ! подгонка → ○ мало сделок → ✕ убыточна» для пояснения порядка в рейтинге. */
export const ORDER_TEXT = "✓ устойчива → ! подгонка → ○ мало сделок → ✕ убыточна";
