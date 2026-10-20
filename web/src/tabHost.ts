// Хост вкладок: ленивое создание, показ/скрытие и «предохранитель». Исключение при создании или показе вкладки (чаще всего — битые сохранённые
// настройки) не валит приложение: на месте вкладки появляется баннер с кнопками «Повторить» и «Сбросить сохранённые настройки вкладки»
// (очищается только ключ этой вкладки), остальные вкладки продолжают работать.
import { clearPersisted, currentRoute, type Route } from "./state";
import type { DataCtx, Tab, TabFactory } from "./tabs/tab";
import type { Meta } from "./types";
import { crashBanner } from "./ui/banner";
import { h } from "./ui/dom";

interface TabHostOpts {
  meta: Meta;
  factories: Record<Route, TabFactory>;
  titles: Record<Route, string>;
  content: HTMLElement;
  links: ReadonlyMap<Route, HTMLElement>;
  /** Индикатор вкладок в шапке после смены активной вкладки. */
  layoutTabs(scrollToActive: boolean): void;
}

interface TabHost {
  /** Показать вкладку текущего маршрута (по hash); та же вкладка повторно — ничего не делает. */
  show(): void;
  active(): Route | null;
  tab(r: Route): Tab | undefined;
  /** Контекст данных активной вкладки для полосы свежести (ошибка вкладки не пробрасывается). */
  dataContext(): DataCtx | null;
  /** «Данные обновились» для активной вкладки (ошибка вкладки не пробрасывается). */
  refresh(): void;
}

const msgOf = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export function createTabHost(o: TabHostOpts): TabHost {
  const tabs = new Map<Route, Tab>();
  let active: Route | null = null;

  const safe = (r: Route, what: string, fn: () => void): boolean => {
    try { fn(); return true; } catch (e) { console.error(`tab ${r}: ${what} failed`, e); return false; }
  };

  const decorate = (r: Route, t: Tab): Tab => {
    t.el.setAttribute("role", "tabpanel");
    t.el.setAttribute("aria-labelledby", `tab-${r}`);
    return t;
  };

  /** Заглушка вместо упавшей вкладки. */
  const crashed = (r: Route, e: unknown): Tab => {
    const retry = () => rebuild(r);
    const el = h("div", { class: "page tab-crash" },
      crashBanner(o.titles[r], msgOf(e), { retry, reset: () => { clearPersisted(r); retry(); } }));
    return { el, show() {}, hide() {} };
  };

  const build = (r: Route): Tab => {
    try {
      return decorate(r, o.factories[r](o.meta));
    } catch (e) {
      console.error(`tab ${r}: create failed`, e);
      return decorate(r, crashed(r, e));
    }
  };

  /** Пересоздать вкладку на месте (после «Повторить» / «Сбросить»). */
  function rebuild(r: Route): void {
    const old = tabs.get(r);
    if (!old) return;
    safe(r, "hide", () => old.hide());
    const fresh = build(r);
    fresh.el.hidden = old.el.hidden;
    old.el.replaceWith(fresh.el);
    tabs.set(r, fresh);
    if (active === r) showTab(r, fresh);
  }

  function showTab(r: Route, t: Tab): void {
    if (safe(r, "show", () => t.show())) return;
    // вкладка создалась, но упала при показе: заменяем её заглушкой (создание заглушки не бросает)
    const stub = decorate(r, crashed(r, new Error("вкладка не смогла показаться — см. консоль браузера")));
    safe(r, "hide", () => t.hide());
    stub.el.hidden = t.el.hidden;
    t.el.replaceWith(stub.el);
    tabs.set(r, stub);
  }

  return {
    show() {
      const r = currentRoute();
      if (r === active) return;
      if (active) {
        const prev = tabs.get(active)!;
        prev.el.hidden = true;
        safe(active, "hide", () => prev.hide());
      }
      let t = tabs.get(r);
      if (!t) {
        t = build(r);
        tabs.set(r, t);
        o.content.append(t.el);
      }
      t.el.hidden = false;
      t.el.classList.remove("enter");
      void t.el.offsetWidth;                 // перезапуск анимации появления
      t.el.classList.add("enter");
      active = r;
      o.links.forEach((a, k) => {
        a.setAttribute("aria-selected", String(k === r));
        a.tabIndex = k === r ? 0 : -1;
      });
      document.title = `${o.titles[r]} — Tradebot`;
      o.layoutTabs(true);
      showTab(r, t);
    },
    active: () => active,
    tab: (r) => tabs.get(r),
    dataContext() {
      let ctx: DataCtx | null = null;
      if (active) safe(active, "dataContext", () => { ctx = tabs.get(active!)?.dataContext?.() ?? null; });
      return ctx;
    },
    refresh() {
      if (active) safe(active, "refresh", () => tabs.get(active!)?.refresh?.());
    },
  };
}
