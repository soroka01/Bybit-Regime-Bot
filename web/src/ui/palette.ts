// Командная палитра (Ctrl/Cmd+K или «/»): поиск по вкладкам и действиям с клавиатуры. Без зависимостей.
// Логика поиска — paletteCore.ts (чистые функции, тесты в tests/palette.test.ts); здесь — диалог, список команд и горячие клавиши.
import { setPendingLab, setRoute, ROUTES, type Route } from "../state";
import type { LiveSnapshot, Meta } from "../types";
import { h } from "./dom";
import { copyWithToast, pyLeg } from "./clipboard";
import { REPO_URL } from "./githubLink";
import { icon } from "./icons";
import { kbd } from "./kit";
import { filterCommands, pushRecent, type Command } from "./paletteCore";
import { applyTheme, currentMode } from "./theme";
import { toast } from "./toast";

const RECENT_KEY = "tb.palette.recent";
const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/i.test(navigator.platform || "");
export const PALETTE_KEYS: string[] = [IS_MAC ? "⌘" : "Ctrl", "K"];

let meta: Meta | null = null;
let lastLive: LiveSnapshot | null = null;
let titles: Record<string, string> = {};

/** После загрузки /api/meta: названия вкладок и список стратегий становятся доступны палитре. */
export function setPaletteContext(m: Meta | null, tabTitles: Record<string, string>): void { meta = m; titles = tabTitles; }
window.addEventListener("tb-live", (e) => { lastLive = (e as CustomEvent<LiveSnapshot>).detail; });

const isTyping = (t: EventTarget | null): boolean => {
  const el = t as HTMLElement | null;
  return !!el && !!el.tagName && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
};

function loadRecent(): string[] {
  try { const v = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); return Array.isArray(v) ? v.filter((x) => typeof x === "string") : []; } catch { return []; }
}
function saveRecent(id: string): void {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(pushRecent(loadRecent(), id))); } catch { /* приватный режим */ }
}

/** Лаборатория с выбранной стратегией: параметры по умолчанию, символ/период — как сейчас в Лаборатории (если сохранены), иначе по метаданным. */
function labPreset(strategy: string) {
  const st = meta!.strategies.find((s) => s.name === strategy)!;
  let sym = meta!.symbols[0] ?? "BTCUSDT", tf = "1h";
  try {
    const raw = JSON.parse(localStorage.getItem("tb.lab") || "{}") as { symbol?: string; tf?: string };
    if (raw.symbol && meta!.symbols.includes(raw.symbol)) sym = raw.symbol;
    if (raw.tf && meta!.timeframes.includes(raw.tf)) tf = raw.tf;
  } catch { /* ignore */ }
  const params = Object.fromEntries(Object.entries(st.params).map(([k, p]) => [k, p.default]));
  setPendingLab({ symbol: sym, tf, strategy, params, days: meta!.defaults.days, longOnly: false, leverage: meta!.defaults.leverage || 1, htf: null, stop: null, market: "perp" });
  setRoute("lab");
}

export function buildCommands(): Command[] {
  const out: Command[] = [];
  ROUTES.forEach((r: Route, i) => out.push({
    id: `tab:${r}`, title: titles[r] ?? r, group: "Вкладки", icon: "arrow", keys: [String(i + 1)],
    keywords: { lab: "lab laboratory бэктест", portfolio: "portfolio портфель", wf: "walk forward wf", best: "leaderboard рейтинг лучшие топ", live: "bot бот live торговля позиция", stats: "stats statistics статистика анализ сделок история roi просадка pnl" }[r],
    run: () => setRoute(r),
  }));
  const mode = currentMode();
  for (const [m, label, ico, kw] of [["light", "Светлая", "sun", "light"], ["auto", "Авто", "auto", "auto система"], ["dark", "Тёмная", "moon", "dark"]] as const) {
    out.push({ id: `theme:${m}`, title: `Тема: ${label}`, hint: mode === m ? "сейчас включена" : undefined, group: "Тема", icon: ico, keywords: `${kw} theme оформление`,
      run: () => { applyTheme(m, { persist: true }); toast(`Тема: ${label}`, "info"); } });
  }
  const fetchBtn = document.querySelector<HTMLButtonElement>(".freshness .btn");
  if (fetchBtn && !fetchBtn.closest("[hidden]") && !fetchBtn.disabled) {
    out.push({ id: "act:fetch", title: "Подгрузить новые свечи", hint: "скачать свежие свечи и деривативы с Bybit (публичный API)", group: "Действия", icon: "refresh", keywords: "update data данные свечи обновить fetch", run: () => fetchBtn.click() });
  }
  if (meta?.legs?.length) {
    const m = meta;
    out.push({ id: "act:legs", title: "Скопировать ноги бота (Leg)", hint: "строки Leg(...) из конфигурации бота по умолчанию", group: "Действия", icon: "copy", keywords: "copy leg бот нога копировать",
      run: () => void copyWithToast(m.legs.map((l) => pyLeg(l)).join("\n")) });
  }
  if (lastLive?.available && lastLive.legs?.length) {
    const l = lastLive;
    out.push({ id: "act:live-legs", title: "Скопировать текущие ноги и веса бота", hint: "из снимка «Бот (live)»", group: "Действия", icon: "copy", keywords: "copy live веса weights",
      run: () => void copyWithToast(l.legs!.map((x) => `${x.key}: вес ${x.weight}${x.enabled ? "" : " (выкл)"}, сигнал ${x.signal ?? "—"}, ${JSON.stringify(x.params)}`).join("\n")) });
  }
  out.push({ id: "act:github", title: "Открыть репозиторий на GitHub", hint: "исходный код проекта, README и CI", group: "Действия", icon: "github", keywords: "github git repo репозиторий исходники код source",
    run: () => { window.open(REPO_URL, "_blank", "noopener,noreferrer"); } });
  for (const s of meta?.strategies ?? []) {
    out.push({ id: `lab:${s.name}`, title: `Открыть в Лаборатории: ${s.name}`, hint: s.desc, group: "Стратегии", icon: "flask", keywords: `lab strategy стратегия ${s.derivative ? "деривативы" : ""}`, run: () => labPreset(s.name) });
  }
  return out;
}

