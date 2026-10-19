// @vitest-environment jsdom
// F1: битый localStorage по КАЖДОМУ сохраняемому ключу вкладки (tb.lab / portfolio / wf / best / live / stats) не должен ронять создание и показ вкладки.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ROUTES, type Route } from "../src/state";
import { createTabHost } from "../src/tabHost";
import type { TabFactory } from "../src/tabs/tab";
import { testMeta } from "./fixtures/meta";

vi.mock("../src/tabs/resultview", () => ({
  createResultView: () => ({ el: document.createElement("div"), slot: document.createElement("div"), setBusy() {}, setError() {}, setData() {}, destroy() {} }),
}));
vi.mock("../src/tabs/sweepPanel", () => ({
  createSweepPanel: () => ({ el: document.createElement("div"), inputsChanged() {}, clear() {}, fillAxes() {} }),
}));
vi.mock("../src/charts/line", () => ({ lineChart: () => ({ setData() {}, setLog() {}, setMarkers() {}, destroy() {} }) }));
vi.mock("../src/charts/bars", () => ({ barChart: () => ({ redraw() {}, destroy() {} }) }));
vi.mock("../src/charts/histogram", () => ({ histogramChart: () => ({ redraw() {}, destroy() {} }) }));
vi.mock("../src/charts/heatmap", () => ({ monthlyHeatmap: () => document.createElement("div"), sweepHeatmap: () => document.createElement("div"), sweepProfile: () => ({ el: document.createElement("div"), destroy() {} }), SWEEP_METRICS: [{ key: "total_return", label: "Доходность", fmt: String, higherIsBetter: true }] }));

const { createLab } = await import("../src/tabs/lab");
const { createPortfolio } = await import("../src/tabs/portfolio");
const { createWalkForward } = await import("../src/tabs/walkforward");
const { createLeaderboard } = await import("../src/tabs/leaderboard");
const { createLive } = await import("../src/tabs/live");
const { createStats } = await import("../src/tabs/stats");

const FACTORIES: Record<Route, TabFactory> = { lab: createLab, portfolio: createPortfolio, wf: createWalkForward, best: createLeaderboard, live: createLive, stats: createStats };

/** Любое имя поля, которое хранит хоть одна вкладка. */
const FIELDS = [
  "symbol", "tf", "strategy", "params", "days", "fee", "feeTouched", "market", "slip", "leverage", "longOnly", "htfOn", "htf", "stopOn", "stop",
  "sx", "sy", "sMetric", "sN", "legs", "rank", "variants", "hideWeak", "vf", "group", "lev", "variant", "minTrades", "q", "selected", "log",
  "range", "ev", "unit", "filter",
];
/** Ядовитые значения (после JSON.stringify): неверные типы, экстремумы, вложенный мусор. NaN/Infinity в JSON невозможны — они приходят как null. */
const POISON: unknown[] = [null, "", "💥", "NaN", -1, 0, 1e308, -1e308, true, false, [], [1, 2, 3], {}, { a: null }, { "EMA cross": null }, { "EMA cross": { fast: "x" } }, [null], ["x"], [{}]];

const RAW: string[] = [
  "{не json", "", "null", "[]", "42", '"строка"', "true", "{}", '{"__proto__":{"polluted":1}}', "[[[[[[[[[[]]]]]]]]]]", "0", '{"a":',
];

