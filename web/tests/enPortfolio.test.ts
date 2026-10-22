// @vitest-environment jsdom
// Английский интерфейс вкладок «Портфель» и Walk-forward: нет кириллицы, точные английские тексты, множественные формы, тексты «для бота» остаются русскими.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Meta } from "../src/types";
import { cyrillicIn, importEn } from "./enHelpers";

vi.mock("../src/tabs/resultview", () => ({
  createResultView: () => ({ el: document.createElement("div"), slot: document.createElement("div"), setBusy() {}, setError() {}, setData() {}, destroy() {} }),
}));
vi.mock("../src/charts/line", () => ({ lineChart: () => ({ setData() {}, setLog() {}, destroy() {} }) }));
vi.mock("../src/charts/bars", () => ({ barChart: () => ({ redraw() {}, destroy() {} }) }));

const sp = (min: number, max: number, def: number) => ({ min, max, step: 1, default: def, kind: "int" as const, grid: [min, max] });
const meta = {
  strategies: [
    { name: "EMA cross", desc: "ema", derivative: false, params: { fast: sp(2, 50, 12) } },
    { name: "OI flush", desc: "oi", derivative: true, params: { n: sp(2, 50, 10) } },
  ],
  timeframes: ["1h"], symbols: ["BTCUSDT"], available: { BTCUSDT: ["1h"] }, klines_available: { BTCUSDT: ["1h"] },
  leaderboard_tfs: ["1h"], portfolio_tfs: ["1h", "4h"], base_tf: "1h", timeframe_minutes: { "1h": 60 }, history_days: { "1h": 1095 },
  fetch_estimate: {}, walkforward_levs: [1, 5],
  defaults: { fee: 0.001, slippage: 0.0002, days: 365, leverage: 1 }, overlay_defaults: { htf: 50, stop: 3 },
  legs: [
    { strategy: "EMA cross", tf: "1h", params: { fast: 12 }, weight: 0.5, long_only: false },
    { strategy: "OI flush", tf: "4h", params: { n: 10 }, weight: 0.5, long_only: true },
  ],
} as unknown as Meta;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const btn = (el: HTMLElement, text: string) => [...el.querySelectorAll("button")].find((b) => b.textContent?.includes(text)) as HTMLButtonElement;

const wfRow = (strategy: string, o: Record<string, unknown>) => ({
  tf: "1h", strategy, variant: "base", windows: 8, windows_positive: 0.75, total_return: 0.5, cagr: 0.2, buy_hold_return: 0.3, max_drawdown: -0.2, sharpe: 1.2, calmar: 1.5,
  trades: 100, win_rate: 0.5, profit_factor: 1.5, avg_month: 0.02, median_month: 0.01, months_15plus: 0.1, worst_month: -0.1, liquidations: 0, ...o,
});
const wfWindows = ["2025-01-01", "2025-04-01"].flatMap((s) => ["EMA cross", "OI flush"].map((st, k) => ({ tf: "1h", strategy: st, variant: "base", test_start: s, params: "n=1", test_return: k ? -0.05 : 0.1 })));

type Handler = (url: string) => Response;
function stub(handler: Handler) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => handler(url)));
}

