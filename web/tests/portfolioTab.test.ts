// @vitest-environment jsdom
// Вкладка «Портфель» (jsdom + подменённый fetch): шкала суммарного веса и предупреждения, импорт ног живого бота, карточки ног.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Meta } from "../src/types";

vi.mock("../src/tabs/resultview", () => ({
  createResultView: () => ({
    el: document.createElement("div"), slot: document.createElement("div"),
    setBusy() {}, setError() {}, setData() {}, destroy() {},
  }),
}));

const { createPortfolio } = await import("../src/tabs/portfolio");

const sp = (min: number, max: number, def: number) => ({ min, max, step: 1, default: def, kind: "int" as const, grid: [min, max] });
const meta = {
  strategies: [
    { name: "EMA cross", desc: "ema", derivative: false, params: { fast: sp(2, 50, 12) } },
    { name: "OI flush", desc: "oi", derivative: true, params: { n: sp(2, 50, 10) } },
  ],
  timeframes: ["1h"], symbols: ["BTCUSDT"], available: { BTCUSDT: ["1h"] }, klines_available: { BTCUSDT: ["1h"] },
  leaderboard_tfs: ["1h"], portfolio_tfs: ["1h", "4h"], base_tf: "1h", timeframe_minutes: { "1h": 60 }, history_days: { "1h": 1095 },
  fetch_estimate: {}, walkforward_levs: [1],
  defaults: { fee: 0.001, slippage: 0.0002, days: 365, leverage: 1 }, overlay_defaults: { htf: 50, stop: 3 },
  legs: [
    { strategy: "EMA cross", tf: "1h", params: { fast: 12 }, weight: 0.5, long_only: false },
    { strategy: "OI flush", tf: "4h", params: { n: 10 }, weight: 0.5, long_only: true },
  ],
} as unknown as Meta;

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
type Call = { url: string; body: Record<string, unknown> };

function stub(live: unknown): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, body: init.body ? JSON.parse(init.body as string) : {} });
    if (url.includes("/api/live")) return live === "fail" ? new Response("{}", { status: 500 }) : json(live);
    return json({});
  }));
  return calls;
}
const btn = (el: HTMLElement, text: string) => [...el.querySelectorAll("button")].find((b) => b.textContent?.includes(text)) as HTMLButtonElement;
const lastBody = (calls: Call[]) => [...calls].reverse().find((c) => c.url.includes("/api/portfolio"))!.body as { legs: { strategy: string; tf: string; weight: number; params: Record<string, number> }[] };

