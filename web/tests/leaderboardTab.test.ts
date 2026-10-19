// @vitest-environment jsdom
// Интеграционные тесты вкладки «Лучшие стратегии» (jsdom + подменённый fetch): нет «зависания» в running, busy-повторы, сброс при смене настроек.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createLeaderboard } from "../src/tabs/leaderboard";
import type { Meta } from "../src/types";

const meta = {
  symbols: ["BTCUSDT"], leaderboard_tfs: ["1h"], klines_available: { BTCUSDT: ["1h"] }, history_days: { "1h": 1095 },
  fetch_estimate: {}, defaults: { fee: 0.00055, slippage: 0.0001, days: 365, leverage: 1 },
} as unknown as Meta;

const plan = {
  symbol: "BTCUSDT", tf: "1h", klines: true, derivs: false,
  strategies: [
    { name: "A", desc: "a", derivative: false, ready: true, reason: "" },
    { name: "B", desc: "b", derivative: false, ready: true, reason: "" },
  ],
};
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const item = (name: string, verdict: string) => ({
  strategy: name, desc: name, derivative: false, funding_included: true, data_last: "2026-01-01T00:00:00+00:00", computed_at: "2026-01-01T00:00:00+00:00",
  result: { verdict, params: { n: 1 }, oos: { calmar: 1, total_return: 0.1, max_drawdown: -0.1, trades: 9 }, is: { total_return: 0.2 }, score: 1, robust_score: 1, full: {} },
});

const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const status = (el: HTMLElement) => el.querySelector(".lb-status")!.textContent;

beforeEach(() => { vi.useFakeTimers(); localStorage.clear(); location.hash = "#/best"; });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function stub(handler: (url: string, body: Record<string, unknown>) => Response) {
  const f = vi.fn(async (url: string, init: RequestInit = {}) => handler(url, init.body ? JSON.parse(init.body as string) : {}));
  vi.stubGlobal("fetch", f);
  return f;
}
const cached = (items: unknown[] = []) => json(200, { items, missing: plan.strategies.map((s) => s.name).filter((n) => !(items as { strategy: string }[]).some((i) => i.strategy === n)), plan, days_effective: 730, data_last: "2026-01-01T00:00:00+00:00", fingerprint: "x" });

