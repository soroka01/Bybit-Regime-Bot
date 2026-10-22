// @vitest-environment jsdom
// Вкладка «Бот (live)» и плашка статуса бота в английском интерфейсе: в разметке нет кириллицы, кроме текстов, которые приходят с сервера/из БД и показываются как есть:
//  - events[].text — сохранённый текст уведомлений Telegram (останется русским, пока Telegram не локализован);
//  - причина закрытой сделки и подпись «% счёта» в таблице «Закрытые сделки» рисуют общие reasonCell/resultText вкладки «Статистика» (их перевод — на стороне той вкладки).
// Русский вывод этих же экранов закреплён существующими тестами (liveTab, liveClosed, side, botStatus…).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveSnapshot, Meta, StatsResponse, StatsTradeRow } from "../src/types";
import { cyrillicIn, importEn } from "./enHelpers";
import { richStats } from "./fixtures/statsRich";

vi.mock("../src/charts/line", () => ({
  lineChart: () => ({ setData() {}, setLog() {}, setMarkers() {}, destroy() {} }),
}));

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(0); };
const q = (el: HTMLElement, s: string) => el.querySelector(s) as HTMLElement | null;
const qa = (el: HTMLElement, s: string) => [...el.querySelectorAll(s)] as HTMLElement[];
const clean = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();

const NOW = Date.parse("2026-10-08T12:00:00Z");
const NOW_S = NOW / 1000;
const H = 3_600_000;
const iso = (ms: number) => new Date(ms).toISOString();

/** Сохранённые по-русски данные бота, показываемые как есть. */
const ALLOW = [/^Действую с опозданием/, /^Бар закрыт/, /^бар$/, /^[−+]?\d+\.\d+% счёта$/];

const leg = (key: string, weight: number, enabled: boolean, signal: number | null, updated: string | null = null) =>
  ({ key, strategy: key, tf: "1h", params: { fast: 9 }, long_only: false, weight, enabled, signal, updated });

const snap = (over: Partial<LiveSnapshot> = {}): LiveSnapshot => ({
  available: true, enabled: true, killed: false, latch: false, equity: 1000, peak_equity: 1100,
  position: { side: "Buy", qty: 0.003, entry: 100000, upnl: -4.5, mark: 98500 }, target_exposure: 0.5, last_bar: iso(NOW - H), heartbeat: iso(NOW - 30_000),
  settings: { stop_pct: 0.05, leverage: 2, max_leverage: 10, kill_dd: 0.25, kill_daily: 0.15 },
  legs: [leg("EMA cross", 0.5, true, 1, iso(NOW - 120_000)), leg("SMA cross", 0.5, true, -1), leg("Donchian", 0.5, true, 0), leg("RSI", 0, true, 1), leg("MACD", 0.3, false, null)],
  trades: [{ ts: iso(NOW - 2 * H), side: "Buy", qty: 0.003, price: 100000, reason: "бар, EMA cross · 4h: вне рынка → лонг", order_id: "1" },
    { ts: iso(NOW - 5 * H), side: "Sell", qty: 0.002, price: 99000, reason: "закрытие вручную", order_id: "2" }],
  equity_history: [[iso(NOW - 26 * H), 990], [iso(NOW - 2 * H), 1050], [iso(NOW), 1000]],
  events: [
    { ts: iso(NOW - 3 * H), level: "info", text: "Бар закрыт, сигналы пересчитаны" },
    { ts: iso(NOW - 2 * H), level: "warning", text: "Действую с опозданием 12 мин" },
    { ts: iso(NOW - H), level: "trade", text: "Бар закрыт: покупка" },
  ],
  ...over,
});

