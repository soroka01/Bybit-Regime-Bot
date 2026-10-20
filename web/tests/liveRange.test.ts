// @vitest-environment jsdom
// Вкладка «Бот (live)»: окно «Всё» / «7 д» из /api/stats, откат на снимок при 404 / ошибке, подписи чипов (jsdom, график подменён).
// Реальные числа владельца: /api/live отдаёт последние 2000 точек (162,60 $ ... 166,05 $), вся история — 3220 точек с 160,63 $ -> +5,42 $ / +3,37 %.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveSnapshot, Meta, StatsResponse } from "../src/types";
import { richStats } from "./fixtures/statsRich";

const chartCalls = vi.hoisted(() => ({ data: [] as { x: number[]; y: (number | null)[] }[], destroyed: 0 }));
vi.mock("../src/charts/line", () => ({
  lineChart: () => ({
    setData(x: number[], ys: (number | null)[][]) { chartCalls.data.push({ x, y: ys[0]! }); },
    setLog() {}, setMarkers() {}, destroy() { chartCalls.destroyed++; },
  }),
}));

const { createLive } = await import("../src/tabs/live");
const { resetStatsFeed } = await import("../src/tabs/statsFeed");

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 12; i++) await vi.advanceTimersByTimeAsync(0); };
const q = (el: HTMLElement, s: string) => el.querySelector(s) as HTMLElement | null;
const qa = (el: HTMLElement, s: string) => [...el.querySelectorAll(s)] as HTMLElement[];
const clean = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();

const MIN = 60_000, DAY = 86_400_000;
const NOW = Date.parse("2026-10-17T12:00:00Z");
const START_ALL = Date.parse("2026-10-06T00:12:00Z");
const iso = (ms: number) => new Date(ms).toISOString();

/** Снимок: 2000 точек с шагом 5 мин (≈ 6,9 суток), от 162,60 до 166,05. */
const N = 2000;
const history = (n = N): [string, number][] => Array.from({ length: n }, (_, i) => [iso(NOW - (n - 1 - i) * 5 * MIN), 162.6 + (3.45 * i) / (n - 1)]);
const snap = (over: Partial<LiveSnapshot> = {}): LiveSnapshot => ({
  available: true, enabled: true, killed: false, latch: false, equity: 166.05, peak_equity: 167,
  position: null, target_exposure: 0, last_bar: iso(NOW - 3_600_000), heartbeat: iso(NOW - 30_000),
  settings: { stop_pct: 0.05, leverage: 2, max_leverage: 10, kill_dd: 0.25, kill_daily: 0.15 }, legs: [], trades: [], equity_history: history(), events: [], ...over,
});

function statsAll(): StatsResponse {
  const n = 600, base = richStats();
  const t = Array.from({ length: n }, (_, i) => Math.round((START_ALL + ((NOW - START_ALL) * i) / (n - 1)) / 1000));
  const v = t.map((_, i) => 160.63 + (5.42 * i) / (n - 1));
  return {
    ...base, range: "all", computed_at: NOW / 1000,
    equity: { ...base.equity!, eq_start: 160.63, eq_end: 166.05, eq_start_ts: Math.round(START_ALL / 1000), eq_end_ts: NOW / 1000, starts_before_data: false, delta: 5.42, wallet_return_pct: 3.3743 },
    series: { ...base.series!, equity: { t, v, lo: v, hi: v } },
  };
}
function stats7(): StatsResponse {
  const s = statsAll(), start = NOW - 7 * DAY, n = 600;
  const t = Array.from({ length: n }, (_, i) => Math.round((start + ((NOW - start) * i) / (n - 1)) / 1000));
  const v = t.map((_, i) => 161.9 + (4.15 * i) / (n - 1));
  return { ...s, range: "7d", equity: { ...s.equity!, eq_start: 161.9, eq_start_ts: start / 1000, delta: 4.15, wallet_return_pct: 2.5633 }, series: { ...s.series!, equity: { t, v, lo: v, hi: v } } };
}

