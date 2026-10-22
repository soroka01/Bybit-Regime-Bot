// Тема: явный переключатель «Светлая / Авто / Тёмная». Выбор хранится в localStorage "tb.theme" (включая "auto"), по умолчанию — тёмная.
// На <html> ставятся data-theme (уже разрешённая: light|dark) и data-theme-mode (выбор: auto|light|dark).
// Первичную расстановку делает inline-скрипт в index.html (до первой отрисовки, без вспышки); здесь — то же самое для переключения «на лету».
// Графики слушают событие "tb-theme" и перечитывают CSS-переменные. `?theme=dark|light` в адресе — временное переопределение (не сохраняется).
import { t } from "../i18n";
import { h } from "./dom";
import { icon } from "./icons";

type ThemeMode = "auto" | "light" | "dark";
type Resolved = "light" | "dark";

const KEY = "tb.theme";
/** Цвет интерфейса браузера (meta theme-color) — по теме. Должен совпадать с --bg в styles/tokens.css. */
export const THEME_COLORS: Record<Resolved, string> = { dark: "#0a0d13", light: "#f3f5f8" };
const MODES: { mode: ThemeMode; label: string; ico: string }[] = [
  { mode: "light", label: t("common.theme.light"), ico: "sun" },
  { mode: "auto", label: t("common.theme.auto"), ico: "auto" },
  { mode: "dark", label: t("common.theme.dark"), ico: "moon" },
];

const isMode = (v: unknown): v is ThemeMode => v === "light" || v === "dark" || v === "auto";

function urlOverride(): Resolved | null {
  try {
    const v = new URLSearchParams(location.search).get("theme");
    return v === "light" || v === "dark" ? v : null;
  } catch { return null; }
}

export function storedMode(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY);
    if (isMode(v)) return v;
  } catch { /* приватный режим */ }
  return "dark";
}

const osDark = (): boolean => {
  try { return matchMedia("(prefers-color-scheme: dark)").matches; } catch { return true; }
};
export const resolve = (m: ThemeMode): Resolved => (m === "auto" ? (osDark() ? "dark" : "light") : m);

/** Текущий выбор (с учётом ?theme=). */
export function currentMode(): ThemeMode {
  return (document.documentElement.dataset.themeMode as ThemeMode | undefined) ?? urlOverride() ?? storedMode();
}

let animTimer = 0;
/** Ставит тему на документ: атрибуты, color-scheme, theme-color, плавный переход (150 мс) и событие "tb-theme". */
export function applyTheme(mode: ThemeMode, opts: { persist?: boolean; animate?: boolean } = {}): void {
  const root = document.documentElement;
  const res = resolve(mode);
  const changed = root.dataset.theme !== res;
  if (opts.animate !== false && changed) {
    root.classList.add("theme-anim");
    clearTimeout(animTimer);
    animTimer = window.setTimeout(() => root.classList.remove("theme-anim"), 220);
  }
  root.dataset.theme = res;
  root.dataset.themeMode = mode;
  const cs = document.querySelector('meta[name="color-scheme"]');
  if (cs) cs.setAttribute("content", res);
  const tc = document.querySelector('meta[name="theme-color"]');
  if (tc) tc.setAttribute("content", THEME_COLORS[res]);
  if (opts.persist) {
    try { localStorage.setItem(KEY, mode); } catch { /* ignore */ }
  }
  window.dispatchEvent(new Event("tb-theme"));
}

/** Переключатель в верхней панели: три кнопки с иконками (aria-pressed), клавиатурой — Tab/Enter/Пробел и стрелки. */
export function themeSwitch(): HTMLElement {
  const wrap = h("div", { class: "theme-switch", role: "group", "aria-label": t("common.theme.group") });
  const btns = new Map<ThemeMode, HTMLButtonElement>();
  const paint = () => {
    const m = currentMode();
    btns.forEach((b, k) => b.setAttribute("aria-pressed", String(k === m)));
  };
  for (const { mode, label, ico } of MODES) {
    const b = h("button", { type: "button", "aria-label": t("common.theme.aria", { label }), "data-tip": label, "aria-pressed": "false" },
      icon(ico, 15), h("span", { class: "ts-label", text: label }));
    b.addEventListener("click", () => { applyTheme(mode, { persist: true }); paint(); });
    btns.set(mode, b);
    wrap.append(b);
  }
  wrap.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const i = MODES.findIndex((m) => m.mode === currentMode());
    const j = (i + (e.key === "ArrowRight" ? 1 : MODES.length - 1)) % MODES.length;
    e.preventDefault();
    applyTheme(MODES[j]!.mode, { persist: true });
    paint();
    btns.get(MODES[j]!.mode)?.focus();
  });
  try {
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (currentMode() === "auto") applyTheme("auto", { animate: true });
    });
  } catch { /* старые браузеры */ }
  window.addEventListener("tb-theme", paint);   // тему могут сменить и извне (командная палитра)
  paint();
  return wrap;
}