const tr = (n: number, side: "long" | "short", net: number, agoH: number, over: Partial<StatsTradeRow> = {}): StatsTradeRow => ({
  n, entry_ts: NOW_S - (agoH + 3) * 3600, exit_ts: NOW_S - agoH * 3600, side, qty: 0.01, entry_price: 100000 + n, exit_price: 100500 + n,
  entry_notional: 1000, gross: net + 0.4, fees: 0.4, funding: 0, funding_complete: true, exact: true, net, ret_pct: net / 10, ret_margin_pct: net / 5,
  equity_at_entry: 1000, ret_account_pct: net / 10, hold_s: 3 * 3600, reason: "бар", reason_group: "бар", reconstructed: false, fills: [], ...over,
});
function stats(rows: StatsTradeRow[]): StatsResponse {
  const base = richStats();
  const profit = rows.filter((r) => (r.net ?? 0) > 0).reduce((x, r) => x + (r.net ?? 0), 0);
  const loss = rows.filter((r) => (r.net ?? 0) < 0).reduce((x, r) => x + (r.net ?? 0), 0);
  return {
    ...base, range: "30d", computed_at: NOW_S, trades: { total: rows.length, truncated: false, rows }, open_position: null,
    kpis: { ...base.kpis!, trades: rows.length, wins: rows.filter((r) => (r.net ?? 0) > 0).length, losses: rows.filter((r) => (r.net ?? 0) < 0).length, profit_sum_usdt: profit, loss_sum_usdt: loss, net_pnl: profit + loss },
  };
}

type Mounted = { el: HTMLElement; hide(): void };
const mounted: Mounted[] = [];
let live: LiveSnapshot;
let statsReply: () => Response;

