// @vitest-environment jsdom
// Вкладка «Бот (live)»: блок «Закрытые сделки» (round trips из /api/stats?range=30d) — деньги в долларах и % счёта, открытая позиция сверху,
// шапка «За 30 дн.: закрыто N · плюс · минус · итого», откат к списку ордеров при 404/ошибке, подсветка новой сделки, нейтральные бейджи сторон.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveSnapshot, Meta, StatsResponse, StatsTradeRow } from "../src/types";
import { richStats } from "./fixtures/statsRich";

vi.mock("../src/charts/line", () => ({
  lineChart: () => ({ setData() {}, setLog() {}, setMarkers() {}, destroy() {} }),
}));

const { createLive } = await import("../src/tabs/live");
const { resetStatsFeed, rememberStats } = await import("../src/tabs/statsFeed");

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(0); };
const q = (el: HTMLElement, s: string) => el.querySelector(s) as HTMLElement | null;
const qa = (el: HTMLElement, s: string) => [...el.querySelectorAll(s)] as HTMLElement[];

const NOW = Date.parse("2026-10-08T12:00:00Z");
const NOW_S = NOW / 1000;
const iso = (ms: number) => new Date(ms).toISOString();
const H = 3_600_000;
const clean = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();

const snap = (over: Partial<LiveSnapshot> = {}): LiveSnapshot => ({
  available: true, env: "mainnet", enabled: true, killed: false, latch: false, equity: 1000, peak_equity: 1010,
  position: null, target_exposure: 0, last_bar: iso(NOW - H), heartbeat: iso(NOW - 30_000),
  settings: { stop_pct: 0.05, leverage: 2, max_leverage: 10, kill_dd: 0.25, kill_daily: 0.15 }, legs: [], trades: [],
  equity_history: [[iso(NOW - 2 * H), 990], [iso(NOW), 1000]], events: [], ...over,
});

/** Закрытая сделка: выход `agoH` часов назад, итог net $ и % счёта. */
const tr = (n: number, side: "long" | "short", net: number, agoH: number, over: Partial<StatsTradeRow> = {}): StatsTradeRow => ({
  n, entry_ts: NOW_S - (agoH + 3) * 3600, exit_ts: NOW_S - agoH * 3600, side, qty: 0.01, entry_price: 100000 + n, exit_price: 100500 + n,
  entry_notional: 1000, gross: net + 0.4, fees: 0.4, funding: 0, funding_complete: true, exact: true, net, ret_pct: net / 10, ret_margin_pct: net / 5,
  equity_at_entry: 1000, ret_account_pct: net / 10, hold_s: 3 * 3600, reason: "бар", reason_group: "бар", reconstructed: false, fills: [], ...over,
});

function stats(rows: StatsTradeRow[], over: Partial<StatsResponse> = {}): StatsResponse {
  const base = richStats();
  const profit = rows.filter((r) => (r.net ?? 0) > 0).reduce((a, r) => a + (r.net ?? 0), 0);
  const loss = rows.filter((r) => (r.net ?? 0) < 0).reduce((a, r) => a + (r.net ?? 0), 0);
  return {
    ...base, range: "30d", computed_at: NOW_S,
    kpis: { ...base.kpis!, trades: rows.length, wins: rows.filter((r) => (r.net ?? 0) > 0).length, losses: rows.filter((r) => (r.net ?? 0) < 0).length, profit_sum_usdt: profit, loss_sum_usdt: loss, net_pnl: profit + loss },
    trades: { total: rows.length, truncated: false, rows },
    open_position: null,
    ...over,
  };
}

let calls: string[] = [];
let statsHandler: () => Response | Promise<Response>;
let live: LiveSnapshot;
function route() {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(String(url));
    if (String(url).startsWith("/api/stats")) return statsHandler();
    return json(200, live);
  }));
}
const mounted: { hide(): void }[] = [];
async function mount(s: LiveSnapshot, st: () => Response | Promise<Response>) {
  live = s;
  statsHandler = st;
  route();
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
  resetStatsFeed();
  calls = [];
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { mounted.splice(0).forEach((t) => t.hide()); vi.useRealTimers(); vi.unstubAllGlobals(); });

