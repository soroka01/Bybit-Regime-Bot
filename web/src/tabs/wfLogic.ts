// Чистая логика вкладки «Walk-forward»: оценка строки (бейдж), сводка по окнам, матрица «окно × вариант». На данные и запросы не влияет.
import type { Num, WfRow, WfWindow } from "../types";

type WfTone = "ok" | "warn" | "bad" | "off";
interface WfVerdict { key: "stable" | "unstable" | "few" | "loss"; text: string; tone: WfTone; glyph: string; hint: string }

const WF_MIN_TRADES = 30;
const WF_STABLE_SHARE = 0.6;

/**
 * Быстрая оценка варианта по его строке таблицы (не серверный вердикт!): убыточный OOS; мало сделок; доля прибыльных окон ≥ 60 % и средний месяц > 0 — стабильный;
 * иначе — нестабильный (результат держится на немногих окнах).
 */
export function wfVerdict(r: Pick<WfRow, "total_return" | "trades" | "windows_positive" | "avg_month">): WfVerdict {
  if (r.trades == null || r.trades < WF_MIN_TRADES) return { key: "few", text: "мало сделок", tone: "off", glyph: "○", hint: `меньше ${WF_MIN_TRADES} сделок за весь OOS — выводы статистически слабы` };
  if (r.total_return == null || r.total_return <= 0) return { key: "loss", text: "убыточен", tone: "bad", glyph: "✕", hint: "на склеенном OOS вариант не заработал" };
  if ((r.windows_positive ?? 0) >= WF_STABLE_SHARE && (r.avg_month ?? 0) > 0) return { key: "stable", text: "стабилен", tone: "ok", glyph: "✓", hint: "OOS в плюсе, ≥ 60 % тестовых окон прибыльны, средний месяц > 0" };
  return { key: "unstable", text: "нестабилен", tone: "warn", glyph: "!", hint: "OOS в плюсе, но прибыльных окон меньше 60 % или средний месяц ≤ 0: результат держится на немногих окнах" };
}

/** Оценка отдельного окна по доходности: плюс / минус / нет сделок. */
export const windowTone = (v: Num): "pos" | "neg" | "" => (v == null || v === 0 ? "" : v > 0 ? "pos" : "neg");

interface WindowSpan { count: number; first: string; lastStart: string }

/** Число окон и даты начала первого и последнего тестового окна (ISO-даты как пришли с сервера). */
export function windowSpan(windows: readonly Pick<WfWindow, "test_start">[]): WindowSpan | null {
  const starts = [...new Set(windows.map((w) => w.test_start))].sort();
  return starts.length ? { count: starts.length, first: starts[0]!, lastStart: starts[starts.length - 1]! } : null;
}

interface MatrixRow { key: string; cells: Num[]; positive: number; total: number }

/** Матрица «окно × выбранные варианты»: значения test_return; positive/total — число прибыльных окон из окон с данными. */
export function windowMatrix(keys: readonly string[], windows: readonly WfWindow[], keyOf: (w: WfWindow) => string): { starts: string[]; rows: MatrixRow[] } {
  const starts = [...new Set(windows.map((w) => w.test_start))].sort();
  const rows = keys.map((key) => {
    const cells = starts.map((s) => windows.find((w) => keyOf(w) === key && w.test_start === s)?.test_return ?? null);
    const have = cells.filter((v): v is number => v != null);
    return { key, cells, positive: have.filter((v) => v > 0).length, total: have.length };
  });
  return { starts, rows };
}
