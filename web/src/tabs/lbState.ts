// Состояние вкладки «Лучшие стратегии»: явный автомат idle | loading | cached | running | error.
// Любое изменение идёт через store.set(), который применяет патч и ровно один раз вызывает onChange (status() + renderList()).
import type { LbItem, LbPlan } from "../types";
import { utcShort } from "../ui/fmt";

export type Phase = "idle" | "loading" | "cached" | "running" | "error";

export type Entry =
  | { k: "item"; item: LbItem }
  | { k: "error"; error: string }
  | { k: "pending" }
  | { k: "skip"; skip: string }
  | { k: "none" }
  | { k: "busy" };

export interface Retry { k: number; n: number; name: string; waitedS?: number }

export interface LbModel {
  phase: Phase;
  plan: LbPlan | null;
  entries: Map<string, Entry>;
  order: string[];
  done: number;
  total: number;
  note: string;
  readyCount: number;
  missingCount: number;
  dataLast: string | null;
  retry: Retry | null;
  error: string;
}

export const initialModel = (): LbModel => ({
  phase: "idle", plan: null, entries: new Map(), order: [], done: 0, total: 0, note: "",
  readyCount: 0, missingCount: 0, dataLast: null, retry: null, error: "",
});

/** Допустимые переходы. Любое состояние может уйти в loading/error/idle, но «считаю» — только из готового кэша (или ошибки). */
const NEXT: Record<Phase, readonly Phase[]> = {
  idle: ["loading", "error"],
  loading: ["cached", "error", "idle", "loading"],
  cached: ["running", "loading", "error", "idle", "cached"],
  running: ["cached", "loading", "error", "idle", "running"],
  error: ["loading", "idle", "running", "cached", "error"],
};
export const canTransition = (from: Phase, to: Phase): boolean => from === to || NEXT[from].includes(to);

export type LbStore = ReturnType<typeof createLbStore>;

/**
 * onChange вызывается после КАЖДОГО успешного set(); исключение в нём перехватывается (состояние уже записано),
 * поэтому ошибка отрисовки никогда не оставляет вкладку «зависшей» в running.
 */
export function createLbStore(onChange: (m: LbModel) => void, onRenderError: (e: unknown) => void = (e) => console.error("lb render failed", e)) {
  const m = initialModel();
  const store = {
    model: m,
    /** Применяет патч. Недопустимый переход фазы отвергается (возвращает false), остальное состояние не меняется. */
    set(patch: Partial<LbModel>): boolean {
      if (patch.phase && !canTransition(m.phase, patch.phase)) {
        console.warn(`lb: недопустимый переход ${m.phase} -> ${patch.phase}`);
        return false;
      }
      Object.assign(m, patch);
      try { onChange(m); } catch (e) { onRenderError(e); }
      return true;
    },
    /** Сброс данных под новые настройки (в loading): планы/карточки старых настроек больше недействительны. */
    resetFor(phase: Phase = "loading"): boolean {
      return store.set({
        phase, plan: null, entries: new Map(), order: [], done: 0, total: 0, note: "", readyCount: 0, missingCount: 0,
        dataLast: null, retry: null, error: "",
      });
    },
  };
  return store;
}

const fmtDataLast = (s: string | null) => (s ? utcShort(s) + " UTC" : "—");

export function statusText(m: LbModel, fmtLast: (s: string | null) => string = fmtDataLast): string {
  switch (m.phase) {
    case "loading": return "Загрузка кэша…";
    case "error": return m.error || "Ошибка";
    case "running": {
      const base = `Считаю ${Math.min(m.done + 1, m.total)} из ${m.total}…`;
      if (!m.retry) return base;
      const w = m.retry.waitedS != null ? `, ждали ${Math.round(m.retry.waitedS)} с` : "";
      return `${base} ожидаю сервер, попытка ${m.retry.k}/${m.retry.n} (${m.retry.name}${w})`;
    }
    default: {
      const cachedN = m.readyCount - m.missingCount;
      if (m.note) return m.note;
      if (!m.readyCount) return "";
      if (!m.missingCount) return `Показано из кэша · данные от ${fmtLast(m.dataLast)}`;
      if (cachedN > 0) return `Из кэша: ${cachedN} из ${m.readyCount}. Осталось досчитать: ${m.missingCount}.`;
      return "В кэше ничего нет для этих настроек — нажмите «Запустить».";
    }
  }
}

export function runLabel(m: LbModel): string {
  if (m.phase === "running") return "Считаю…";
  if (m.readyCount > 0 && m.missingCount === 0) return "Показано из кэша";
  if (m.readyCount > 0 && m.missingCount < m.readyCount) return `Досчитать недостающие (${m.missingCount})`;
  return "Запустить";
}

export const runDisabled = (m: LbModel): boolean =>
  m.phase === "running" || (m.readyCount > 0 && m.missingCount === 0);

/** Сколько стратегий осталось досчитать (ожидают, не рассчитаны, ошибка, «сервер занят»). */
export const countMissing = (entries: Map<string, Entry>): number =>
  [...entries.values()].filter((e) => e.k === "pending" || e.k === "none" || e.k === "error" || e.k === "busy").length;
