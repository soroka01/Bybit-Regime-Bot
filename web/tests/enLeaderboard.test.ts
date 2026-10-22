// @vitest-environment jsdom
// Английский интерфейс вкладки «Лучшие стратегии» и вердиктов: нет кириллицы, точные английские тексты, перевод серверных причин (reason_code / русский текст),
// текст «для бота» остаётся русским.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Meta } from "../src/types";
import { cyrillicIn, importEn } from "./enHelpers";

const meta = {
  symbols: ["BTCUSDT"], leaderboard_tfs: ["1h"], klines_available: { BTCUSDT: ["1h"] }, history_days: { "1h": 1095 },
  fetch_estimate: {}, defaults: { fee: 0.001, slippage: 0.0001, days: 365, leverage: 1 },
} as unknown as Meta;

const verdicts: Record<string, string> = { A: "robust", B: "overfit", C: "loss", D: "few" };
const strategies = [
  ...["A", "B", "C", "D"].map((name) => ({ name, desc: name, derivative: false, ready: true, reason: "" })),
  { name: "E", desc: "E", derivative: true, ready: false, reason: "нет данных фандинга/OI/LSR" },
];
const plan = { symbol: "BTCUSDT", tf: "1h", klines: true, derivs: false, strategies };
const item = (name: string) => ({
  strategy: name, desc: name, derivative: false, funding_included: true, data_last: "2026-01-01T00:00:00+00:00", computed_at: "2026-01-01T00:00:00+00:00", from_cache: true,
  oos_days: 45, short_oos: true,
  result: { verdict: verdicts[name], params: { n: 1 }, oos: { calmar: 1, total_return: 0.1, max_drawdown: -0.1, trades: 9, sharpe: 1, green_months: 0.5, avg_month: 0.01 }, is: { total_return: 0.2, max_drawdown: -0.1, sharpe: 1, calmar: 1, trades: 20, green_months: 0.5, avg_month: 0.01 }, score: 1, robust_score: 1, full: {} },
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const btn = (el: HTMLElement, text: string) => [...el.querySelectorAll("button")].find((b) => b.textContent === text) as HTMLButtonElement;

beforeEach(() => {
  vi.useFakeTimers(); localStorage.clear(); location.hash = "#/best";
  vi.stubGlobal("fetch", vi.fn(async (url: string) => json(String(url).includes("/cached")
    ? { items: ["A", "B", "C", "D"].map(item), missing: [], plan, days_effective: 730, data_last: "2026-01-01T00:00:00+00:00", fingerprint: "x" }
    : {})));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("Лучшие стратегии: английский интерфейс", () => {
  it("вкладка без кириллицы; статус, кнопки, сводка и карточки по-английски", async () => {
    const { createLeaderboard } = await importEn(() => import("../src/tabs/leaderboard"));
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    expect(tab.el.querySelector("h1, .page-head h2, .page-head")!.textContent).toContain("Best strategies");
    expect(tab.el.querySelector(".lb-status")!.textContent).toBe("Shown from cache · data as of 2026-01-01 00:00 UTC");
    expect(tab.el.querySelector(".lb-actions .btn.primary")!.textContent).toBe("Shown from cache");
    expect([...tab.el.querySelectorAll(".lb-vchip")].map((b) => b.querySelector(".lb-vchip-t")!.textContent)).toEqual(["robust", "overfit", "few trades", "losing"]);
    expect(tab.el.querySelector(".lb-count")!.textContent).toBe("Showing 4");
    btn(tab.el, "Hide weak").click();
    expect(tab.el.querySelector(".lb-count")!.textContent).toBe("Showing 2 of 4");
    btn(tab.el, "Hide weak").click();
    const card = tab.el.querySelector(".lb-card")!;
    expect(card.textContent).toContain("OOS return");
    expect(card.textContent).toContain("Max drawdown");
    expect(card.querySelector(".cache-chip")!.textContent).toBe("cached");
    expect(card.querySelector(".cache-chip")!.getAttribute("title")).toBe("calculated 2026-01-01 00:00 UTC; data as of 2026-01-01 00:00 UTC");
    expect(cyrillicIn(tab.el)).toEqual([]);
  });

  it("короткий OOS: баннер, примечание «слишком короткий период» с порогом из константы", async () => {
    const { createLeaderboard } = await importEn(() => import("../src/tabs/leaderboard"));
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    expect(tab.el.querySelector(".lb-notice-slot")!.textContent).toContain("Short OOS: the test period is only 45 days");
    const few = [...tab.el.querySelectorAll(".lb-card")].find((c) => c.querySelector("h3")!.textContent === "D")!;
    expect(few.querySelector(".lb-note")!.textContent).toBe("○ test period too short (under 90 days)");
    expect(few.querySelector(".badge")!.getAttribute("title")).toBe("test period too short");
  });

  it("причина от сервера: код reason_code и русский текст сервера переводятся, неизвестный текст остаётся как есть", async () => {
    const { compactInfo, lbReasonText } = await importEn(() => import("../src/tabs/lbCard"));
    expect(lbReasonText("нет свечей — скачайте данные")).toBe("no candles — download the data");
    expect(lbReasonText("нет данных фандинга/OI/LSR")).toBe("no funding/OI/LSR data");
    expect(lbReasonText("недостаточно сделок на in-sample для надёжного подбора")).toBe("not enough in-sample trades for a reliable parameter search");
    expect(lbReasonText("что-то новое", "no_klines")).toBe("no candles — download the data");      // код сильнее текста
    expect(lbReasonText("что-то новое")).toBe("что-то новое");
    expect(compactInfo({ k: "skip", skip: "нет данных фандинга/OI/LSR" }).text).toBe("unavailable — no funding/OI/LSR data");
    expect(compactInfo({ k: "skip", skip: "x", code: "no_derivs" }).text).toBe("unavailable — no funding/OI/LSR data");
    expect(compactInfo({ k: "skip", skip: "нет OI" }).text).toBe("unavailable — нет OI");
    expect(compactInfo({ k: "item", item: { reason: "недостаточно сделок на in-sample для надёжного подбора" } as never }).text).toBe("not enough in-sample trades for a reliable parameter search");
    expect(compactInfo({ k: "item", item: {} as never }).text).toBe("not enough in-sample trades for a reliable parameter search");
    expect(compactInfo({ k: "busy" }).text).toBe("server busy — try again later (“Calculate missing”)");
  });

  it("компактная карточка стратегии без данных по деривативам: английский текст причины", async () => {
    const { createLeaderboard } = await importEn(() => import("../src/tabs/leaderboard"));
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    const compact = tab.el.querySelector(".lb-card.compact")!;
    expect(compact.textContent).toContain("unavailable — no funding/OI/LSR data");
    expect(compact.textContent).toContain("derivatives");
    expect(cyrillicIn(compact)).toEqual([]);
  });

  it("«Copy for bot»: подпись английская, строка результата для бота — русская (протокол)", async () => {
    const copied: string[] = [];
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn(async (s: string) => { copied.push(s); }) } });
    const { createLeaderboard } = await importEn(() => import("../src/tabs/leaderboard"));
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    (tab.el.querySelector(".lb-card .lb-btns button:nth-child(2)") as HTMLButtonElement).click();
    await flush();
    expect(copied[0]).toMatch(/Стратегия: A \| Таймфрейм: 1h/);
    expect(copied[0]).toMatch(/Результат: OOS .*; IS доход \+20%/);
  });

  it("вердикты и порядок: английские тексты, пороги в подсказках", async () => {
    const v = await importEn(() => import("../src/ui/verdicts"));
    expect(v.VERDICTS.robust.text).toBe("robust");
    expect(v.VERDICTS.loss.text).toBe("losing in OOS");
    expect(v.VERDICTS.robust.hint).toBe("OOS is positive, enough trades, Sharpe OOS > 0.5, average month > 0, green months ≥ 40% and the result is not much worse than IS");
    expect(v.ORDER_TEXT).toBe("✓ robust → ! overfit → ○ few trades → ✕ losing");
    expect(v.verdictInfo("mystery").text).toBe("unknown");
    for (const k of v.VERDICT_ORDER) expect(`${v.VERDICTS[k].text} ${v.VERDICTS[k].hint} ${v.VERDICTS[k].warnTitle} ${v.VERDICTS[k].warnBody}`).not.toMatch(/[А-Яа-яЁё]/);
  });

  it("статус расчёта и подписи кнопки по-английски", async () => {
    const { initialModel, runLabel, statusText } = await importEn(() => import("../src/tabs/lbState"));
    const m = initialModel();
    expect(runLabel(m)).toBe("Run");
    Object.assign(m, { phase: "cached", readyCount: 10, missingCount: 4, dataLast: "2025-03-01T12:30:00+00:00" });
    expect(statusText(m)).toBe("From cache: 6 of 10. 4 left to calculate.");
    expect(runLabel(m)).toBe("Calculate missing (4)");
    Object.assign(m, { phase: "running", done: 2, total: 10, retry: { k: 2, n: 4, name: "EMA", waitedS: 20 } });
    expect(statusText(m)).toBe("Calculating 3 of 10… waiting for the server, attempt 2/4 (EMA, waited 20 s)");
  });
});