const ROWS = [tr(1, "long", 12.5, 30), tr(2, "short", -4.25, 20), tr(3, "short", 6, 10), tr(4, "long", -1.5, 2)];

describe("«Закрытые сделки»: деньги и проценты", () => {
  it("шапка: за 30 дн. закрыто N, плюс, минус, итого — в долларах со знаками", async () => {
    const tab = await mount(snap(), () => json(200, stats(ROWS)));
    const strip = q(tab.el, ".lv-closed .lv-strip")!;
    const t = clean(strip.textContent);
    expect(t).toContain("За 30 дн.:");
    expect(t).toContain("закрыто 4");
    expect(t).toMatch(/плюс ▲ \+18,50 \$/);                    // 12,5 + 6
    expect(t).toMatch(/минус ▼ −5,75 \$/);                      // −4,25 − 1,5
    expect(t).toMatch(/итого ▲ \+12,75 \$/);
    expect(q(strip, ".lv-strip-p .st-m")!.classList.contains("pos")).toBe(true);
    expect(q(strip, ".lv-strip-m .st-m")!.classList.contains("neg")).toBe(true);
    expect(q(strip, ".lv-strip-t .st-m")!.classList.contains("pos")).toBe(true);
    expect(calls.filter((u) => u.startsWith("/api/stats"))).toEqual(["/api/stats?range=30d"]);
  });

  it("убыточное окно: итого красится минусом со знаком −", async () => {
    const tab = await mount(snap(), () => json(200, stats([tr(1, "long", -3, 5), tr(2, "long", 1, 4)])));
    const tot = q(tab.el, ".lv-strip-t .st-m")!;
    expect(tot.classList.contains("neg")).toBe(true);
    expect(clean(tot.textContent)).toBe("▼ −2,00 $");
  });

  it("строки: сторона словами ЛОНГ/ШОРТ, итог $ жирным со знаком и ▲/▼, «% счёта» под ним; новые сверху", async () => {
    const tab = await mount(snap(), () => json(200, stats(ROWS)));
    const rows = qa(tab.el, ".lv-closed-t tbody tr");
    expect(rows).toHaveLength(4);
    expect(rows.map((r) => q(r, ".side-badge")!.textContent)).toEqual(["△ЛОНГ", "▽ШОРТ", "▽ШОРТ", "△ЛОНГ"]);
    // порядок по времени выхода: самая свежая (n=4, лонг, −1,5) первой
    expect(clean(rows[0]!.textContent)).toContain("▼ −1,50 $");
    expect(clean(q(rows[0]!, ".lv-res-pct")!.textContent)).toBe("−0,15% счёта");
    expect(clean(q(rows[2]!, ".lv-res-usd")!.textContent)).toBe("▼ −4,25 $");
    expect(clean(q(rows[3]!, ".lv-res-usd")!.textContent)).toBe("▲ +12,50 $");
    expect(clean(q(rows[3]!, ".lv-res-pct")!.textContent)).toBe("+1,25% счёта");
    expect(q(rows[3]!, ".lv-res-usd")!.parentElement!.classList.contains("pos")).toBe(true);
    expect(clean(q(rows[0]!, ".lv-c-px")!.textContent)).toBe("100 004,00 → 100 504,00");
    expect(clean(q(rows[0]!, ".lv-c-hold")!.textContent)).toBe("3 ч");
    // время: относительное и UTC
    expect(clean(rows[0]!.children[0]!.textContent)).toMatch(/^2 ч назад · 08\.10 10:00$/);
  });

  it("цвет результата зависит ТОЛЬКО от знака P/L, а не от стороны: шорт в плюсе — pos, лонг в минусе — neg; бейдж стороны без pos/neg", async () => {
    const tab = await mount(snap(), () => json(200, stats(ROWS)));
    for (const r of qa(tab.el, ".lv-closed-t tbody tr")) {
      const side = q(r, ".side-badge")!;
      expect(side.className).not.toMatch(/\b(pos|neg)\b/);
      expect(side.querySelector(".pos, .neg")).toBeNull();
      const res = q(r, "td.lv-res")!;
      const net = clean(q(r, ".lv-res-usd")!.textContent);
      expect(res.classList.contains(net.includes("−") ? "neg" : "pos")).toBe(true);
    }
    const short = qa(tab.el, ".lv-closed-t tbody tr").find((r) => q(r, ".side-badge")!.dataset.side === "short" && clean(q(r, ".lv-res-usd")!.textContent).includes("+"))!;
    expect(q(short, "td.lv-res")!.classList.contains("pos")).toBe(true);
  });

  it("нет кошелька на входе — «% счёта: —» с пояснением, доллары остаются", async () => {
    const tab = await mount(snap(), () => json(200, stats([tr(1, "long", 5, 3, { equity_at_entry: null, ret_account_pct: null })])));
    const pct = q(tab.el, ".lv-closed-t tbody .lv-res-pct")!;
    expect(clean(pct.textContent)).toBe("% счёта: —");
    expect(pct.title).toContain("Нет точки капитала");
    expect(clean(q(tab.el, ".lv-closed-t tbody .lv-res-usd")!.textContent)).toBe("▲ +5,00 $");
  });

  it("реконструированная сделка: бейджи «реконструкция» и «оценка» в колонке причины", async () => {
    const tab = await mount(snap(), () => json(200, stats([tr(1, "long", -8, 3, { reconstructed: true, exact: false, reason: "стоп (реконструкция)" })])));
    const reason = clean(q(tab.el, ".lv-closed-t tbody td:last-child")!.textContent);
    expect(reason).toContain("реконструкция");
    expect(reason).toContain("оценка");
  });

  it("бейдж «оценка» только у сделок без данных Bybit (exact=false); у точных его нет, у реконструкции остаётся «реконструкция»", async () => {
    const tab = await mount(snap(), () => json(200, stats([
      tr(1, "long", 2.02, 3, { exact: true }), tr(2, "long", 2.08, 6, { exact: false }),
      tr(3, "short", -3, 9, { exact: true, reconstructed: true, reason: "стоп (реконструкция)" })])));
    const cells = qa(tab.el, ".lv-closed-t tbody tr[data-key] td:last-child").map((c) => clean(c.textContent));
    expect(cells).toHaveLength(3);
    expect(cells[0]).not.toContain("оценка");
    expect(cells[1]).toContain("оценка");
    expect(cells[2]).toContain("реконструкция");
    expect(cells[2]).not.toContain("оценка");
    expect(clean(q(tab.el, ".lv-closed-note")!.textContent)).toContain("по данным Bybit; «оценка» — рассчитано ботом, точнее после обновления фандинга");
  });

  it("открытая позиция — первая строка: «ОТКРЫТА», сторона, нереализованный $ и % счёта (не зафиксирован)", async () => {
    const tab = await mount(snap({ position: { side: "Sell", qty: 0.02, entry: 100000, upnl: 7.5, mark: 99600 } }),
      () => json(200, stats(ROWS, { open_position: { ...richStats().open_position!, opened_ts: NOW_S - 5 * 3600 } })));
    const first = qa(tab.el, ".lv-closed-t tbody tr")[0]!;
    expect(first.classList.contains("lv-open-row")).toBe(true);
    const t = clean(first.textContent);
    expect(t).toContain("ОТКРЫТА");
    expect(t).toContain("▽ШОРТ");
    expect(t).toContain("▲ +7,50 $");
    expect(t).toContain("+0,75% счёта · не зафиксирован");        // 7,5 / 1000
    expect(t).toContain("5 ч");
    expect(qa(tab.el, ".lv-closed-t tbody tr")).toHaveLength(5);
  });

  it("нет закрытых сделок за 30 дней: понятное пустое состояние (без падения)", async () => {
    const tab = await mount(snap(), () => json(200, stats([])));
    expect(tab.el.textContent).toContain("Закрытых сделок за 30 дней нет");
    expect(q(tab.el, ".lv-closed-t")).toBeNull();
  });

  it("no_trades от сервера (журнал пуст) — то же пустое состояние, а не ошибка", async () => {
    const tab = await mount(snap(), () => json(200, { available: false, reason: "no_trades", message: "В журнале ещё нет сделок бота.", range: "30d", computed_at: NOW_S, definitions: {}, warnings: [] }));
    expect(tab.el.textContent).toContain("Закрытых сделок за 30 дней нет");
    expect(q(tab.el, ".lv-trades-hint")).toBeNull();
  });
});

