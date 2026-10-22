// @vitest-environment jsdom
// Английский интерфейс Лаборатории (F1): вкладка, вид результата, диалог подбора параметров, панель развёртки, рынок/плечо и описания стратегий.
// Русский вывод закрыт прежними тестами (labUi, resultview, leverage, market, marketTabs…); здесь — отсутствие кириллицы и точные английские строки.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BacktestResult, Meta, OptResult, SweepResult, TradeRow } from "../src/types";
import { cyrillicIn, importEn } from "./enHelpers";
import { testMeta } from "./fixtures/meta";

// графики не нужны: проверяем тексты, а не отрисовку
vi.mock("../src/charts/line", () => ({ lineChart: () => ({ setData() {}, setLog() {}, setMarkers() {}, destroy() {} }) }));
vi.mock("../src/charts/bars", () => ({ barChart: () => ({ redraw() {}, destroy() {} }) }));
vi.mock("../src/charts/heatmap", () => ({
  SWEEP_METRICS: [{ key: "total_return", label: "Return", fmt: (v: number) => String(v), higherIsBetter: true }],
  monthlyHeatmap: () => document.createElement("div"),
  sweepHeatmap: () => document.createElement("div"),
  sweepProfile: () => ({ el: document.createElement("div"), destroy() {} }),
}));

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

const trade = (side: "long" | "short", ret: number, i: number): TradeRow =>
  ({ entry: `2026-01-0${i + 1} 00:00:00`, exit: `2026-01-0${i + 1} 12:00:00`, side, ret, bars: 5, entry_price: 100, exit_price: 101 });

const result = (over: Partial<BacktestResult> = {}, days = 365): BacktestResult => ({
  metrics: {
    total_return: 0.42, buy_hold_return: -0.02, cagr: 0.4, max_drawdown: -0.12, sharpe: 1.9, calmar: 3.3, trades: 4, win_rate: 0.5,
    avg_month: 0.03, median_month: 0.02, months_15plus: 0.1, green_months: 0.6, liquidations: 0, worst_month: -0.08, profit_factor: 1.5, avg_trade: 0.01, sortino: 2.4, exposure: 0.8, bh_max_drawdown: -0.4,
  },
  data_range: { start: "2025-01-01T00:00:00Z", end: new Date(Date.UTC(2025, 0, 1) + days * 86400000).toISOString(), bars: 100, step_min: 60 },
  t: [1, 2], equity: [1, 1.1], bh: [1, 1], drawdown: [0, -0.1], price: [100, 101],
  markers: { entries: [], exits: [], truncated: false }, monthly: [], yearly: [],
  trades: [trade("long", 0.05, 0), trade("short", -0.02, 1), trade("long", -0.01, 2), trade("short", 0.03, 3)], trades_total: 4,
  ...over,
} as BacktestResult);

const metrics = (ret: number) => ({ total_return: ret, max_drawdown: -0.1, sharpe: 1.2, trades: 30, win_rate: 0.5, green_months: 0.6 });
const optResult = (over: Partial<OptResult> = {}): OptResult => ({
  results: [
    { params: { fast: 12 }, score: 1, robust_score: 1, is: metrics(0.5), oos: metrics(0.2), full: metrics(0.4), verdict: "robust", curve: { t: [1, 2], equity: [1, 1.1], bh: [1, 1] } },
    { params: { fast: 20 }, score: 0.9, robust_score: 0.8, is: metrics(0.4), oos: metrics(-0.1), full: metrics(0.2), verdict: "loss" },
  ],
  n_combos: 1234, n_valid: 1000, min_trades: 20, criterion: "robust", elapsed: 1.5,
  is_range: { start: "2024-01-01", end: "2025-01-01", bars: 100, days: 366 }, oos_range: { start: "2025-01-02", end: "2025-03-02", bars: 50, days: 59 },
  oos_days: 59, short_oos: true, buy_hold_oos: 0.05, param_specs: {}, days_effective: 365, from_cache: true,
  ...over,
} as unknown as OptResult);

const label = (el: HTMLElement, text: string) => [...el.querySelectorAll("label, .field-label")].some((l) => l.textContent === text);
const btn = (el: ParentNode, text: string) => [...el.querySelectorAll("button")].find((b) => b.textContent?.trim() === text) as HTMLButtonElement | undefined;

