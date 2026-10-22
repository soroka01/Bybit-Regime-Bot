// Состояние вкладки «Лучшие стратегии»: явный автомат idle | loading | cached | running | error.
// Любое изменение идёт через store.set(), который применяет патч и ровно один раз вызывает onChange (status() + renderList()).
import { t } from "../i18n";
import type { LbItem, LbPlan } from "../types";
import { utcShort } from "../ui/fmt";

export type Phase = "idle" | "loading" | "cached" | "running" | "error";

export type Entry =
  | { k: "item"; item: LbItem }
  | { k: "error"; error: string }
  | { k: "pending" }
  | { k: "skip"; skip: string; code?: string }
  | { k: "none" }
  | { k: "busy" };

interface Retry { k: number; n: number; name: string; waitedS?: number }

interface LbModel {
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
    case "loading": return t("leaderboard.status.loading");
    case "error": return m.error || t("leaderboard.status.error");
    case "running": {
      const base = t("leaderboard.status.running", { n: Math.min(m.done + 1, m.total), total: m.total });
      if (!m.retry) return base;
      const w = m.retry.waitedS != null ? t("leaderboard.status.waited", { s: Math.round(m.retry.waitedS) }) : "";
      return t("leaderboard.status.retry", { base, k: m.retry.k, max: m.retry.n, name: m.retry.name, waited: w });
    }
    default: {
      const cachedN = m.readyCount - m.missingCount;
      if (m.note) return m.note;
      if (!m.readyCount) return "";
      if (!m.missingCount) return t("leaderboard.status.fresh", { when: fmtLast(m.dataLast) });
      if (cachedN > 0) return t("leaderboard.status.partial", { cached: cachedN, ready: m.readyCount, missing: m.missingCount });
      return t("leaderboard.status.empty");
    }
  }
}

export function runLabel(m: LbModel): string {
  if (m.phase === "running") return t("leaderboard.run.running");
  if (m.readyCount > 0 && m.missingCount === 0) return t("leaderboard.run.cached");
  if (m.readyCount > 0 && m.missingCount < m.readyCount) return t("leaderboard.run.missing", { n: m.missingCount });
  return t("leaderboard.run");
}

export const runDisabled = (m: LbModel): boolean =>
  m.phase === "running" || (m.readyCount > 0 && m.missingCount === 0);

/** Сколько стратегий осталось досчитать (ожидают, не рассчитаны, ошибка, «сервер занят»). */
export const countMissing = (entries: Map<string, Entry>): number =>
  [...entries.values()].filter((e) => e.k === "pending" || e.k === "none" || e.k === "error" || e.k === "busy").length;
