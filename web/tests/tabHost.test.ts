// @vitest-environment jsdom
// «Предохранитель» вкладок (F1): упавшая при создании/показе вкладка заменяется баннером со сбросом её сохранённых настроек, остальные работают.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearPersisted, persisted, ROUTES, type Route } from "../src/state";
import { createTabHost } from "../src/tabHost";
import type { Tab, TabFactory } from "../src/tabs/tab";
import { testMeta } from "./fixtures/meta";

const titles = Object.fromEntries(ROUTES.map((r) => [r, `T-${r}`])) as Record<Route, string>;

interface Probe { created: Record<string, number>; shown: Record<string, number>; hidden: Record<string, number> }
function setup(over: Partial<Record<Route, TabFactory>> = {}) {
  document.body.replaceChildren();
  const content = document.createElement("main");
  document.body.append(content);
  const links = new Map<Route, HTMLAnchorElement>(ROUTES.map((r) => [r, Object.assign(document.createElement("a"), { id: `tab-${r}` })]));
  const p: Probe = { created: {}, shown: {}, hidden: {} };
  const count = (m: Record<string, number>, k: string) => { m[k] = (m[k] ?? 0) + 1; };
  const factories = Object.fromEntries(ROUTES.map((r) => [r, over[r] ?? ((): Tab => {
    count(p.created, r);
    return { el: Object.assign(document.createElement("div"), { textContent: `tab ${r}` }), show() { count(p.shown, r); }, hide() { count(p.hidden, r); } };
  })])) as Record<Route, TabFactory>;
  const layout = vi.fn();
  const host = createTabHost({ meta: testMeta, factories, titles, content, links, layoutTabs: layout });
  return { host, content, links, p, layout };
}
const go = (r: Route) => { location.hash = `#/${r}`; };
const banner = (root: HTMLElement) => root.querySelector<HTMLElement>(".tab-crash-banner");
const click = (root: HTMLElement, text: string) => [...root.querySelectorAll("button")].find((b) => b.textContent === text)!.click();

