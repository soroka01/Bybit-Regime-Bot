// @vitest-environment jsdom
// Смена рынка во вкладках (jsdom + подменённый fetch): плечо блокируется, «только лонг» фиксируется,
// комиссия переходит на умолчание рынка, пока её не правили; в запрос уходит эффективное плечо/лонг/рынок.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Meta } from "../src/types";

// тяжёлые графики не нужны для проверки логики панели
vi.mock("../src/tabs/resultview", () => ({
  createResultView: () => ({
    el: document.createElement("div"), slot: document.createElement("div"),
    setBusy() {}, setError() {}, setData() {}, destroy() {},
  }),
}));
vi.mock("../src/tabs/sweepPanel", () => ({
  createSweepPanel: () => ({ el: document.createElement("div"), inputsChanged() {}, clear() {}, fillAxes() {} }),
}));

const { createLab } = await import("../src/tabs/lab");
const { createLeaderboard } = await import("../src/tabs/leaderboard");
const { createPortfolio } = await import("../src/tabs/portfolio");

const param = { min: 2, max: 50, step: 1, default: 12, kind: "int", grid: [5, 12, 20] };
const meta = {
  strategies: [
    { name: "EMA cross", desc: "ema", derivative: false, params: { fast: param } },
    { name: "OI flush", desc: "oi", derivative: true, params: { n: param } },
  ],
  timeframes: ["1h"], symbols: ["BTCUSDT"], available: { BTCUSDT: ["1h"] }, klines_available: { BTCUSDT: ["1h"] },
  leaderboard_tfs: ["1h"], portfolio_tfs: ["1h", "4h"], base_tf: "1h", timeframe_minutes: { "1h": 60 }, history_days: { "1h": 1095 },
  fetch_estimate: {}, walkforward_levs: [1],
  defaults: { fee: 0.00055, slippage: 0.0002, days: 365, leverage: 1 }, overlay_defaults: { htf: 50, stop: 3 },
  legs: [{ strategy: "EMA cross", tf: "1h", params: { fast: 12 }, weight: 0.5, long_only: false }],
} as unknown as Meta;

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const plan = { symbol: "BTCUSDT", tf: "1h", klines: true, derivs: false, strategies: [{ name: "A", desc: "a", derivative: false, ready: true, reason: "" }] };

type Call = { url: string; body: Record<string, unknown> };
function stub(): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const body = init.body ? JSON.parse(init.body as string) : {};
    calls.push({ url, body });
    if (url.includes("/cached")) return json({ items: [], missing: ["A"], plan, days_effective: 730, data_last: null, fingerprint: "x" });
    return json({});
  }));
  return calls;
}
const last = (calls: Call[], part: string) => [...calls].reverse().find((c) => c.url.includes(part))!.body;
const seg = (el: HTMLElement, text: string) => [...el.querySelectorAll(".segmented button")].find((b) => b.textContent === text) as HTMLButtonElement;
const numBox = (el: HTMLElement, label: string) => el.querySelector(`input[type=number][aria-label="${label}"]`) as HTMLInputElement;
const toggleOf = (el: HTMLElement, label: string) =>
  [...el.querySelectorAll("label.toggle")].find((l) => l.textContent === label)!.querySelector("input") as HTMLInputElement;

