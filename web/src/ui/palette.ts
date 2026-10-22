// Командная палитра (Ctrl/Cmd+K или «/»): поиск по вкладкам и действиям с клавиатуры. Без зависимостей.
// Логика поиска — paletteCore.ts (чистые функции, тесты в tests/palette.test.ts); здесь — диалог, список команд и горячие клавиши.
import { strategyDesc } from "../i18n/serverText";
import { t } from "../i18n";
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
    id: `tab:${r}`, title: titles[r] ?? r, group: t("common.pal.group_tabs"), icon: "arrow", keys: [String(i + 1)],
    // ключевые слова для поиска — на обоих языках в любом языке интерфейса (русские названия вкладок находятся и в английском интерфейсе)
    keywords: { lab: "lab laboratory backtest лаборатория бэктест", portfolio: "portfolio портфель", wf: "walk forward wf", best: "leaderboard best top ranking рейтинг лучшие стратегии топ", live: "bot бот live торговля позиция trading position", stats: "stats statistics статистика анализ сделок история roi просадка drawdown history trades pnl" }[r],
    run: () => setRoute(r),
  }));
  const mode = currentMode();
  for (const [m, label, ico, kw] of [["light", t("common.theme.light"), "sun", "light светлая"], ["auto", t("common.theme.auto"), "auto", "auto авто система system"], ["dark", t("common.theme.dark"), "moon", "dark тёмная темная"]] as const) {
    const title = t("common.pal.theme_title", { label });
    out.push({ id: `theme:${m}`, title, hint: mode === m ? t("common.pal.theme_on") : undefined, group: t("common.pal.group_theme"), icon: ico, keywords: `${kw} theme тема оформление`,
      run: () => { applyTheme(m, { persist: true }); toast(title, "info"); } });
  }
  const fetchBtn = document.querySelector<HTMLButtonElement>(".freshness .btn");
  if (fetchBtn && !fetchBtn.closest("[hidden]") && !fetchBtn.disabled) {
    out.push({ id: "act:fetch", title: t("common.fresh.fetch"), hint: t("common.pal.fetch_hint"), group: t("common.pal.group_actions"), icon: "refresh", keywords: "update data candles fetch download данные свечи обновить подгрузить скачать", run: () => fetchBtn.click() });
  }
  if (meta?.legs?.length) {
    const m = meta;
    out.push({ id: "act:legs", title: t("common.pal.legs_title"), hint: t("common.pal.legs_hint"), group: t("common.pal.group_actions"), icon: "copy", keywords: "copy leg legs bot бот нога ноги копировать",
      run: () => void copyWithToast(m.legs.map((l) => pyLeg(l)).join("\n")) });
  }
  if (lastLive?.available && lastLive.legs?.length) {
    const l = lastLive;
    out.push({ id: "act:live-legs", title: t("common.pal.live_legs_title"), hint: t("common.pal.live_legs_hint", { tab: t("tab.live") }), group: t("common.pal.group_actions"), icon: "copy", keywords: "copy live legs ноги копировать веса weights",
      run: () => void copyWithToast(l.legs!.map((x) => t("common.pal.live_leg", { key: x.key, weight: x.weight, off: x.enabled ? "" : t("common.pal.live_leg_off"), signal: x.signal ?? "—", params: JSON.stringify(x.params) })).join("\n")) });
  }
  out.push({ id: "act:github", title: t("common.pal.github_title"), hint: t("common.pal.github_hint"), group: t("common.pal.group_actions"), icon: "github", keywords: "github git repo repository репозиторий исходники код source",
    run: () => { window.open(REPO_URL, "_blank", "noopener,noreferrer"); } });
  for (const s of meta?.strategies ?? []) {
    out.push({ id: `lab:${s.name}`, title: t("common.pal.strategy_title", { name: s.name }), hint: strategyDesc(s.name, s.desc), group: t("common.pal.group_strategies"), icon: "flask", keywords: `lab strategy стратегия лаборатория ${s.derivative ? "деривативы derivatives" : ""}`, run: () => labPreset(s.name) });
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
      type: "text", class: "pal-input", placeholder: t("common.pal.placeholder"), autocomplete: "off", spellcheck: false,
      role: "combobox", "aria-expanded": "true", "aria-controls": "pal-list", "aria-autocomplete": "list", "aria-label": t("common.pal.search_aria"),
    });
    const list = h("ul", { class: "pal-list", id: "pal-list", role: "listbox", "aria-label": t("common.pal.list_aria") });
    const empty = h("div", { class: "pal-empty", hidden: true, text: t("common.nothing_found") });
    const status = h("div", { class: "sr-only", "aria-live": "polite" });
    dlg = h("dialog", { class: "palette", "aria-label": t("common.pal.dialog_aria") },
      h("div", { class: "pal-head" }, icon("search", 16), input, kbd("Esc", true)),
      list, empty, status,
      h("div", { class: "pal-foot" }, h("span", {}, kbd("↑", true), kbd("↓", true), " " + t("common.pal.foot_select")), h("span", {}, kbd("Enter", true), " " + t("common.pal.foot_run")), h("span", {}, kbd("Esc", true), " " + t("common.pal.foot_close"))));

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
      status.textContent = shown.length ? t("common.pal.count", { n: shown.length }) : t("common.nothing_found");
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
    class: "btn ghost palette-btn", type: "button", "aria-label": t("common.pal.btn_aria", { keys: PALETTE_KEYS.join(" ") }), "aria-haspopup": "dialog", title: t("common.pal.btn_tip", { keys: PALETTE_KEYS.join("+") }),
    onClick: openPalette,
  }, icon("search", 15), h("span", { class: "palette-btn-t", text: t("common.pal.btn_text") }), h("span", { class: "palette-btn-k", "aria-hidden": "true" }, ...PALETTE_KEYS.map((k) => kbd(k, true))));

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