const poisonObjects = (): string[] => [
  ...POISON.map((v) => JSON.stringify(Object.fromEntries(FIELDS.map((f) => [f, v])))),
  // точечные: ноги портфеля
  JSON.stringify({ legs: [{ strategy: "EMA cross", tf: "1h", weight: 1e9, long_only: false, params: { fast: 1e9 } }] }),
  JSON.stringify({ legs: [{ strategy: "EMA cross", tf: "1h", weight: -5, long_only: false, params: { fast: null } }] }),
  JSON.stringify({ legs: [{ strategy: "EMA cross", tf: "1h", weight: 0.5, long_only: false, params: [1, 2] }] }),
  JSON.stringify({ legs: [{ strategy: "EMA cross", tf: "1h", weight: 0.5, long_only: false, params: { fast: "12" } }] }),
  JSON.stringify({ legs: [null, 1, "x", [], { strategy: 1 }] }),
  JSON.stringify({ legs: Array.from({ length: 50 }, () => ({ strategy: "EMA cross", tf: "1h", weight: 0.1, long_only: false, params: {} })) }),
  JSON.stringify({ legs: [{ strategy: "Нет такой", tf: "1h", weight: 0.5, long_only: false, params: {} }] }),
  // лаборатория: параметры стратегий
  JSON.stringify({ strategy: "EMA cross", params: { "EMA cross": { fast: 1e9, slow: -1e9 }, "OI flush": null } }),
  JSON.stringify({ strategy: "OI flush", params: { "OI flush": { n: 3.5 } }, tf: "4h", days: 3, symbol: "ETHUSDT" }),
];

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 8; i++) await vi.advanceTimersByTimeAsync(0); };

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  document.body.replaceChildren();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("fetch", vi.fn(async () => json(500, { detail: { code: "boom", message: "тест" } })));
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("битый localStorage не роняет вкладки", () => {
  for (const route of ROUTES) {
    it(`tb.${route}: сырые значения и ядовитые объекты — создание и показ без исключений`, async () => {
      const payloads = [...RAW, ...poisonObjects()];
      for (const payload of payloads) {
        localStorage.setItem(`tb.${route}`, payload);
        let tab: ReturnType<TabFactory> | undefined;
        try {
          tab = FACTORIES[route](testMeta);
          document.body.replaceChildren(tab.el);
          tab.show();
          await flush();
          tab.hide();
        } catch (e) {
          throw new Error(`tb.${route} = ${payload.slice(0, 120)} -> ${(e as Error).stack ?? e}`);
        }
      }
    });
  }

  it("другие вкладки при этом читают свои ключи нетронутыми", () => {
    localStorage.setItem("tb.lab", "{не json");
    localStorage.setItem("tb.stats", JSON.stringify({ range: "7d", unit: "pct", filter: "win" }));
    createLab(testMeta);
    const s = createStats(testMeta);
    expect([...s.el.querySelectorAll('[aria-pressed="true"]')].map((b) => b.textContent)).toContain("1н");      // 7d — кнопка «1н», выбранная из своего ключа
    expect(localStorage.getItem("tb.stats")).toContain('"range":"7d"');
  });

  it("сквозь tabHost: даже если фабрика всё же бросит, остальные вкладки открываются, а сброс чинит именно её ключ", () => {
    const content = document.createElement("main");
    document.body.append(content);
    const links = new Map<Route, HTMLAnchorElement>(ROUTES.map((r) => [r, document.createElement("a")]));
    const boom: TabFactory = () => { if (localStorage.getItem("tb.wf")) throw new Error("walkforward упал"); return createWalkForward(testMeta); };
    const host = createTabHost({ meta: testMeta, factories: { ...FACTORIES, wf: boom }, titles: Object.fromEntries(ROUTES.map((r) => [r, r])) as Record<Route, string>, content, links, layoutTabs() {} });
    localStorage.setItem("tb.wf", "что-то");
    localStorage.setItem("tb.lab", JSON.stringify({ symbol: "ETHUSDT" }));
    location.hash = "#/wf"; host.show();
    expect(content.querySelector(".tab-crash-banner")).not.toBeNull();
    [...content.querySelectorAll("button")].find((b) => b.textContent === "Сбросить сохранённые настройки вкладки")!.click();
    expect(content.querySelector(".tab-crash-banner")).toBeNull();
    expect(localStorage.getItem("tb.wf")).toBeNull();
    expect(localStorage.getItem("tb.lab")).toContain("ETHUSDT");
  });
});