beforeEach(() => {
  vi.useFakeTimers(); localStorage.clear(); document.body.replaceChildren();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  // jsdom не умеет модальные <dialog>
  const proto = HTMLDialogElement.prototype as unknown as Record<string, unknown>;
  proto.showModal = function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  proto.close = function (this: HTMLDialogElement) { this.removeAttribute("open"); this.dispatchEvent(new Event("close")); };
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("English Lab: вкладка", () => {
  const mount = async (res: BacktestResult = result()) => {
    vi.stubGlobal("fetch", vi.fn(async () => json(res)));
    const { createLab } = await importEn(() => import("../src/tabs/lab"));
    const tab = createLab(testMeta as Meta);
    document.body.append(tab.el);
    tab.show();
    await vi.advanceTimersByTimeAsync(400); await flush();
    return tab;
  };

  it("без кириллицы; подписи панели и кнопки на английском", async () => {
    const tab = await mount();
    expect(cyrillicIn(tab.el)).toEqual([]);
    expect(tab.el.querySelector("h1, h2")!.textContent).toBe("Lab");
    for (const l of ["Symbol", "Timeframe", "Fee", "Slippage"]) expect(label(tab.el, l), l).toBe(true);
    for (const g of ["Period", "Market"]) expect(tab.el.querySelector(`.segmented[aria-label="${g}"]`), g).not.toBeNull();
    expect(btn(tab.el, "Search parameters")).toBeTruthy();
    expect(btn(tab.el, "Reset")).toBeTruthy();
    expect(tab.el.textContent).toContain("Costs and risk");
    expect(tab.el.textContent).toContain("Overlays");
  });

  it("сводки секций и описание стратегии — по-английски", async () => {
    const tab = await mount();
    const text = tab.el.textContent ?? "";
    expect(text).toContain("BTCUSDT · 1h · 1 year");
    expect(text).toContain("default parameters");
    expect(text).toContain("fee 0.10% · slippage 0.020%");
    expect(text).toContain("Long when the fast EMA is above the slow EMA, short otherwise");   // strategies.desc.EMA cross вместо серверного «ema»
    expect(text).toContain("off");
  });

  it("спот: пояснение рынка, сводка «spot», плашка результата без кириллицы", async () => {
    const tab = await mount(result({ market: "spot", effective: { leverage: 1, long_only: true, fee: 0.001 } } as Partial<BacktestResult>));
    btn(tab.el, "Spot")!.click();
    await vi.advanceTimersByTimeAsync(400); await flush();
    const text = tab.el.textContent ?? "";
    expect(text).toContain("Spot: 1× leverage, long only, no funding, default fee 0.10%. The bot trades perp only. Data: perp candles as a spot approximation.");
    expect(text).toContain("BTCUSDT · 1h · 1 year · spot");
    expect(text).toContain("1× · spot, no leverage");
    expect(text).toContain("leverage 1×, long only, no funding, fee 0.100%. Data: perp candles as a spot approximation.");
    expect(cyrillicIn(tab.el)).toEqual([]);
  });
});

describe("English: вид результата", () => {
  const mount = async (r: BacktestResult) => {
    const { createResultView } = await importEn(() => import("../src/tabs/resultview"));
    const v = createResultView();
    document.body.append(v.el);
    v.setData(r);
    return v;
  };

  it("герой, метрики, сделки и подписи — без кириллицы; точные строки", async () => {
    const v = await mount(result());
    expect(cyrillicIn(v.el)).toEqual([]);
    const tiles = v.el.querySelectorAll(".rv-tile");
    expect(tiles[0]!.textContent).toContain("Return");
    expect(tiles[0]!.querySelector(".delta")!.textContent).toContain("+44 pp vs B&H");
    expect(tiles[1]!.querySelector(".delta")!.textContent).toContain("28 pp shallower than B&H");
    expect(tiles[3]!.textContent).toContain("win rate 50%");
    expect(tiles[3]!.textContent).toContain("avg trade +1.00%");
    expect(v.el.textContent).toContain("2025-01-01 — 2026-01-01 · 100 candles");
    expect(v.el.textContent).toContain("Total trades: 4.");
    expect(v.el.textContent).toContain("Download CSV");
    expect(v.el.textContent).toContain("2026-01-01 00:00");            // дата сделки: ISO вместо «дд.мм.гг»
    expect(v.el.textContent).toContain("△ Long");
  });

  it("короткий период: плашка с числом дней (множественное число), аннуализированные метрики", async () => {
    const v = await mount(result({}, 90));
    expect(v.el.querySelector(".rv-notices .notice.warn")!.textContent).toContain("Short period: only 90 days.");
    expect([...v.el.querySelectorAll(".rv-minis .mini")].find((m) => m.textContent?.includes("CAGR"))!.getAttribute("title")).toBe("annualized over 90 days");
    expect(cyrillicIn(v.el)).toEqual([]);
  });

  it("одна сделка: «1 trade» в подсказке CSV, во множественном — «4 trades»", async () => {
    const v = await mount(result({ trades: [trade("long", 0.05, 0)], trades_total: 1 }));
    const csv = btn(v.el, "Download CSV")!;
    expect(csv.title).toBe("Download 1 trade as CSV (UTC, return as a fraction)");
    v.setData(result());
    expect(btn(v.el, "Download CSV")!.title).toBe("Download 4 trades as CSV (UTC, return as a fraction)");
  });
});

describe("English: подбор параметров", () => {
  it("диалог: контекст, критерии, статистика, предупреждения и таблица без кириллицы", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(optResult())));
    const { openOptimize } = await importEn(() => import("../src/tabs/optimize"));
    openOptimize({
      title: "EMA cross",
      body: () => ({ symbol: "BTCUSDT", tf: "1h", days: 365, strategy: "EMA cross", fee: 0.001, slippage: 0.0002, leverage: 3, long_only: true, market: "perp", overlays: { htf: 50, stop: 3 } }) as never,
      onApply: vi.fn(),
      copyText: () => "x",
    });
    await vi.advanceTimersByTimeAsync(10); await flush();
    const dlg = document.querySelector("dialog")! as HTMLElement;
    expect(dlg.querySelector("h2")!.textContent).toBe("Parameter search — EMA cross");
    expect(cyrillicIn(dlg)).toEqual([]);
    const text = dlg.textContent ?? "";
    expect(text).toContain("Perp · 3×");
    expect(text).toContain("long only");
    expect(text).toContain("ATR stop 3×");
    expect(text).toContain("How it works.");
    expect(text).toContain("1,234 combinations");
    expect(text).toContain("calculated in 1.5 s · from cache");
    expect(text).toContain("validation · 59 days");
    expect(text).toContain("Short validation period.");
    expect(text).toContain("With OOS under 90 days the verdict can only be “few trades”.");
    expect(btn(dlg, "Apply best")).toBeTruthy();
    expect(btn(dlg, "Robust")).toBeTruthy();
  });

  it("нет подходящих комбинаций: сообщение с числом сделок и датами IS", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(optResult({ results: [] }))));
    const { openOptimize } = await importEn(() => import("../src/tabs/optimize"));
    openOptimize({
      title: "OI flush",
      body: () => ({ symbol: "BTCUSDT", tf: "4h", days: 730, strategy: "OI flush", fee: 0.001, slippage: 0.0002, leverage: 1, long_only: false, market: "spot", overlays: { htf: null, stop: null } }) as never,
      onApply: vi.fn(),
    });
    await vi.advanceTimersByTimeAsync(10); await flush();
    const dlg = document.querySelector("dialog")! as HTMLElement;
    expect(dlg.textContent).toContain("Nothing to choose from.");
    expect(dlg.textContent).toContain("No combination produced at least 20 trades in-sample (2024-01-01 — 2025-01-01). Extend the period or change the timeframe.");
    expect(dlg.textContent).toContain("2 years");
    expect(cyrillicIn(dlg)).toEqual([]);
  });
});