beforeEach(() => {
  vi.useFakeTimers(); localStorage.clear(); document.body.replaceChildren();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("Портфель: английский интерфейс", () => {
  it("вкладка без кириллицы, точные подписи, Σ весов и предупреждения", async () => {
    stub(() => json({}));
    const { createPortfolio } = await importEn(() => import("../src/tabs/portfolio"));
    const tab = createPortfolio(meta);
    expect(cyrillicIn(tab.el)).toEqual([]);
    expect(tab.el.querySelector(".wsum-val")!.textContent).toBe("Σ weights 100%");
    expect(tab.el.querySelector(".wsum-head")!.textContent).toContain("live bot limit 100% · max exposure ≈ 1.00× of capital");
    expect(btn(tab.el, "Add leg")).toBeTruthy();
    expect(btn(tab.el, "Import live bot legs")).toBeTruthy();
    expect(tab.el.querySelectorAll(".leg-card")[0]!.getAttribute("aria-label")).toBe("Leg 1");
    // вес > 100 %: предупреждение про лимит бота
    const w = tab.el.querySelectorAll<HTMLInputElement>('.leg-card input[type=number][aria-label="Weight"]');
    w[0]!.value = "1"; w[0]!.dispatchEvent(new Event("input"));
    expect(tab.el.querySelector(".wsum-lines")!.textContent).toContain("The live bot caps the total weight at 100%. At Σ = 150%");
    expect(tab.el.querySelector(".wbar")!.getAttribute("aria-valuetext")).toBe("Σ weights 150%, above the bot limit of 100%");
    expect(cyrillicIn(tab.el)).toEqual([]);
  });

  it("множественное число: одна нога с весом 0 / несколько", async () => {
    stub(() => json({}));
    const { createPortfolio } = await importEn(() => import("../src/tabs/portfolio"));
    const tab = createPortfolio(meta);
    const w = () => tab.el.querySelectorAll<HTMLInputElement>('.leg-card input[type=number][aria-label="Weight"]');
    w()[1]!.value = "0"; w()[1]!.dispatchEvent(new Event("input"));
    expect(tab.el.querySelector(".wsum-lines")!.textContent).toContain("Leg 2 has weight 0 — it does not trade here or in the bot.");
    w()[0]!.value = "0.5"; w()[0]!.dispatchEvent(new Event("input"));
    w()[0]!.value = "0"; w()[0]!.dispatchEvent(new Event("input"));
    expect(tab.el.querySelector(".wsum-lines")!.textContent).toContain("All weights are zero");
    // два нулевых при ненулевой сумме: добавляем ногу
    btn(tab.el, "Add leg").click();
    expect(tab.el.querySelector(".wsum-lines")!.textContent).toContain("Legs 1, 2 have weight 0 — they do not trade here or in the bot.");
  });

  it("импорт ног живого бота: английские сообщения, код причины no_snapshot переводится, неизвестный текст сервера остаётся", async () => {
    const snap = {
      available: true,
      legs: [
        { key: "a", strategy: "OI flush", tf: "4h", params: { n: 99 }, weight: 0.3, enabled: true, signal: 0, updated: null },
        { key: "b", strategy: "EMA cross", tf: "1h", params: { fast: 20 }, weight: 0.2, enabled: true, signal: 0, updated: null },
        { key: "c", strategy: "EMA cross", tf: "4h", params: { fast: 5 }, weight: 0.5, enabled: false, signal: 0, updated: null },
        { key: "d", strategy: "Unknown", tf: "4h", params: {}, weight: 0.5, enabled: true, signal: 0, updated: null },
      ],
    };
    let live: unknown = snap;
    stub((url) => (url.includes("/api/live") ? json(live) : json({})));
    const { createPortfolio } = await importEn(() => import("../src/tabs/portfolio"));
    const tab = createPortfolio(meta);
    btn(tab.el, "Import live bot legs").click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    const note = tab.el.querySelector(".leg-import")!.textContent!;
    expect(note).toContain("Live bot legs. Loaded 2 legs, Σ weights 50%.");
    expect(note).toContain("Disabled skipped: 1.");
    expect(note).toContain("Not imported: Unknown@4h: no such strategy in the dashboard.");
    expect(cyrillicIn(tab.el.querySelector(".leg-import")!)).toEqual([]);
    live = { available: false, reason: "no_snapshot" };
    btn(tab.el, "Import live bot legs").click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(tab.el.querySelector(".leg-import")!.textContent).toBe("Could not import the bot’s legs. Bot snapshot unavailable: the bot is not running or the state database was not found.");
    live = { available: false, reason: "Бот не запущен" };
    btn(tab.el, "Import live bot legs").click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(tab.el.querySelector(".leg-import")!.textContent).toContain("Бот не запущен");     // неизвестный текст сервера — как есть
  });

  it("«Copy for bot»: подписи английские, а текст для бота остаётся русским (протокол core/legspec.py)", async () => {
    stub(() => json({}));
    const copied: string[] = [];
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn(async (s: string) => { copied.push(s); }) } });
    const { createPortfolio } = await importEn(() => import("../src/tabs/portfolio"));
    const tab = createPortfolio(meta);
    btn(tab.el, "Copy for bot").click();
    await flush();
    expect(copied).toHaveLength(1);
    expect(copied[0]).toMatch(/^Портфель: BTCUSDT \| Рынок: Перп \| Плечо: 1x \| Комиссия: .*% \| Проскальзывание: .*% \| Период: 730 дн\. \| Ноги: EMA cross@1h \(fast=12; вес 0\.5\)/);
    expect(copied[0]).toContain("Результат: нет расчёта");
    expect(copied[0]).toContain('Leg("EMA cross", "1h"');
  });
});