async function mount(s: LiveSnapshot, st: () => Response): Promise<Mounted> {
  live = s;
  statsReply = st;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => (String(url).startsWith("/api/stats") ? statsReply() : json(200, live))));
  const { createLive } = await importEn(() => import("../src/tabs/live"));
  const { resetStatsFeed } = await import("../src/tabs/statsFeed");
  resetStatsFeed();
  const tab = createLive({} as Meta);
  mounted.push(tab);
  document.body.append(tab.el);
  tab.show();
  await flush();
  return tab;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  localStorage.clear();
  document.body.replaceChildren();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { mounted.splice(0).forEach((m) => m.hide()); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("вкладка «Бот (live)» по-английски", () => {
  it("рабочий снимок с позицией, ногами, закрытыми сделками и событиями: без кириллицы, точные подписи", async () => {
    const tab = await mount(snap(), () => json(200, stats([tr(1, "long", 12.5, 30), tr(2, "short", -4.25, 20), tr(3, "short", 6, 10), tr(4, "long", -1.5, 2)])));
    expect(cyrillicIn(tab.el, ALLOW)).toEqual([]);
    expect(q(tab.el, ".page-head h1, h1, h2")?.textContent).toBe("Bot (live)");
    expect(tab.el.textContent).toContain("View only: the bot is controlled through Telegram (/start).");
    expect(qa(tab.el, ".lv-range button").map((b) => b.textContent)).toEqual(["24 h", "7 d", "All", "All", "Warnings and errors", "Trades"]);
    // герой и позиция
    expect(clean(q(tab.el, ".lv-hero .lv-equity")!.textContent)).toBe("$1,000.00");
    expect(clean(q(tab.el, ".lv-pos .lv-side")!.textContent)).toBe("△LONG");
    expect(qa(tab.el, ".lv-pos .lv-fact-l").map((e) => e.textContent)).toEqual(["Entry", "Mark", "Stop (calculated)", "Liquidation (est.)", "Margin (est.)", "Notional / account"]);
    expect(clean(q(tab.el, ".lv-pos-pct")!.textContent)).toBe("−0.45% of account");
    expect(clean(q(tab.el, ".lv-nextbar")!.textContent)).toBe("Next 1h candle in1:00:00at 13:00 UTC");
    // ноги
    expect(clean(q(tab.el, ".lv-legs .lv-leg.head")!.textContent)).toBe("LegWeightSignalContributionClose");
    expect(qa(tab.el, ".lv-leg-sig").map((e) => e.textContent)).toEqual(["△ long", "▽ short", "■ flat"]);
    expect(clean(q(tab.el, ".lv-idle summary")!.textContent)).toBe("Inactive legs (2)");
    expect(qa(tab.el, ".lv-idle-leg .badge").map((e) => e.textContent)).toEqual(["not traded", "disabled"]);
    expect(q(tab.el, ".lv-gauge .lg-word")!.textContent).toBe(" long");
    expect(q(tab.el, ".lv-signals .lv-hint")!.textContent).toContain("To add or remove a strategy");
    // лимиты риска
    expect(clean(q(tab.el, ".lv-risks")!.textContent)).toContain("Drawdown from peak");
    expect(clean(q(tab.el, ".lv-risks")!.textContent)).toContain("pp left to the kill-switch");
    expect(qa(tab.el, ".lv-risks .chip").map((c) => c.textContent)).toEqual(["Stop5%", "Leverage2.0× of 10×"]);
    // закрытые сделки
    const strip = clean(q(tab.el, ".lv-closed .lv-strip")!.textContent);
    expect(strip).toContain("Last 30 d:");
    expect(strip).toContain("closed 4");
    expect(strip).toMatch(/profit ▲ \+\$18\.50/);
    expect(strip).toMatch(/loss ▼ −\$5\.75/);
    expect(strip).toMatch(/net ▲ \+\$12\.75/);
    expect(qa(tab.el, ".lv-closed-t thead th").map((e) => e.textContent)).toEqual(["Closed (UTC)", "Side", "Size", "Entry → exit", "Result, $ · % of account", "Held", "Reason"]);
    expect(clean(q(tab.el, ".lv-closed-t .lv-open-row")!.textContent)).toContain("OPEN");
    expect(clean(q(tab.el, ".lv-closed-t tbody tr:nth-child(2) td")!.textContent)).toMatch(/^2 h ago · 10-08 10:00/);
    // события: подписи английские, сохранённый текст — как есть
    expect(q(tab.el, ".lv-events .card-head h3")!.textContent).toBe("Events");
    expect(qa(tab.el, ".lv-ev-kind").map((e) => e.textContent)).toEqual(["Trade", "Warning", "Info"]);
    expect(qa(tab.el, ".lv-ev-text").map((e) => e.textContent)).toContain("Действую с опозданием 12 мин");
    expect(q(tab.el, ".lv-ev.noise")).not.toBeNull();                                // шум распознаётся по сохранённому русскому тексту
  });

  it("откат к списку ордеров: причины сделок из БД переводятся, подсказка английская", async () => {
    const tab = await mount(snap({ position: null }), () => json(500, { error: "boom" }));
    expect(cyrillicIn(tab.el, ALLOW)).toEqual([]);
    expect(q(tab.el, ".lv-trades-card h3")!.textContent).toBe("Latest trades");
    expect(q(tab.el, ".lv-trades-hint")!.textContent).toMatch(/^Could not load closed trades.*Individual orders are shown for now; retry in a minute\.$/);
    expect(qa(tab.el, ".lv-trades .lv-reason").map((e) => e.textContent)).toEqual(["bar, EMA cross · 4h: flat → long", "manual close"]);
    expect(qa(tab.el, ".lv-trades tbody .side-badge.order").map((e) => e.textContent)).toEqual(["△Buy", "▽Sell"]);
    expect(clean(q(tab.el, ".lv-pos")!.textContent)).toContain("No position — the bot is flat");
    expect(clean(q(tab.el, ".lv-pos")!.textContent)).toContain("Target — long 0.50×: the bot will open a position at the next check.");
  });

  it("аварийная остановка, нет сигнала жизни, latch: баннеры и причина kill-switch переведены", async () => {
    const tab = await mount(snap({ killed: true, kill_reason: "просадка от пика 12.3% >= 25%", latch: true, heartbeat: iso(NOW - 20 * 60_000), position: null }), () => json(500, {}));
    expect(cyrillicIn(tab.el, ALLOW)).toEqual([]);
    const banners = qa(tab.el, ".banner").map((b) => clean(b.textContent));
    expect(banners[0]).toMatch(/^The bot is not responding\. Last heartbeat: 20 min ago \(/);
    expect(banners[1]).toBe("Emergency stop. Reason: drawdown from peak 12.3% >= 25%. No new orders are placed; trading can be resumed only manually via Telegram.");
    expect(qa(tab.el, ".lv-pills .badge").map((b) => clean(b.textContent))).toContain("✕Emergency stop");
    expect(q(tab.el, ".lv-pill-note")!.textContent).toContain("Latch: after a stop-loss");
  });

  it("бот ни разу не запускался: заглушка и код причины", async () => {
    const tab = await mount({ available: false, reason: "no_db" }, () => json(500, {}));
    expect(cyrillicIn(tab.el, ALLOW)).toEqual([]);
    expect(q(tab.el, ".lv-empty")!.textContent).toContain("The bot has not been started yet");
    expect(q(tab.el, ".lv-reason-line")!.textContent).toBe("Reason code: no_db");
  });
});

describe("плашка статуса бота и чистая логика по-английски", () => {
  const pill = async (s: LiveSnapshot | null) => {
    vi.stubGlobal("fetch", vi.fn(async () => (s ? json(200, s) : json(500, { error: "x" }))));
    const { botStatusPill } = await importEn(() => import("../src/ui/botStatus"));
    const el = botStatusPill();
    document.body.append(el);
    for (let i = 0; i < 6; i++) await vi.advanceTimersByTimeAsync(0);
    return el;
  };

  it("позиция, остановка, нет связи", async () => {
    let p = await pill(snap());
    expect(cyrillicIn(p)).toEqual([]);
    expect(clean(p.textContent)).toBe("Trading on△ LONG 0.0030▼ −$4.50−0.45%");
    expect(p.getAttribute("aria-label")).toBe("Bot, Trading on, long position 0.0030, unrealized result −$4.50 (−0.45% of account). Open the Bot (live) tab");
    document.body.replaceChildren();
    p = await pill(snap({ killed: true, position: null }));
    expect(clean(p.textContent)).toContain("Emergency stop");
    expect(p.getAttribute("aria-label")).toContain("no position");
    document.body.replaceChildren();
    p = await pill({ available: false });
    expect(clean(p.textContent)).toBe("Bot is not running");
  });

  it("обратный отсчёт, окно результата, предупреждение о стопе", async () => {
    const calc = await importEn(() => import("../src/tabs/liveCalc"));
    const win = await import("../src/tabs/liveWindow");
    expect(calc.fmtCountdown(26 * 3600_000 + 130_000)).toBe("1 d 02:02:10");
    expect(calc.RANGES.map((r) => r.label)).toEqual(["24 h", "7 d", "All"]);
    expect(win.fmtSpan(30 * 60_000)).toBe("30 min");
    expect(win.fmtSpan(5 * 3600_000)).toBe("5 h");
    expect(win.fmtSpan(6.9 * 86_400_000)).toBe("6.9 d");
    expect(win.todayLabel({ since: Date.parse("2026-10-08T00:00:00Z") }, NOW)).toBe("today (since 00:00 UTC)");
    expect(win.todayLabel({ since: Date.parse("2026-10-08T07:30:00Z") }, NOW)).toBe("today (since 07:30 UTC)");
    const w = calc.stopRiskWarning({ stop_pct: 0.2, leverage: 2, kill_dd: 0.25, kill_daily: 0.4 }, [leg("a", 1, true, 1)]);
    expect(w?.limitLabel).toBe("drawdown");
    const r = win.resolveWindow("24h", [{ t: NOW - 26 * H, v: 100 }, { t: NOW, v: 110 }], 110, NOW);
    expect(r.label).toBe("over the last 24 h");
    expect(r.title).toMatch(/^Wallet change over the last 24 h: from /);
  });
});