describe("English: панель развёртки", () => {
  const sweepResult = (): SweepResult => ({
    x: { name: "fast", values: [5, 12, 20] }, y: { name: "slow", values: [20, 40] }, points: null, grids: {}, base: { fast: 12, slow: 40 },
  });

  it("подсказка, подписи осей и кнопки; подпись карты; сообщение об устаревании", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(sweepResult())));
    const { createSweepPanel } = await importEn(() => import("../src/tabs/sweepPanel"));
    const strat = { name: "EMA cross", desc: "", derivative: false, params: { fast: { min: 2, max: 50, step: 1, default: 12, kind: "int", grid: [5, 20] }, slow: { min: 5, max: 90, step: 1, default: 40, kind: "int", grid: [20, 60] } } };
    let k = 0;
    const state = { sx: "", sy: "", sMetric: "total_return", sN: 8 };
    const p = createSweepPanel({
      state, save() {}, strat: () => strat as never, currentParams: () => ({ fast: 12, slow: 40 }),
      body: () => ({ symbol: "BTCUSDT", tf: "1h", strategy: "EMA cross", params: { fast: 12, slow: 40 }, days: 365 + k } as never), apply() {},
    });
    document.body.append(p.el);
    expect(cyrillicIn(p.el)).toEqual([]);
    expect(p.el.querySelector("h3")!.textContent).toBe("Parameter sweep");
    expect(btn(p.el, "Build sweep")).toBeTruthy();
    expect(p.el.textContent).toContain("Pick two different axes and a metric, press “Build sweep”");
    expect(label(p.el, "X axis")).toBe(true);
    expect(label(p.el, "Y axis")).toBe(true);

    btn(p.el, "Build sweep")!.click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(p.el.textContent).toContain("Return · “fast” × “slow” · 3×2.");
    expect(p.el.textContent).toContain("current point");
    expect(p.el.textContent).toContain("click or Enter to apply");
    expect(cyrillicIn(p.el)).toEqual([]);

    k = 1;                                      // изменился период — результат помечен устаревшим
    p.inputsChanged();
    expect(p.el.querySelector(".sw-stale")!.textContent).toContain("Sweep is out of date. The period, fee, leverage, overlays or other parameters changed.");
    expect(btn(p.el, "Rebuild")).toBeTruthy();
  });
});

