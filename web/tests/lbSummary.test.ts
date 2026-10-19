// @vitest-environment jsdom
// Сводка по вердиктам на вкладке «Лучшие стратегии»: клик по плашке фильтрует список, «скрыть слабые» и сброс работают, выбор запоминается.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createLeaderboard } from "../src/tabs/leaderboard";
import type { Meta } from "../src/types";

const meta = {
  symbols: ["BTCUSDT"], leaderboard_tfs: ["1h"], klines_available: { BTCUSDT: ["1h"] }, history_days: { "1h": 1095 },
  fetch_estimate: {}, defaults: { fee: 0.00055, slippage: 0.0001, days: 365, leverage: 1 },
} as unknown as Meta;

const names = ["A", "B", "C", "D"];
const verdicts: Record<string, string> = { A: "robust", B: "overfit", C: "loss", D: "loss" };
const plan = { symbol: "BTCUSDT", tf: "1h", klines: true, derivs: false, strategies: names.map((name) => ({ name, desc: name, derivative: false, ready: true, reason: "" })) };
const item = (name: string) => ({
  strategy: name, desc: name, derivative: false, funding_included: true, data_last: "2026-01-01T00:00:00+00:00", computed_at: "2026-01-01T00:00:00+00:00",
  result: { verdict: verdicts[name], params: { n: 1 }, oos: { calmar: 1, total_return: 0.1, max_drawdown: -0.1, trades: 9 }, is: { total_return: 0.2 }, score: 1, robust_score: 1, full: {} },
});
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const titles = (el: HTMLElement) => [...el.querySelectorAll(".lb-card h3")].map((h) => h.textContent);

beforeEach(() => {
  vi.useFakeTimers(); localStorage.clear(); location.hash = "#/best";
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: names.map(item), missing: [], plan, days_effective: 730, data_last: null, fingerprint: "x" }), { status: 200, headers: { "Content-Type": "application/json" } })));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("сводка по вердиктам", () => {
  it("показывает счётчики и фильтрует по клику, повторный клик снимает фильтр", async () => {
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    const chip = (v: string) => tab.el.querySelector(`.lb-vchip.${v}`) as HTMLButtonElement;
    expect(chip("robust").textContent).toContain("1");
    expect(chip("loss").textContent).toContain("2");
    expect(titles(tab.el)).toEqual(["A", "B", "C", "D"]);
    chip("loss").click();
    expect(titles(tab.el)).toEqual(["C", "D"]);
    expect(chip("loss").getAttribute("aria-pressed")).toBe("true");
    expect(tab.el.querySelector(".lb-count")!.textContent).toBe("Показано 2 из 4");
    chip("loss").click();
    expect(titles(tab.el)).toEqual(["A", "B", "C", "D"]);
  });

  it("«Скрыть слабые» убирает убыточные; место в рейтинге считается по полному списку", async () => {
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    const weak = [...tab.el.querySelectorAll(".lb-sum-tools .chip-btn")].find((b) => b.textContent === "Скрыть слабые") as HTMLButtonElement;
    weak.click();
    expect(titles(tab.el)).toEqual(["A", "B"]);
    expect([...tab.el.querySelectorAll(".lb-rank")].map((r) => r.textContent)).toEqual(["1", "2"]);
    const group = [...tab.el.querySelectorAll(".lb-sum-tools .chip-btn")].find((b) => b.textContent === "По вердиктам") as HTMLButtonElement;
    group.click();
    expect(tab.el.querySelectorAll(".lb-group").length).toBe(2);
  });

  it("виды списка запоминаются между открытиями", async () => {
    const a = createLeaderboard(meta);
    a.show();
    await flush();
    (a.el.querySelector(".lb-vchip.overfit") as HTMLButtonElement).click();
    await vi.advanceTimersByTimeAsync(300);
    const b = createLeaderboard(meta);
    b.show();
    await flush();
    expect(titles(b.el)).toEqual(["B"]);
  });
});