describe("Walk-forward: английский интерфейс", () => {
  const rows = [wfRow("EMA cross", {}), wfRow("OI flush", { trades: 10 })];
  const server = (url: string): Response => {
    if (url.includes("/api/walkforward/windows")) return json(wfWindows);
    if (url.includes("/api/walkforward/curve")) return json({ t: [1, 2, 3], equity: [1, 1.1, 1.2], bh: [1, 1, 1] });
    if (url.includes("/api/walkforward")) return json(rows);
    return json({});
  };

  it("таблица, сводка и оценки по-английски; клик по строке открывает матрицу окон без кириллицы", async () => {
    stub(server);
    const { createWalkForward } = await importEn(() => import("../src/tabs/walkforward"));
    const tab = createWalkForward(meta);
    tab.show();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(tab.el.querySelector("table caption")!.textContent).toBe("Walk-forward variants");
    const heads = [...tab.el.querySelectorAll("thead th")].map((th) => th.textContent!.replace(/[▲▼↑↓\s]+$/g, "").trim());
    expect(heads).toEqual(expect.arrayContaining(["Rating", "OOS return", "Max DD", "Avg month", "Trades", "Win rate", "Liq."]));
    expect(tab.el.querySelector(".wf-count")!.textContent).toBe("Showing 2 of 2 variants. Click a row to add or remove a curve (up to 8).");
    const badges = [...tab.el.querySelectorAll("tbody .badge")].map((b) => b.textContent);
    expect(badges.join("|")).toMatch(/stable/);
    expect(badges.join("|")).toMatch(/few trades/);
    expect(tab.el.querySelector(".wf-summary")!.textContent).toContain("of 2 · no filters");
    expect(tab.el.querySelector(".wf-summary")!.textContent).toContain("from 2025-01-01 · 3 months each (last from 2025-04-01)");
    (tab.el.querySelector("tbody tr") as HTMLElement).click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(tab.el.querySelector(".wf-matrix")!.textContent).toContain("Profitable windows");
    expect(tab.el.querySelector(".wf-foot")!.textContent).toMatch(/2 of 2 · 100%/);
    expect(cyrillicIn(tab.el)).toEqual([]);
  });

  it("нет результатов: пустое состояние по-английски со ссылкой на другое плечо", async () => {
    stub(() => json({ detail: { code: "no_results", message: "No results" } }, 404));
    const { createWalkForward } = await importEn(() => import("../src/tabs/walkforward"));
    const tab = createWalkForward(meta);
    tab.show();
    await vi.advanceTimersByTimeAsync(10); await flush();
    const text = tab.el.textContent!;
    expect(text).toContain("No walk-forward results for 1× leverage");
    expect(text).toContain("research.walkforward study (files wf_BTCUSDT*.csv and wf_windows_BTCUSDT*.csv");
    expect(text).toContain("Results exist for leverage:");
  });

  it("wfVerdict: тексты и порог в подсказке по-английски", async () => {
    const { wfVerdict } = await importEn(() => import("../src/tabs/wfLogic"));
    const base = { total_return: 0.5, trades: 100, windows_positive: 0.8, avg_month: 0.02 };
    expect(wfVerdict({ ...base, trades: 10 })).toMatchObject({ key: "few", text: "few trades", hint: "fewer than 30 trades over the whole OOS — conclusions are statistically weak" });
    expect(wfVerdict({ ...base, total_return: -0.1 }).text).toBe("losing");
    expect(wfVerdict(base).text).toBe("stable");
    expect(wfVerdict({ ...base, windows_positive: 0.4 }).text).toBe("unstable");
  });
});