describe("English: рынок, плечо, описания", () => {
  it("подписи периодов и сводки", async () => {
    const { daysLabel, costsSummary, overlaysSummary, strategySummary } = await importEn(() => import("../src/tabs/labLogic"));
    expect(daysLabel(365)).toBe("1 year");
    expect(daysLabel(730)).toBe("2 years");
    expect(daysLabel(90)).toBe("90 d");
    expect(strategySummary("EMA cross", 2, 5)).toBe("EMA cross · 2 of 5 changed");
    expect(costsSummary({ fee: 0.1, slip: 0.02, longOnly: true })).toBe("fee 0.10% · slippage 0.020% · long only");
    expect(overlaysSummary({ htfOn: true, htf: 50, stopOn: true, stop: 3 })).toBe("HTF EMA 50 · ATR stop 3×");
  });

  it("плечо: подсказка о ликвидации и потолок бота; для текста «для бота» — по-русски", async () => {
    const lev = await importEn(() => import("../src/leverage"));
    expect(lev.liqHintText(100)).toBe("liquidation ≈ at a 0.5% move against the position");
    expect(lev.botLimitNote(20)).toBe("bot capped at 3×");
    expect(lev.botLimitNote(2)).toBeNull();
    expect(lev.botLimitNoteRu(20)).toBe("бот ограничен 3×");
  });

  it("рынок: подписи, пояснения; marketLabel остаётся русским (протокол бота)", async () => {
    const m = await importEn(() => import("../src/market"));
    expect(m.MARKET_OPTIONS.map((o) => o.label)).toEqual(["Derivatives (perp)", "Spot"]);
    expect(m.SPOT_LEV_NOTE).toBe("spot: 1× leverage, no liquidation");
    expect(m.SPOT_LONG_NOTE).toBe("spot: short positions are not possible");
    expect(m.marketNote("perp", true)).toBe("");
    expect(m.marketNote("spot", true)).toBe(`${m.SPOT_RULES} Data: perp candles as a spot approximation. This strategy: funding/OI/LSR signals are computed from perp data.`);
    expect(m.marketLabel("spot")).toBe("Спот");
    expect(m.marketLabel("perp")).toBe("Перп");
  });

  it("описания стратегий: английский по имени, незнакомое имя — серверный текст", async () => {
    await importEn(async () => {});
    const { strategyDesc } = await import("../src/i18n/serverText");
    expect(strategyDesc("RSI reversion", "Покупка")).toBe("Buys oversold, shorts overbought, exits near RSI 50");
    expect(strategyDesc("Brand new", "Покупка")).toBe("Покупка");
  });
});