beforeEach(() => { localStorage.clear(); vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("tabHost: нормальная работа", () => {
  it("создаёт вкладку лениво, показывает/скрывает при переключении, размечает tabpanel и ссылки", () => {
    const { host, content, links, p, layout } = setup();
    go("lab"); host.show();
    expect(p.created).toEqual({ lab: 1 });
    expect(p.shown.lab).toBe(1);
    expect(content.querySelectorAll("[role=tabpanel]")).toHaveLength(1);
    expect(content.firstElementChild!.getAttribute("aria-labelledby")).toBe("tab-lab");
    expect(links.get("lab")!.getAttribute("aria-selected")).toBe("true");
    expect(links.get("lab")!.tabIndex).toBe(0);
    expect(links.get("stats")!.tabIndex).toBe(-1);
    expect(document.title).toBe("T-lab — Tradebot");
    expect(layout).toHaveBeenCalledWith(true);
    go("stats"); host.show();
    expect(p.hidden.lab).toBe(1);
    expect((host.tab("lab")!.el).hidden).toBe(true);
    go("lab"); host.show();
    expect(p.created.lab).toBe(1);                       // повторно не создаётся
    expect(p.shown.lab).toBe(2);
    host.show();                                          // тот же маршрут — ничего
    expect(p.shown.lab).toBe(2);
  });
});

describe("tabHost: исключение при создании вкладки", () => {
  const poisoned: TabFactory = () => {
    if (localStorage.getItem("tb.lab")?.includes("poison")) throw new TypeError("битые настройки: Cannot read properties of null");
    return { el: Object.assign(document.createElement("div"), { textContent: "lab ok" }), show() {}, hide() {} };
  };

  it("вместо вкладки — баннер role=alert с текстом ошибки и двумя действиями; остальные вкладки живы", () => {
    const { host, content, p } = setup({ lab: () => { throw new Error("boom"); } });
    go("lab");
    expect(() => host.show()).not.toThrow();
    const b = banner(content)!;
    expect(b.getAttribute("role")).toBe("alert");
    expect(b.textContent).toContain("Вкладка «T-lab» не открылась");
    expect(b.textContent).toContain("boom");
    expect([...b.querySelectorAll("button")].map((x) => x.textContent)).toEqual(["Повторить", "Сбросить сохранённые настройки вкладки"]);
    expect(b.closest("[role=tabpanel]")!.getAttribute("aria-labelledby")).toBe("tab-lab");
    go("stats"); host.show();
    expect(p.shown.stats).toBe(1);
    expect(banner(content)!.closest<HTMLElement>("[role=tabpanel]")!.hidden).toBe(true);
    go("lab"); host.show();
    expect(banner(content)!.closest<HTMLElement>("[role=tabpanel]")!.hidden).toBe(false);
  });

  it("«Сбросить сохранённые настройки вкладки» стирает только ключ этой вкладки и пересоздаёт её", () => {
    localStorage.setItem("tb.lab", '{"symbol":"poison"}');
    localStorage.setItem("tb.stats", '{"range":"7d"}');
    localStorage.setItem("tb.theme", "light");
    localStorage.setItem("tb.route", "lab");
    const { host, content } = setup({ lab: poisoned });
    go("lab"); host.show();
    expect(banner(content)).not.toBeNull();
    click(content, "Сбросить сохранённые настройки вкладки");
    expect(localStorage.getItem("tb.lab")).toBeNull();
    expect(localStorage.getItem("tb.stats")).toBe('{"range":"7d"}');
    expect(localStorage.getItem("tb.theme")).toBe("light");
    expect(localStorage.getItem("tb.route")).toBe("lab");
    expect(banner(content)).toBeNull();
    expect(content.textContent).toContain("lab ok");
    expect(content.querySelectorAll("[role=tabpanel]")).toHaveLength(1);        // заглушка заменена, а не добавлена
    expect(host.tab("lab")!.el.hidden).toBe(false);
  });

  it("«Повторить» без сброса: причина осталась — баннер остаётся (один), вкладка создаётся заново", () => {
    localStorage.setItem("tb.lab", "poison");
    const f = vi.fn(poisoned);
    const { host, content } = setup({ lab: f });
    go("lab"); host.show();
    click(content, "Повторить");
    expect(f).toHaveBeenCalledTimes(2);
    expect(content.querySelectorAll(".tab-crash-banner")).toHaveLength(1);
    localStorage.removeItem("tb.lab");
    click(content, "Повторить");
    expect(banner(content)).toBeNull();
    expect(content.textContent).toContain("lab ok");
  });

  it("сброс снимает отложенное сохранение: упавший экземпляр не воскрешает стёртый ключ", () => {
    vi.useFakeTimers();
    const P = persisted<{ a: number }>("lab", { a: 1 });
    P.state.a = 2;
    P.save();
    clearPersisted("lab");
    vi.advanceTimersByTime(1000);
    expect(localStorage.getItem("tb.lab")).toBeNull();
    P.state.a = 3;                                        // обычное сохранение работает и дальше
    P.save();
    vi.advanceTimersByTime(300);
    expect(JSON.parse(localStorage.getItem("tb.lab")!)).toEqual({ a: 3 });
  });
});

describe("tabHost: исключения при показе, скрытии и в обратных вызовах", () => {
  it("show() упал — вкладка заменяется баннером, hide() упавшей вызывается один раз", () => {
    const hide = vi.fn();
    const { host, content } = setup({ live: () => ({ el: document.createElement("div"), show() { throw new Error("show failed"); }, hide }) });
    go("live");
    expect(() => host.show()).not.toThrow();
    expect(banner(content)).not.toBeNull();
    expect(hide).toHaveBeenCalledTimes(1);
    expect(content.querySelectorAll("[role=tabpanel]")).toHaveLength(1);
    expect(host.active()).toBe("live");
  });

  it("hide() упал при переключении — следующая вкладка всё равно показывается", () => {
    const { host, p } = setup({ lab: () => ({ el: document.createElement("div"), show() {}, hide() { throw new Error("hide failed"); } }) });
    go("lab"); host.show();
    go("stats");
    expect(() => host.show()).not.toThrow();
    expect(p.shown.stats).toBe(1);
    expect(host.active()).toBe("stats");
  });

  it("dataContext()/refresh() упавшей вкладки не бросают наружу", () => {
    const { host } = setup({
      stats: () => ({ el: document.createElement("div"), show() {}, hide() {}, dataContext() { throw new Error("ctx"); }, refresh() { throw new Error("rf"); } }),
    });
    go("stats"); host.show();
    expect(host.dataContext()).toBeNull();
    expect(() => host.refresh()).not.toThrow();
  });
});