export function installPalette(): { button: HTMLButtonElement } {
  let dlg: HTMLDialogElement | null = null;

  const openPalette = () => {
    if (dlg) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const all = buildCommands();
    const recent = loadRecent();
    let shown: Command[] = [];
    let active = 0;

    const input = h("input", {
      type: "text", class: "pal-input", placeholder: "Куда перейти или что сделать…", autocomplete: "off", spellcheck: false,
      role: "combobox", "aria-expanded": "true", "aria-controls": "pal-list", "aria-autocomplete": "list", "aria-label": "Поиск команд",
    });
    const list = h("ul", { class: "pal-list", id: "pal-list", role: "listbox", "aria-label": "Команды" });
    const empty = h("div", { class: "pal-empty", hidden: true, text: "Ничего не найдено" });
    const status = h("div", { class: "sr-only", "aria-live": "polite" });
    dlg = h("dialog", { class: "palette", "aria-label": "Командная палитра" },
      h("div", { class: "pal-head" }, icon("search", 16), input, kbd("Esc", true)),
      list, empty, status,
      h("div", { class: "pal-foot" }, h("span", {}, kbd("↑", true), kbd("↓", true), " выбор"), h("span", {}, kbd("Enter", true), " выполнить"), h("span", {}, kbd("Esc", true), " закрыть")));

    const close = () => { const d = dlg; dlg = null; d?.close(); d?.remove(); if (opener?.isConnected) opener.focus({ preventScroll: true }); };
    const run = (c: Command) => { saveRecent(c.id); close(); void Promise.resolve(c.run()).catch((e) => toast(String(e), "error")); };

    const paint = () => {
      list.replaceChildren(...shown.map((c, i) => {
        const li = h("li", { class: `pal-item${i === active ? " active" : ""}`, role: "option", id: `pal-o${i}`, "aria-selected": String(i === active) },
          h("span", { class: "pal-ic" }, icon(c.icon ?? "arrow", 16)),
          h("span", { class: "pal-txt" }, h("span", { class: "pal-title", text: c.title }), c.hint ? h("span", { class: "pal-hint", text: c.hint }) : null),
          h("span", { class: "pal-group", text: c.group }),
          c.keys ? h("span", { class: "pal-keys" }, ...c.keys.map((k) => kbd(k, true))) : null);
        li.addEventListener("mousemove", () => { if (active !== i) { active = i; markActive(false); } });
        li.addEventListener("click", () => run(c));
        return li;
      }));
      empty.hidden = shown.length > 0;
      input.setAttribute("aria-activedescendant", shown.length ? `pal-o${active}` : "");
      status.textContent = shown.length ? `${shown.length} вариантов` : "Ничего не найдено";
    };
    const markActive = (scroll = true) => {
      list.querySelectorAll(".pal-item").forEach((el, i) => { el.classList.toggle("active", i === active); el.setAttribute("aria-selected", String(i === active)); });
      input.setAttribute("aria-activedescendant", shown.length ? `pal-o${active}` : "");
      if (scroll) (list.children[active] as HTMLElement | undefined)?.scrollIntoView({ block: "nearest" });
    };
    const refilter = () => { shown = filterCommands(all, input.value, recent); active = 0; paint(); };

    input.addEventListener("input", refilter);
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!shown.length) return;
        active = (active + (e.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length;
        markActive();
      } else if (e.key === "Home" || e.key === "End") {
        if (!shown.length) return;
        e.preventDefault(); active = e.key === "Home" ? 0 : shown.length - 1; markActive();
      } else if (e.key === "Enter") {
        e.preventDefault();
        const c = shown[active];
        if (c) run(c);
      }
    });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
    dlg.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); close(); } });
    document.body.append(dlg);
    dlg.showModal();
    refilter();
    input.focus();
  };

  const button = h("button", {
    class: "btn ghost palette-btn", type: "button", "aria-label": `Командная палитра (${PALETTE_KEYS.join(" ")})`, "aria-haspopup": "dialog", title: `Командная палитра · ${PALETTE_KEYS.join("+")}`,
    onClick: openPalette,
  }, icon("search", 15), h("span", { class: "palette-btn-t", text: "Поиск" }), h("span", { class: "palette-btn-k", "aria-hidden": "true" }, ...PALETTE_KEYS.map((k) => kbd(k, true))));

  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && !e.altKey && !e.shiftKey && e.code === "KeyK") {
      e.preventDefault();
      if (dlg) return;
      if (document.querySelector("dialog[open]")) return;   // не поверх другого модального окна
      openPalette();
      return;
    }
    if (e.key === "/" && !mod && !e.altKey && !isTyping(e.target) && !dlg && !document.querySelector("dialog[open]")) { e.preventDefault(); openPalette(); }
  });
  return { button };
}