describe("вкладка рейтинга", () => {
  it("расчёт доходит до конца, карточки в порядке ярусов, статус не «Считаю»", async () => {
    stub((url, b) => (url.includes("/cached") ? cached() : json(200, item(String(b.strategy), b.strategy === "A" ? "loss" : "robust"))));
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    expect(tab.el.querySelectorAll(".lb-card").length).toBe(2);
    (tab.el.querySelector(".lb-actions .btn.primary") as HTMLButtonElement).click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(status(tab.el)).not.toMatch(/Считаю/);
    const names = [...tab.el.querySelectorAll(".lb-card h3")].map((h) => h.textContent);
    expect(names).toEqual(["B", "A"]);            // robust выше loss
  });

  it("A1: неизвестный вердикт и мусорный ранг не вешают вкладку в «Считаю…»", async () => {
    localStorage.setItem("tb.best", JSON.stringify({ rank: "garbage" }));
    stub((url, b) => (url.includes("/cached") ? cached() : json(200, item(String(b.strategy), "mystery"))));
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    (tab.el.querySelector(".lb-actions .btn.primary") as HTMLButtonElement).click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(status(tab.el)).not.toMatch(/Считаю/);
    expect((tab.el.querySelector(".lb-actions .btn.primary") as HTMLButtonElement).textContent).toBe("Показано из кэша");
    expect(tab.el.querySelector(".progress")!.hasAttribute("hidden")).toBe(true);
  });

  it("A1: исключение внутри отрисовки не оставляет вкладку в running", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    stub((url, b) => (url.includes("/cached") ? cached() : json(200, { ...item(String(b.strategy), "robust"), result: { verdict: "robust", oos: { calmar: 1 }, is: null, params: null } })));
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    (tab.el.querySelector(".lb-actions .btn.primary") as HTMLButtonElement).click();
    await vi.advanceTimersByTimeAsync(10); await flush();
    expect(status(tab.el)).not.toMatch(/Считаю/);
    expect(tab.el.querySelector(".lb-actions .btn.primary")!.textContent).not.toBe("Считаю…");
    expect(tab.el.querySelectorAll(".lb-card").length).toBe(2);           // список отрисован, битые карточки заменены заглушками
    expect(tab.el.textContent).toMatch(/не удалось отобразить/);
    err.mockRestore();
  });

  it("A2: сервер занят — «ожидаю сервер, попытка k/4», после 4 попыток стратегия помечена «сервер занят», расчёт идёт дальше", async () => {
    const f = stub((url, b) => {
      if (url.includes("/cached")) return cached();
      return b.strategy === "A" ? json(429, { detail: { code: "busy", message: "занят", waited_s: 20 } }) : json(200, item("B", "robust"));
    });
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    (tab.el.querySelector(".lb-actions .btn.primary") as HTMLButtonElement).click();
    await vi.advanceTimersByTimeAsync(0); await flush();
    await vi.advanceTimersByTimeAsync(100); await flush();
    expect(status(tab.el)).toMatch(/ожидаю сервер, попытка 2\/4/);
    await vi.advanceTimersByTimeAsync(20_000); await flush();
    expect(status(tab.el)).not.toMatch(/Считаю/);
    const cards = [...tab.el.querySelectorAll(".lb-card")].map((c) => c.textContent);
    expect(cards.some((t) => /A/.test(t!) && /сервер занят/.test(t!))).toBe(true);
    expect(cards.some((t) => /B/.test(t!) && /OOS|Доход/.test(t!))).toBe(true);
    const itemCalls = f.mock.calls.filter((c) => String(c[0]).includes("/item"));
    expect(itemCalls.filter((c) => JSON.parse(c[1]!.body as string).strategy === "A").length).toBe(4);
  });

  it("A3: смена входов рейтинга сбрасывает карточки (нет смешения старых и новых)", async () => {
    let call = 0;
    stub((url) => (url.includes("/cached") ? (++call === 1 ? cached([item("A", "robust"), item("B", "robust")]) : cached()) : json(200, item("A", "robust"))));
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    expect(tab.el.querySelectorAll(".lb-card:not(.compact)").length).toBe(2);
    const long = tab.el.querySelector("input[role=switch]") as HTMLInputElement;
    long.checked = true;
    long.dispatchEvent(new Event("change"));
    await flush();
    expect(tab.el.querySelectorAll(".lb-card:not(.compact)").length).toBe(0);   // новые настройки — кэш пуст
  });

  it("A15: ввод в слайдер плеча — один запрос к кэшу через 150 мс", async () => {
    const f = stub(() => cached());
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    f.mockClear();
    const range = tab.el.querySelector("input[type=range]") as HTMLInputElement;
    for (const v of ["1.5", "2", "2.5"]) { range.value = v; range.dispatchEvent(new Event("input")); }
    await vi.advanceTimersByTimeAsync(149);
    expect(f).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(5); await flush();
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("A16: hide() не прерывает расчёт; Остановить перерисовывает", async () => {
    let release: (() => void) | null = null;
    stub((url, b) => {
      if (url.includes("/cached")) return cached();
      return new Promise<Response>((res) => { release = () => res(json(200, item(String(b.strategy), "robust"))); }) as unknown as Response;
    });
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    (tab.el.querySelector(".lb-actions .btn.primary") as HTMLButtonElement).click();
    await flush();
    expect(status(tab.el)).toMatch(/Считаю 1 из 2/);
    tab.hide();
    expect(status(tab.el)).toMatch(/Считаю/);
    release!(); await flush();
    expect(status(tab.el)).toMatch(/Считаю 2 из 2/);
    tab.show();                                   // показ во время расчёта не перезапускает загрузку кэша
    const stop = [...tab.el.querySelectorAll(".lb-actions button")].find((b) => b.textContent === "Остановить") as HTMLButtonElement;
    stop.click();
    await flush();
    expect(status(tab.el)).not.toMatch(/Считаю/);
  });
});