let calls: string[] = [];
let live: LiveSnapshot;
let statsHandler: (range: string) => Response | Promise<Response>;
const mounted: { hide(): void }[] = [];
function route() {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(String(url));
    if (String(url).startsWith("/api/stats")) return statsHandler(new URL(String(url), "http://x").searchParams.get("range") ?? "");
    return json(200, live);
  }));
}
async function mount(s = snap()) {
  live = s;
  route();
  const tab = createLive({} as Meta);
  mounted.push(tab);
  document.body.append(tab.el);
  tab.show();
  await flush();
  return tab;
}
const fullCalls = () => calls.filter((c) => /range=(all|7d)/.test(c));
const totalChip = (el: HTMLElement) => qa(el, ".lv-hero .lv-pl").find((c) => !(c.textContent ?? "").includes("сегодня")) ?? null;
const todayChip = (el: HTMLElement) => qa(el, ".lv-hero .lv-pl").find((c) => (c.textContent ?? "").includes("сегодня")) ?? null;
const rangeBtn = (el: HTMLElement, label: string) => qa(el, ".lv-range button").find((b) => b.textContent === label) as HTMLButtonElement;
const noteText = (el: HTMLElement) => { const n = q(el, ".lv-range-note"); return n && !n.hidden ? clean(n.textContent) : ""; };

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  localStorage.clear();
  document.body.replaceChildren();
  resetStatsFeed();
  calls = [];
  chartCalls.data = []; chartCalls.destroyed = 0;
  statsHandler = (r) => json(200, r === "7d" ? stats7() : r === "all" ? statsAll() : { ...richStats(), range: r });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { mounted.splice(0).forEach((t) => t.hide()); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("live: «Всё» — вся история из /api/stats", () => {
  it("по умолчанию (24 ч) полная история не запрашивается; 24 ч — «за 24 ч»", async () => {
    const tab = await mount();
    expect(fullCalls()).toHaveLength(0);
    expect(clean(totalChip(tab.el)!.textContent)).toContain("за 24 ч");
    expect(clean(todayChip(tab.el)!.textContent)).toContain("сегодня (с 00:00 UTC)");
    expect(noteText(tab.el)).toBe("");
  });

  it("клик по «Всё» запрашивает /api/stats?range=all; чип = +5,42 $ · +3,37 % «за всё время · с 06.10.26», график начинается 06.10", async () => {
    const tab = await mount();
    rangeBtn(tab.el, "Всё").click();
    await flush();
    expect(fullCalls().filter((c) => c.includes("range=all"))).toHaveLength(1);
    const chip = totalChip(tab.el)!;
    const t = clean(chip.textContent);
    expect(t).toMatch(/\+5,42 \$/);
    expect(t).toContain("+3,37%");
    expect(t).toContain("за всё время · с 06.10.26");
    expect(t).not.toContain("доступн");
    expect(chip.getAttribute("title")).toContain("160,63 $");
    expect(chip.getAttribute("title")).toContain("166,05 $");
    expect(noteText(tab.el)).toBe("");
    const last = chartCalls.data[chartCalls.data.length - 1]!;
    expect(last.x[0]).toBe(Math.round(START_ALL / 1000));                      // график начинается 06.10 00:12, а не 10.10
    expect(new Date(last.x[0]! * 1000).toISOString().slice(0, 10)).toBe("2026-10-06");
    expect(last.y[0]).toBeCloseTo(160.63, 2);
    expect(last.x.length).toBe(600);
    expect(todayChip(tab.el)).not.toBeNull();                                   // «сегодня» продолжает считаться по снимку
  });

  it("сохранённый диапазон «Всё»: запрос уходит сразу при открытии вкладки, пока грузится — «загрузка…» без числа; повтор раз в минуту", async () => {
    localStorage.setItem("tb.live", JSON.stringify({ range: "all", ev: "all" }));
    let release!: (r: Response) => void;
    statsHandler = () => new Promise<Response>((res) => { release = res; });
    const tab = await mount();
    expect(fullCalls()).toHaveLength(1);
    let t = clean(totalChip(tab.el)!.textContent);
    expect(t).toContain("загрузка");
    expect(t).not.toMatch(/\d\s?\$/);
    expect(q(tab.el, ".lv-chart .skeleton")).not.toBeNull();
    release(json(200, statsAll()));
    await flush();
    t = clean(totalChip(tab.el)!.textContent);
    expect(t).toContain("за всё время");
    expect(q(tab.el, ".lv-chart .skeleton")).toBeNull();
    await vi.advanceTimersByTimeAsync(61_000);
    await flush();
    expect(fullCalls().length).toBeGreaterThanOrEqual(2);                       // обновление примерно раз в минуту
  });

  it("запрос идёт один, даже когда снимок и клики перерисовывают страницу", async () => {
    const tab = await mount();
    rangeBtn(tab.el, "Всё").click();
    rangeBtn(tab.el, "Всё").click();
    await flush();
    await vi.advanceTimersByTimeAsync(10_500);                                  // новый опрос /api/live
    await flush();
    expect(fullCalls().filter((c) => c.includes("range=all"))).toHaveLength(1);
  });
});

describe("live: откат на снимок", () => {
  it("404 (старый бэкенд): «за доступную историю (≈ 6,9 дн.)», строка-подсказка, число — по снимку, не выдаётся за всё время", async () => {
    statsHandler = () => json(404, { detail: "Not Found" });
    const tab = await mount();
    rangeBtn(tab.el, "Всё").click();
    await flush();
    const t = clean(totalChip(tab.el)!.textContent);
    expect(t).toContain("за доступную историю (≈ 6,9 дн.)");
    expect(t).not.toContain("за всё время");
    expect(t).toMatch(/\+3,45 \$/);                                            // 166,05 − 162,60 по снимку
    expect(noteText(tab.el)).toBe("Полная история доступна после обновления бота");
    const last = chartCalls.data[chartCalls.data.length - 1]!;
    expect(new Date(last.x[0]! * 1000).toISOString().slice(0, 10)).toBe("2026-10-10");
    // подсказка пропадает при возврате на 24 ч
    rangeBtn(tab.el, "24 ч").click();
    await flush();
    expect(noteText(tab.el)).toBe("");
  });

  it("ошибка сервера (500): честная подпись, но без подсказки про обновление", async () => {
    statsHandler = () => json(500, { detail: "boom" });
    const tab = await mount();
    rangeBtn(tab.el, "Всё").click();
    await flush();
    expect(clean(totalChip(tab.el)!.textContent)).toContain("за доступную историю");
    expect(noteText(tab.el)).toBe("");
  });

  it("статистика недоступна (available:false, нет БД): тот же откат без подсказки", async () => {
    statsHandler = () => json(200, { available: false, reason: "no_db", message: "нет базы", range: "all", computed_at: 1, warnings: [], definitions: {} });
    const tab = await mount();
    rangeBtn(tab.el, "Всё").click();
    await flush();
    expect(clean(totalChip(tab.el)!.textContent)).toContain("за доступную историю (≈ 6,9 дн.)");
    expect(noteText(tab.el)).toBe("");
  });

  it("старая форма снимка (нет peak_equity, settings, legs) + 404: вкладка рисуется, подпись честная", async () => {
    statsHandler = () => json(404, {});
    const old = { available: true, equity: 166.05, equity_history: history(300), heartbeat: iso(NOW - 10_000) } as LiveSnapshot;
    const tab = await mount(old);
    rangeBtn(tab.el, "Всё").click();
    await flush();
    const t = clean(totalChip(tab.el)!.textContent);
    expect(t).toContain("за доступную историю (≈ 1,0 дн.)");
    expect(noteText(tab.el)).toContain("Полная история");
    expect(q(tab.el, ".lv-hero")).not.toBeNull();
  });

  it("разовый сбой обновления не затирает уже полученную историю", async () => {
    const tab = await mount();
    rangeBtn(tab.el, "Всё").click();
    await flush();
    statsHandler = () => json(500, {});
    await vi.advanceTimersByTimeAsync(61_000);
    await flush();
    expect(clean(totalChip(tab.el)!.textContent)).toContain("за всё время · с 06.10.26");
  });
});

describe("live: «7 д»", () => {
  it("снимок (6,9 дн.) неполон -> запрос range=7d и честные 7 суток из статистики", async () => {
    const tab = await mount();
    rangeBtn(tab.el, "7 д").click();
    await flush();
    expect(fullCalls().some((c) => c.includes("range=7d"))).toBe(true);
    const t = clean(totalChip(tab.el)!.textContent);
    expect(t).toContain("за 7 дн.");
    expect(t).toMatch(/\+4,15 \$/);
    expect(t).toContain("+2,56%");
  });

  it("404: «за доступные 6,9 дн.» вместо «за 7 дн.» + подсказка", async () => {
    statsHandler = () => json(404, {});
    const tab = await mount();
    rangeBtn(tab.el, "7 д").click();
    await flush();
    const t = clean(totalChip(tab.el)!.textContent);
    expect(t).toContain("за доступные 6,9 дн.");
    expect(t).not.toContain("за 7 дн.");
    expect(noteText(tab.el)).toContain("Полная история доступна после обновления бота");
  });

  it("снимок покрывает неделю (2100 точек): без запросов, «за 7 дн.»", async () => {
    const tab = await mount(snap({ equity_history: history(2100) }));
    rangeBtn(tab.el, "7 д").click();
    await flush();
    expect(fullCalls()).toHaveLength(0);
    expect(clean(totalChip(tab.el)!.textContent)).toContain("за 7 дн.");
  });
});

describe("live: подписи", () => {
  it("полоса «Закрытые сделки»: ROI счёта явно за 30 дн., а не за всё время", async () => {
    const res = { ...richStats(), range: "30d" as const, computed_at: NOW / 1000 };
    statsHandler = (r) => json(200, r === "30d" ? res : r === "7d" ? stats7() : statsAll());
    const tab = await mount();
    const roi = q(tab.el, ".lv-strip-roi");
    expect(roi).not.toBeNull();
    expect(clean(roi!.textContent)).toMatch(/^ROI счёта за 30 дн\. /);
    expect(roi!.getAttribute("title")).toContain("Только за 30 дней");
    expect(tab.el.textContent).not.toContain("всего за период");
  });
});