beforeEach(() => {
  vi.useFakeTimers(); localStorage.clear(); document.body.replaceChildren();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("Портфель: ноги карточками и сумма весов", () => {
  it("по умолчанию Σ = 100 %: в пределах лимита, предупреждений нет; у каждой ноги карточка с весом", () => {
    stub({});
    const tab = createPortfolio(meta);
    expect(tab.el.querySelectorAll(".leg-card")).toHaveLength(2);
    expect(tab.el.querySelector(".wsum-val")!.textContent).toBe("Σ весов 100%");
    expect(tab.el.querySelector(".wbar")!.classList.contains("ok")).toBe(true);
    expect(tab.el.querySelectorAll(".wsum-lines .notice")).toHaveLength(0);
  });

  it("вес > 100 %: предупреждение про лимит живого бота; вес 0 — отметка карточки и отдельное сообщение", () => {
    stub({});
    const tab = createPortfolio(meta);
    const w = tab.el.querySelectorAll<HTMLInputElement>('.leg-card input[type=number][aria-label="Вес"]');
    w[0]!.value = "1"; w[0]!.dispatchEvent(new Event("input"));
    expect(tab.el.querySelector(".wsum-val")!.textContent).toBe("Σ весов 150%");
    expect(tab.el.querySelector(".wbar")!.classList.contains("over")).toBe(true);
    expect(tab.el.querySelector(".wsum-lines")!.textContent).toContain("Живой бот ограничивает суммарный вес 100");
    w[1]!.value = "0"; w[1]!.dispatchEvent(new Event("input"));
    expect(tab.el.querySelectorAll(".leg-card")[1]!.classList.contains("is-zero")).toBe(true);
    expect(tab.el.querySelector(".wsum-lines")!.textContent).toContain("Нога 2 с весом 0");
    expect(tab.el.querySelector(".wbar")!.getAttribute("aria-valuenow")).toBe("1");
  });

  it("добавить и удалить ногу: счётчик карточек и запрос меняются; последнюю ногу удалить нельзя", async () => {
    const calls = stub({});
    const tab = createPortfolio(meta);
    tab.show();
    await vi.advanceTimersByTimeAsync(10); await flush();
    btn(tab.el, "Добавить ногу").click();
    await vi.advanceTimersByTimeAsync(400); await flush();
    expect(tab.el.querySelectorAll(".leg-card")).toHaveLength(3);
    expect(lastBody(calls).legs).toHaveLength(3);
    (tab.el.querySelector('button[aria-label="Удалить ногу 3"]') as HTMLButtonElement).click();
    (tab.el.querySelector('button[aria-label="Удалить ногу 2"]') as HTMLButtonElement).click();
    expect(tab.el.querySelectorAll(".leg-card")).toHaveLength(1);
    expect((tab.el.querySelector('button[aria-label="Удалить ногу 1"]') as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("Портфель: ноги из localStorage приводятся к допустимым (F1)", () => {
  const stored = (legs: unknown) => localStorage.setItem("tb.portfolio", JSON.stringify({ legs }));
  it("вес зажимается в [0; 1,5], параметры — в диапазон стратегии, неизвестные стратегии и таймфреймы отбрасываются", async () => {
    const calls = stub({});
    stored([
      { strategy: "EMA cross", tf: "1h", weight: 1e9, long_only: false, params: { fast: 1e9, лишний: 5 } },
      { strategy: "OI flush", tf: "4h", weight: -3, long_only: true, params: {} },
      { strategy: "Нет такой", tf: "1h", weight: 0.5, long_only: false, params: {} },
      { strategy: "EMA cross", tf: "15m", weight: 0.5, long_only: false, params: {} },
    ]);
    const tab = createPortfolio(meta);
    tab.show();
    await vi.advanceTimersByTimeAsync(400); await flush();
    const legs = lastBody(calls).legs;
    expect(legs.map((l) => [l.strategy, l.tf, l.weight])).toEqual([["EMA cross", "1h", 1.5], ["OI flush", "4h", 0]]);
    expect(legs[0]!.params).toEqual({ fast: 50 });          // 1e9 -> max спецификации, лишний параметр отброшен
    expect(legs[1]!.params).toEqual({ n: 10 });             // отсутствует -> по умолчанию
  });

  it("params не словарь чисел (массив / строка вместо числа) — сохранённые ноги отвергаются целиком и берутся ноги по умолчанию", async () => {
    for (const bad of [[1, 2], { fast: "12" }, { fast: null }]) {
      localStorage.clear();
      stored([{ strategy: "EMA cross", tf: "1h", weight: 0.9, long_only: false, params: bad }]);
      const tab = createPortfolio(meta);
      expect(tab.el.querySelectorAll(".leg-card"), JSON.stringify(bad)).toHaveLength(2);        // умолчание из meta.legs
      expect(tab.el.querySelector(".wsum-val")!.textContent).toBe("Σ весов 100%");
    }
  });
});

describe("Портфель: «Взять ноги живого бота»", () => {
  const snap = {
    available: true,
    legs: [
      { key: "a", strategy: "OI flush", tf: "4h", params: { n: 99 }, weight: 0.3, enabled: true, signal: 0, updated: null },
      { key: "b", strategy: "EMA cross", tf: "1h", params: { fast: 20 }, weight: 0.2, enabled: true, signal: 0, updated: null },
      { key: "c", strategy: "EMA cross", tf: "4h", params: { fast: 5 }, weight: 0.5, enabled: false, signal: 0, updated: null },
    ],
  };

  it("подставляет включённые ноги с весами и параметрами по спецификации и пересчитывает бэктест", async () => {
    const calls = stub(snap);
    const tab = createPortfolio(meta);
    tab.show();
    await vi.advanceTimersByTimeAsync(10); await flush();
    btn(tab.el, "живого бота").click();
    await vi.advanceTimersByTimeAsync(400); await flush();
    expect(calls.some((c) => c.url.includes("/api/live"))).toBe(true);
    const legs = lastBody(calls).legs;
    expect(legs.map((l) => `${l.strategy}@${l.tf}`)).toEqual(["OI flush@4h", "EMA cross@1h"]);
    expect(legs[0]!.params).toEqual({ n: 50 });          // 99 зажато в диапазон спецификации
    expect(legs.map((l) => l.weight)).toEqual([0.3, 0.2]);
    expect(tab.el.querySelector(".leg-import")!.textContent).toContain("Загружено ног: 2");
    expect(tab.el.querySelector(".leg-import")!.textContent).toContain("Выключенных пропущено: 1");
    expect(tab.el.querySelector(".wsum-val")!.textContent).toBe("Σ весов 50%");
  });

  it("снимок с признаком long_only: ноги загружаются без примечаний про «только лонг»", async () => {
    const withFlag = { available: true, legs: snap.legs.map((l) => ({ ...l, long_only: true })) };
    stub(withFlag);
    const tab = createPortfolio(meta);
    btn(tab.el, "живого бота").click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(tab.el.querySelector(".leg-import")!.textContent).toContain("Загружено ног: 2");
    expect(tab.el.querySelector(".leg-import")!.textContent).not.toContain("только лонг");
  });

  it("бот не запущен или сервер недоступен: сообщение, ноги не меняются", async () => {
    stub({ available: false, reason: "Бот не запущен" });
    const tab = createPortfolio(meta);
    btn(tab.el, "живого бота").click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(tab.el.querySelector(".leg-import")!.textContent).toContain("Бот не запущен");
    expect(tab.el.querySelectorAll(".leg-card")).toHaveLength(2);
    stub("fail");
    btn(tab.el, "живого бота").click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(tab.el.querySelector(".leg-import")!.textContent).toContain("Снимок бота недоступен");
    expect(tab.el.querySelectorAll(".leg-card")).toHaveLength(2);
    expect(btn(tab.el, "живого бота").disabled).toBe(false);
  });
});