beforeEach(() => {
  vi.useFakeTimers(); localStorage.clear();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("Лаборатория: рынок", () => {
  it("спот блокирует плечо и «только лонг», шлёт market=spot, плечо 1, long_only, комиссию 0.10 %", async () => {
    const calls = stub();
    localStorage.setItem("tb.lab", JSON.stringify({ leverage: 20, longOnly: false }));
    const tab = createLab(meta);
    tab.show();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(last(calls, "/api/backtest")).toMatchObject({ market: "perp", leverage: 20, long_only: false, fee: 0.00055 });
    expect(numBox(tab.el, "Плечо").disabled).toBe(false);

    seg(tab.el, "Спот").click();
    await vi.advanceTimersByTimeAsync(400); await flush();
    expect(last(calls, "/api/backtest")).toMatchObject({ market: "spot", leverage: 1, long_only: true, fee: 0.001 });
    expect(numBox(tab.el, "Плечо").disabled).toBe(true);
    expect(tab.el.querySelector(".lev-presets button")!.hasAttribute("disabled")).toBe(true);
    expect(toggleOf(tab.el, "Только лонг").disabled).toBe(true);
    expect(toggleOf(tab.el, "Только лонг").checked).toBe(true);
    expect(numBox(tab.el, "Комиссия").value).toBe("0.100");
    expect(tab.el.textContent).toContain("свечи перпа как приближение спота");

    seg(tab.el, "Деривативы (перп)").click();            // обратно: прежние плечо и лонг, комиссия по умолчанию перпа
    await vi.advanceTimersByTimeAsync(400); await flush();
    expect(last(calls, "/api/backtest")).toMatchObject({ market: "perp", leverage: 20, long_only: false, fee: 0.00055 });
    expect(numBox(tab.el, "Плечо").disabled).toBe(false);
    expect(toggleOf(tab.el, "Только лонг").disabled).toBe(false);
    expect(numBox(tab.el, "Комиссия").value).toBe("0.055");
  });

  it("комиссия, правленная вручную, при смене рынка не меняется", async () => {
    const calls = stub();
    const tab = createLab(meta);
    tab.show();
    await vi.advanceTimersByTimeAsync(10); await flush();
    const fee = numBox(tab.el, "Комиссия");
    fee.value = "0.07";
    fee.dispatchEvent(new Event("input"));
    await vi.advanceTimersByTimeAsync(400); await flush();
    seg(tab.el, "Спот").click();
    await vi.advanceTimersByTimeAsync(400); await flush();
    expect(last(calls, "/api/backtest")).toMatchObject({ market: "spot" });
    expect(last(calls, "/api/backtest").fee).toBeCloseTo(0.0007, 10);
    expect(parseFloat(numBox(tab.el, "Комиссия").value)).toBe(0.07);
  });

  it("сохранённый рынок восстанавливается, мусор — нет", async () => {
    stub();
    localStorage.setItem("tb.lab", JSON.stringify({ market: "spot", leverage: 50 }));
    const tab = createLab(meta);
    expect(numBox(tab.el, "Плечо").disabled).toBe(true);
    expect(seg(tab.el, "Спот").getAttribute("aria-pressed")).toBe("true");
    localStorage.setItem("tb.lab", JSON.stringify({ market: "margin", leverage: 500 }));
    const tab2 = createLab(meta);
    expect(seg(tab2.el, "Деривативы (перп)").getAttribute("aria-pressed")).toBe("true");
    expect(numBox(tab2.el, "Плечо").value).toBe("1");
  });
});

describe("Портфель: рынок", () => {
  it("спот: ноги принудительно только лонг, плечо 1, market=spot", async () => {
    const calls = stub();
    localStorage.setItem("tb.portfolio", JSON.stringify({ leverage: 10 }));
    const tab = createPortfolio(meta);
    tab.show();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(last(calls, "/api/portfolio")).toMatchObject({ market: "perp", leverage: 10 });
    seg(tab.el, "Спот").click();
    await vi.advanceTimersByTimeAsync(400); await flush();
    const b = last(calls, "/api/portfolio") as { legs: { long_only: boolean }[] };
    expect(b).toMatchObject({ market: "spot", leverage: 1, fee: 0.001 });
    expect(b.legs.every((l) => l.long_only)).toBe(true);
    expect(toggleOf(tab.el.querySelector(".leg") as HTMLElement, "Только лонг").disabled).toBe(true);
  });
});

describe("Рейтинг: рынок", () => {
  it("спот: запрос кэша с market=spot, плечо 1, long_only, комиссия 0.001; плечо заблокировано", async () => {
    const calls = stub();
    localStorage.setItem("tb.best", JSON.stringify({ leverage: 20 }));
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    expect(last(calls, "/cached")).toMatchObject({ market: "perp", leverage: 20, long_only: false, fee: 0.00055 });
    expect(tab.el.textContent).toContain("бот ограничен 3×");
    seg(tab.el, "Спот").click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(last(calls, "/cached")).toMatchObject({ market: "spot", leverage: 1, long_only: true, fee: 0.001 });
    expect(numBox(tab.el, "Плечо").disabled).toBe(true);
    expect(toggleOf(tab.el, "Только лонг").disabled).toBe(true);
    expect(tab.el.textContent).not.toContain("бот ограничен 3×");
  });
});
