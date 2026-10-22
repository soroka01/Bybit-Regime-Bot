// @vitest-environment jsdom
// Вкладка «Статистика» на английском: вкладка строится на fixtures тестов (русские серверные тексты) и не должна показывать кириллицу, кроме явно
// разрешённых серверных данных. Определения метрик и предупреждения берутся из словаря (по ключу/коду), а не из серверного русского текста.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Meta, StatsResponse } from "../src/types";
import golden7 from "./fixtures/stats_7d.json";
import goldenAll from "./fixtures/stats_all.json";
import goldenUnavailable from "./fixtures/stats_unavailable.json";
import { richStats } from "./fixtures/statsRich";
import { cyrillicIn, importEn } from "./enHelpers";

vi.mock("../src/ui/download", () => ({ downloadText: () => {} }));
vi.mock("../src/charts/line", () => ({ lineChart: () => ({ setData() {}, setLog() {}, setMarkers() {}, destroy() {} }) }));
vi.mock("../src/charts/bars", () => ({ barChart: () => ({ redraw() {}, destroy() {} }) }));
vi.mock("../src/charts/histogram", () => ({ histogramChart: () => ({ redraw() {}, destroy() {} }) }));

const g7 = golden7 as unknown as StatsResponse;
const gAll = goldenAll as unknown as StatsResponse;
const gNo = goldenUnavailable as unknown as StatsResponse;
const flush = async () => { for (let i = 0; i < 8; i++) await vi.advanceTimersByTimeAsync(0); };
const q = (el: HTMLElement, s: string) => el.querySelector(s) as HTMLElement;
const qa = (el: HTMLElement, s: string) => [...el.querySelectorAll(s)] as HTMLElement[];

const mounted: { hide(): void }[] = [];
async function mountEn(body: StatsResponse) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify({ ...body, range: new URL(url, "http://x").searchParams.get("range") ?? "" }), { status: 200, headers: { "Content-Type": "application/json" } })));
  const { createStats } = await importEn(() => import("../src/tabs/stats"));
  const tab = createStats({} as Meta);
  mounted.push(tab);
  document.body.append(tab.el);
  tab.show();
  await flush();
  return tab;
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  document.body.replaceChildren();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(async () => {
  mounted.splice(0).forEach((t) => t.hide());
  vi.useRealTimers();
  vi.unstubAllGlobals();
  (await import("../src/i18n")).__setLang("ru");
});