describe("«Закрытые сделки»: старый бэкенд, ошибки, кэш, опрос", () => {
  const FILL = { ts: iso(NOW - H), side: "Buy", qty: 0.01, price: 100000, reason: "бар", order_id: "o1" };

  it("404 (старый бэкенд): прежняя таблица ордеров + подсказка про перезапуск; названия «Покупка/Продажа» нейтральны", async () => {
    const tab = await mount(snap({ trades: [FILL, { ...FILL, side: "Sell", ts: iso(NOW - 2 * H) }] }), () => json(404, { detail: "Not Found" }));
    expect(q(tab.el, ".lv-closed")).toBeNull();
    const card = q(tab.el, ".lv-trades-card")!;
    expect(card.textContent).toContain("Последние сделки");
    expect(clean(q(card, ".lv-trades-hint")!.textContent)).toContain("run.bat");
    const badges = qa(card, ".side-badge");
    expect(badges.map((b) => clean(b.textContent))).toEqual(["△Покупка", "▽Продажа"]);
    expect(badges.every((b) => !/\b(pos|neg|long|short)\b/.test(b.className))).toBe(true);        // ордер — не лонг/шорт и не прибыль/убыток
  });

  it("сеть/5xx без прежних данных: откат к ордерам с другой подсказкой; вкладка работает", async () => {
    const tab = await mount(snap({ trades: [FILL] }), () => json(500, { detail: { code: "boom", message: "сломалось" } }));
    const hint = clean(q(tab.el, ".lv-trades-hint")!.textContent);
    expect(hint).toContain("Не удалось загрузить закрытые сделки");
    expect(hint).not.toContain("run.bat");
    expect(q(tab.el, ".lv-hero")).not.toBeNull();
  });

  it("available:false с другой причиной (нет капитала) — ордера с пояснением", async () => {
    const tab = await mount(snap({ trades: [FILL] }), () => json(200, { available: false, reason: "no_equity", message: "В окне меньше двух точек equity.", range: "30d", computed_at: NOW_S, definitions: {}, warnings: [] }));
    expect(clean(q(tab.el, ".lv-trades-hint")!.textContent)).toContain("недоступны");
  });

  it("свежий ответ вкладки «Статистика» берётся из общего кэша: отдельного запроса /api/stats нет", async () => {
    rememberStats(stats(ROWS), Date.now() - 5_000);
    const tab = await mount(snap(), () => { throw new Error("не должно запрашиваться"); });
    expect(calls.some((u) => u.startsWith("/api/stats"))).toBe(false);
    expect(qa(tab.el, ".lv-closed-t tbody tr")).toHaveLength(4);
  });

  it("устаревший кэш (старше 30 с) — запрос; опрос раз в 60 с, пока вкладка видна; после hide() — тишина", async () => {
    rememberStats(stats(ROWS), Date.now() - 120_000);
    const tab = await mount(snap(), () => json(200, stats(ROWS)));
    const n = () => calls.filter((u) => u.startsWith("/api/stats")).length;
    expect(n()).toBe(1);
    await vi.advanceTimersByTimeAsync(61_000);
    expect(n()).toBe(2);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(n()).toBe(3);
    tab.hide();
    await vi.advanceTimersByTimeAsync(180_000);
    expect(n()).toBe(3);
  });

  it("разовая ошибка опроса не затирает уже показанные итоги", async () => {
    let fail = false;
    const tab = await mount(snap(), () => (fail ? json(500, { detail: "x" }) : json(200, stats(ROWS))));
    expect(q(tab.el, ".lv-closed-t")).not.toBeNull();
    fail = true;
    await vi.advanceTimersByTimeAsync(61_000);
    expect(q(tab.el, ".lv-closed-t")).not.toBeNull();
    expect(q(tab.el, ".lv-trades-hint")).toBeNull();
  });

  it("новая закрытая сделка между опросами подсвечивается 10 с (первый опрос не подсвечивает ничего)", async () => {
    let rows = ROWS;
    const tab = await mount(snap(), () => json(200, stats(rows)));
    expect(qa(tab.el, ".lv-closed-t tr.tr-new")).toHaveLength(0);
    rows = [...ROWS, tr(5, "short", 9.75, 0.01)];
    await vi.advanceTimersByTimeAsync(61_000);
    const fresh = qa(tab.el, ".lv-closed-t tr.tr-new");
    expect(fresh).toHaveLength(1);
    expect(clean(fresh[0]!.textContent)).toContain("новая");
    expect(clean(fresh[0]!.textContent)).toContain("▲ +9,75 $");
    await vi.advanceTimersByTimeAsync(10_500);
    expect(qa(tab.el, ".lv-closed-t tr.tr-new")).toHaveLength(0);
    expect(qa(tab.el, ".lv-closed-t tbody tr")).toHaveLength(5);
  });

  it("«Показать ещё»: по 20 строк", async () => {
    const many = Array.from({ length: 30 }, (_, i) => tr(i + 1, i % 2 ? "short" : "long", i % 3 ? 1 : -1, i + 1));
    const tab = await mount(snap(), () => json(200, stats(many)));
    expect(qa(tab.el, ".lv-closed-t tbody tr")).toHaveLength(20);
    (q(tab.el, ".lv-closed .lv-more") as HTMLButtonElement).click();
    expect(qa(tab.el, ".lv-closed-t tbody tr")).toHaveLength(30);
  });
});

