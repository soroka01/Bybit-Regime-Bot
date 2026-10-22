// Состояние контролов (localStorage) и маршрутизация по hash.
import type { Market } from "./types";

type Checks<T> = { [K in keyof T]?: (v: T[K]) => boolean };

const kindOf = (v: unknown): string => (v === null ? "null" : Array.isArray(v) ? "array" : typeof v);

/**
 * Берёт из «сырого» значения (разобранный localStorage) только те ключи, которые есть в defaults и совпадают с ними по типу
 * (typeof, массив/не массив, null), а затем проходят явные проверки `checks` (белые списки значений). Всё остальное отбрасывается —
 * вместо него остаётся значение по умолчанию. Конечными должны быть и числа (NaN/Infinity отбрасываются).
 */
export function validate<T extends object>(defaults: T, raw: unknown, checks: Checks<T> = {}): Partial<T> {
  const out: Partial<T> = {};
  if (kindOf(raw) !== "object") return out;
  const src = raw as Record<string, unknown>;
  for (const key of Object.keys(defaults) as (keyof T & string)[]) {
    if (!Object.prototype.hasOwnProperty.call(src, key)) continue;
    const v = src[key];
    const d = (defaults as Record<string, unknown>)[key];
    if (kindOf(v) !== kindOf(d)) continue;
    if (typeof v === "number" && !Number.isFinite(v)) continue;
    const check = checks[key];
    if (check) {
      let ok = false;
      try { ok = check(v as T[typeof key]); } catch { ok = false; }
      if (!ok) continue;
    }
    out[key] = v as T[typeof key];
  }
  return out;
}

/** Отложенные записи по ключам: clearPersisted гасит их, чтобы упавшая вкладка не воскресила сброшенное состояние. */
const pendingSaves = new Map<string, { timer: number; write: () => void }>();

/** Стереть сохранённое состояние вкладки (только её ключ tb.<key>; остальные настройки не трогаются). */
export function clearPersisted(key: string): void {
  clearTimeout(pendingSaves.get(key)?.timer);
  pendingSaves.delete(key);
  try { localStorage.removeItem("tb." + key); } catch { /* приватный режим */ }
}

/** Немедленно записать все отложенные (200 мс) сохранения: вызывается перед перезагрузкой страницы (смена языка), чтобы не потерять последнюю правку. */
export function flushSaves(): void {
  for (const { timer, write } of [...pendingSaves.values()]) { clearTimeout(timer); write(); }
}

/** Объект состояния, сохраняемый в localStorage под ключом tb.<key>. Недостающие/некорректные поля берутся из defaults. */
export function persisted<T extends object>(
  key: string, defaults: T, checks: Checks<T> = {},
): { state: T; save(): void; reset(): void } {
  const k = "tb." + key;
  let state: T = structuredClone(defaults);
  try {
    const raw = localStorage.getItem(k);
    if (raw) state = { ...state, ...validate(defaults, JSON.parse(raw), checks) };
  } catch { /* приватный режим / битый JSON — работаем с дефолтами */ }
  let timer = 0;
  return {
    state,
    save() {
      clearTimeout(timer);
      const write = () => {
        pendingSaves.delete(key);
        try { localStorage.setItem(k, JSON.stringify(state)); } catch { /* ignore */ }
      };
      timer = window.setTimeout(write, 200);
      pendingSaves.set(key, { timer, write });
    },
    reset() {
      clearTimeout(timer);   // отложенная запись не должна воскресить сброшенное состояние
      Object.assign(state, structuredClone(defaults));
      try { localStorage.removeItem(k); } catch { /* ignore */ }
    },
  };
}

export const ROUTES = ["lab", "portfolio", "wf", "best", "live", "stats"] as const;
export type Route = (typeof ROUTES)[number];

export function currentRoute(): Route {
  const h = location.hash.replace(/^#\/?/, "").split(/[?/]/)[0] as Route;
  if ((ROUTES as readonly string[]).includes(h)) return h;
  try {
    const last = localStorage.getItem("tb.route") as Route | null;
    if (last && (ROUTES as readonly string[]).includes(last)) return last;
  } catch { /* ignore */ }
  return "lab";
}

export function setRoute(r: Route): void {
  location.hash = "#/" + r;
  try { localStorage.setItem("tb.route", r); } catch { /* ignore */ }
}

/**
 * Одноразовые deep-link флаги в hash: «#/lab?opt», «#/best?run». Возвращает true, если флаг есть, и убирает query из адреса
 * (replaceState — без события hashchange), чтобы следующий show() вкладки не запускал действие повторно.
 */
export function takeFlag(route: Route, flag: string): boolean {
  const q = location.hash.split("?")[1];
  if (q === undefined) return false;
  const has = q.split("&").includes(flag);
  if (has) {
    try { history.replaceState(null, "", "#/" + route); } catch { /* ignore */ }
  }
  return has;
}

/** Передача настроек из «Лучших стратегий» в Лабораторию (Лаборатория забирает при показе). */
interface LabPreset {
  symbol: string; tf: string; strategy: string; params: Record<string, number>; days: number;
  longOnly: boolean; leverage: number; htf: number | null; stop: number | null; market: Market;
}
let pendingLab: LabPreset | null = null;
export const setPendingLab = (p: LabPreset) => { pendingLab = p; };
export const takePendingLab = (): LabPreset | null => { const p = pendingLab; pendingLab = null; return p; };