describe("вкладка «Статистика» на английском", () => {
  it("насыщенный ответ (90 дней, реконструкция, расхождение позиции, раскрытые fills): кириллицы нет", async () => {
    const res = richStats({ position_mismatch: { replayed: { side: "long", qty: 0.02 }, actual: null } as never });
    const tab = await mountEn(res);
    qa(tab.el, ".st-trades .row-exp").slice(0, 3).forEach((b) => b.click());
    expect(qa(tab.el, ".st-fills").length).toBeGreaterThan(0);
    expect(cyrillicIn(tab.el)).toEqual([]);
    expect(q(tab.el, ".page-title").textContent).toBe("Statistics");
    expect(q(tab.el, ".st-mismatch").textContent).toContain("doesn't match the position on the exchange");
  });

  it("золотой ответ 7d: заголовок, диапазоны, плитки, множественное число", async () => {
    const tab = await mountEn(g7);
    expect(qa(tab.el, ".st-ranges button").map((b) => b.textContent)).toEqual(["1h", "1d", "3d", "1w", "1M", "3M", "6M", "1Y", "2Y", "3Y", "All"]);
    expect(q(tab.el, ".st-tile.kpi-roi").textContent).toContain("Account ROI");
    expect(q(tab.el, ".st-few").textContent).toContain("Few trades (2 of 10 needed)");
    expect(q(tab.el, ".st-trades .card-head").textContent).toContain("2 of 2");
    expect(q(tab.el, ".st-updated").textContent).toMatch(/^updated .* · auto every 60 s$/);
    expect(q(tab.el, ".st-window").textContent).toContain("2026-05-25 – 2026-06-01");
    // число сделок в карточке «Long vs short»: one / other
    const counts = qa(tab.el, ".st-side-top .muted").map((x) => x.textContent);
    expect(counts.every((c) => /^\d+ trades?$/.test(c ?? ""))).toBe(true);
    expect(counts).toContain("1 trade");
    expect(cyrillicIn(tab.el)).toEqual([]);
  });

  it("определения метрик — английские из словаря, ставка комиссии подставлена", async () => {
    const tab = await mountEn(g7);
    expect(q(tab.el, ".st-tile.kpi-roi .st-info").getAttribute("title")).toMatch(/^Account ROI, % = wallet Δ over the window/);
    const costs = q(tab.el, ".st-tile.kpi-costs .st-info").getAttribute("title") ?? "";
    expect(costs).toContain("fill volume in USDT × 0.100%");
    expect(costs).not.toContain("{fee}");
    for (const i of qa(tab.el, ".st-info")) expect(i.getAttribute("title") ?? "").not.toMatch(/[А-Яа-яЁё]/);
  });

  it("метрики, которых нет в definitions сервера, описываются локальными английскими текстами", async () => {
    const tab = await mountEn({ ...g7, definitions: {} } as StatsResponse);
    const best = q(tab.el, ".st-minis .mini[data-kpi=\"best\"] .st-info");
    expect(best.getAttribute("title")).toBe("The trade with the highest net in the window.");
    expect(cyrillicIn(q(tab.el, ".st-minis"))).toEqual([]);
  });

  it("предупреждения собираются из кода и параметров, а не из серверного текста", async () => {
    const tab = await mountEn(gAll);
    const text = q(tab.el, ".st-quality").textContent ?? "";
    expect(text).toContain("Reconstructed: 1");
    expect(text).toContain("1 trade was reconstructed from the “Position disappeared” event");
    expect(text).toContain("The window has equity gaps > 15 min (the bot was not running): 1 gaps, 20.3 h in total.");
    expect(text).toContain("Equity gaps: 1");
    expect(cyrillicIn(q(tab.el, ".st-quality"))).toEqual([]);
  });

  it("причины сделок переводятся при показе, исходная строка остаётся для логики (реконструкция -> бейдж)", async () => {
    const tab = await mountEn(richStats());
    const reasons = qa(tab.el, ".st-rt .st-reason-k").map((x) => x.textContent ?? "");
    expect(reasons.some((r) => r.startsWith("bar"))).toBe(true);
    const recon = qa(tab.el, ".st-rt .st-reason-k").find((x) => x.querySelector(".badge"));
    expect(recon?.textContent).toContain("stop (reconstructed)");
    expect(recon?.querySelector(".badge")?.textContent).toBe("est.");
  });

  it("недоступная статистика: заголовок и пояснение английские, русское серверное сообщение для известной причины не показывается", async () => {
    const tab = await mountEn({ ...gNo, reason: "no_db", message: "База бота не найдена: бот ещё не запускался." } as StatsResponse);
    const card = q(tab.el, ".st-unavail");
    expect(card.querySelector("h3")!.textContent).toBe("Bot database not found");
    expect(card.querySelector(".strong")).toBeNull();
    expect(card.textContent).toContain("Reason code: no_db");
    expect(cyrillicIn(card)).toEqual([]);
  });

  it("неизвестная причина: серверное сообщение показывается как есть (разрешённая кириллица — только оно)", async () => {
    const tab = await mountEn({ ...gNo, reason: "weird", message: "Что-то странное." } as StatsResponse);
    const card = q(tab.el, ".st-unavail");
    expect(card.querySelector("h3")!.textContent).toBe("Couldn't calculate the statistics");
    expect(cyrillicIn(card, [/^Что-то странное\.$/])).toEqual([]);
    expect(card.querySelector(".strong")!.textContent).toBe("Что-то странное.");
  });
});