describe("вкладка «Бот (live)»: сторона и среда без двусмысленных цветов", () => {
  it("карточка позиции: бейдж ЛОНГ/ШОРТ нейтральный, P/L отдельно по знаку, % счёта рядом с долларами", async () => {
    const tab = await mount(snap({ position: { side: "Buy", qty: 0.02, entry: 100000, upnl: -12, mark: 99400 } }), () => json(200, stats(ROWS)));
    const side = q(tab.el, ".lv-pos .lv-side")!;
    expect(clean(side.textContent)).toBe("△ЛОНГ");
    expect(side.className).not.toMatch(/\b(pos|neg)\b/);
    const pnl = q(tab.el, ".lv-pos-pnl")!;
    expect(pnl.classList.contains("neg")).toBe(true);                       // лонг, но убыток -> именно neg
    expect(clean(pnl.textContent)).toContain("▼ −12,00 $");
    expect(clean(q(tab.el, ".lv-pos-pct")!.textContent)).toBe("−1,20% счёта");
  });

  it("герой: MAINNET не показывается; TESTNET и PAPER — заметные предупреждения", async () => {
    const t1 = await mount(snap({ env: "mainnet" }), () => json(200, stats(ROWS)));
    expect(t1.el.textContent).not.toMatch(/MAINNET|TESTNET|PAPER/);
    t1.hide(); document.body.replaceChildren();
    const t2 = await mount(snap({ env: "testnet" }), () => json(200, stats(ROWS)));
    expect(clean(q(t2.el, ".lv-pills")!.textContent)).toContain("TESTNET · не реальные деньги");
    expect(q(t2.el, ".lv-pills .badge.warn")).not.toBeNull();
    t2.hide(); document.body.replaceChildren();
    const t3 = await mount(snap({ env: "mainnet", paper: true }), () => json(200, stats(ROWS)));
    expect(clean(q(t3.el, ".lv-pills")!.textContent)).toContain("PAPER · без ордеров");
  });

  it("цель и сигналы: направление без цветов P/L (классы вклада и значения без pos/neg)", async () => {
    const tab = await mount(snap({ target_exposure: -0.5, legs: [{ key: "a", strategy: "a", tf: "1h", params: {}, long_only: false, weight: 1, enabled: true, signal: -1, updated: null }] }), () => json(200, stats(ROWS)));
    expect(q(tab.el, ".lv-leg-c")!.className).not.toMatch(/\b(pos|neg)\b/);
    expect(clean(q(tab.el, ".lv-leg-sig")!.textContent)).toBe("▽ шорт");
    expect(q(tab.el, ".lg-val")!.className).not.toMatch(/\b(pos|neg)\b/);
  });
});
