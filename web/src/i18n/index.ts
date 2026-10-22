// Локализация интерфейса: язык (en | ru), t() с подстановкой {имя} и множественными формами, словари — i18n/messages/<пространство>.ts.
//
// Язык определяется ОДИН раз при загрузке страницы: ?lang= (временно) > localStorage "tb.lang" (выбор переключателя) > <meta name="tb-default-lang">
// (язык по умолчанию из конфигурации сервера, APP_LANGUAGE) > en. Тот же порядок — в inline-скрипте index.html, который ставит <html lang> до первой
// отрисовки. Интерфейс строится императивно и вызывает t() при построении, поэтому переключение сохраняет выбор и перезагружает страницу
// (switchLang): перерисовка «на лету» не нужна, а константы модулей (заголовки, словари вердиктов) спокойно вычисляются при импорте.
// Модуль лежит ниже ui/ и charts/ и ничего из них не импортирует.
import { EN, RU, type Msg, type MsgKey } from "./messages";

export type { Msg, MsgKey } from "./messages";
export type Lang = "en" | "ru";
export type Params = Record<string, string | number>;

export const LANGS: readonly Lang[] = ["en", "ru"];
export const DEFAULT_LANG: Lang = "en";
export const LANG_KEY = "tb.lang";

export const isLang = (v: unknown): v is Lang => v === "en" || v === "ru";

/** Язык по порядку приоритетов (см. шапку файла). Без DOM (тесты в node) — язык по умолчанию. */
export function detectLang(): Lang {
  try {
    const q = new URLSearchParams(location.search).get("lang");
    if (isLang(q)) return q;
  } catch { /* нет location */ }
  try {
    const v = localStorage.getItem(LANG_KEY);
    if (isLang(v)) return v;
  } catch { /* приватный режим / нет localStorage */ }
  try {
    const m = document.querySelector('meta[name="tb-default-lang"]')?.getAttribute("content");
    if (isLang(m)) return m;
  } catch { /* нет document */ }
  return DEFAULT_LANG;
}

let current: Lang = detectLang();

export const getLang = (): Lang => current;
/** BCP-47 тег для Intl: ru-RU или en-US. */
export const localeTag = (): string => (current === "ru" ? "ru-RU" : "en-US");

/** ТОЛЬКО для тестов: сменить язык без перезагрузки. Значения, вычисленные при импорте модулей (константы), остаются прежними. */
export function __setLang(l: Lang): void {
  current = l;
  try { document.documentElement.lang = l; } catch { /* нет document */ }
}

/** Выбор переключателя: сохранить и перезагрузить страницу (reload — для тестов). Если localStorage недоступен, язык уходит в адрес (?lang=). */
export function switchLang(l: Lang, reload: () => void = () => location.reload()): void {
  if (l === current) return;
  let saved = false;
  try { localStorage.setItem(LANG_KEY, l); saved = localStorage.getItem(LANG_KEY) === l; } catch { /* приватный режим */ }
  try {
    const u = new URL(location.href);
    if (saved) u.searchParams.delete("lang"); else u.searchParams.set("lang", l);   // иначе старый ?lang= перебьёт выбор после перезагрузки
    history.replaceState(null, "", u);
  } catch { /* нет location */ }
  reload();
}

/** Ставит язык и заголовок на документ (атрибут lang уже выставлен inline-скриптом index.html, здесь — страховка и заголовок). */
export function applyLangToDocument(): void {
  document.documentElement.lang = current;
  document.title = t("app.title");
}

const warned = new Set<string>();
const miss = (key: string): string => {
  if (!warned.has(key)) { warned.add(key); try { console.warn(`i18n: нет ключа «${key}»`); } catch { /* ignore */ } }
  return key;
};

const rules = new Map<Lang, Intl.PluralRules>();
const pluralCategory = (n: number): Intl.LDMLPluralRule => {
  let r = rules.get(current);
  if (!r) rules.set(current, (r = new Intl.PluralRules(localeTag())));
  return r.select(n);
};

const fill = (s: string, params?: Params): string =>
  params ? s.replace(/\{(\w+)\}/g, (m, k: string) => (k in params ? String(params[k]) : m)) : s;

function pick(msg: Msg, params?: Params): string | undefined {
  if (typeof msg === "string") return fill(msg, params);
  if (Array.isArray(msg)) return undefined;
  const p = msg as Exclude<Msg, string | readonly string[]>;
  const n = Number(params?.n);
  return fill((Number.isFinite(n) ? p[pluralCategory(n)] : undefined) ?? p.other, params);
}

const dict = (): Record<string, Msg> => (current === "ru" ? RU : EN);

/** Текст по ключу: «{имя}» подставляется из params; для набора форм число — params.n (ru: one/few/many/other, en: one/other). Нет ключа — сам ключ. */
export function t(key: MsgKey, params?: Params): string {
  return tDyn(key, params) ?? miss(key);
}

/** То же для ключей, собранных на лету (по имени стратегии, коду ошибки): undefined, если ключа нет — вызывающий подставляет запасной текст. */
export function tDyn(key: string, params?: Params): string | undefined {
  const m = dict()[key];
  return m === undefined ? undefined : pick(m, params);
}

/** Список строк (месяцы, дни недели). */
export function tList(key: MsgKey): readonly string[] {
  const m = dict()[key];
  return Array.isArray(m) ? (m as readonly string[]) : (miss(key), []);
}

export const hasKey = (key: string): boolean => key in dict();
