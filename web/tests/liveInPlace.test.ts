// @vitest-environment jsdom
// F2: вкладка «Бот (live)» обновляет секции на месте. Фокус (переключатель периода, «Обновить сейчас», «Показать ещё»), прокрутка и role=alert
// переживают обновление снимка; «N мин назад» ведёт тикер, а не повторная отрисовка.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { patchSection, sectionSig } from "../src/ui/keyed";
import type { LiveSnapshot, Meta } from "../src/types";

vi.mock("../src/charts/line", () => ({
  lineChart: () => ({ setData() {}, setLog() {}, setMarkers() {}, destroy() {} }),
}));

const { createLive } = await import("../src/tabs/live");

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 8; i++) await vi.advanceTimersByTimeAsync(0); };
const q = (el: ParentNode, s: string) => el.querySelector(s) as HTMLElement | null;
const qa = (el: ParentNode, s: string) => [...el.querySelectorAll(s)] as HTMLElement[];

const NOW = Date.parse("2026-10-08T12:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();
const MIN = 60_000, H = 3_600_000;
const history: [string, number][] = [[iso(NOW - 7 * 24 * H), 100], [iso(NOW - 48 * H), 120], [iso(NOW - 24 * H), 130], [iso(NOW - 2 * H), 140], [iso(NOW), 150]];
const orders = (n: number) => Array.from({ length: n }, (_, i) => ({ ts: iso(NOW - (i + 1) * MIN), side: i % 2 ? "Sell" : "Buy", qty: 0.001, price: 100000 + i, reason: "сигнал", order_id: `o${i}` }));
const leg = { key: "a", strategy: "EMA cross", tf: "1h", params: {}, long_only: false, weight: 1, enabled: true, signal: 1, updated: null };

function snap(over: Partial<LiveSnapshot> = {}): LiveSnapshot {
  return {
    available: true, env: "mainnet", enabled: true, killed: false, latch: false, equity: 150, peak_equity: 160,
    position: null, target_exposure: 0, last_bar: iso(NOW - H), heartbeat: iso(NOW - 30_000),
    settings: { stop_pct: 0.05, leverage: 2, max_leverage: 10, kill_dd: 0.25, kill_daily: 0.15 }, legs: [leg], trades: orders(30),
    equity_history: history, events: [{ ts: iso(NOW - 5 * MIN), level: "info", text: "старт" }],
    ...over,
  };
}

let current: LiveSnapshot;
const mounted: { hide(): void }[] = [];
async function mount(s: LiveSnapshot) {
  current = s;
  // /api/stats недоступен (старый бэкенд) -> блок сделок показывает список ордеров с кнопкой «Показать ещё»
  vi.stubGlobal("fetch", vi.fn(async (url: string) => (String(url).includes("/api/stats") ? json(404, { detail: "no" }) : json(200, current))));
  const tab = createLive({} as Meta);
  mounted.push(tab);
  document.body.append(tab.el);
  tab.show();
  await flush();
  return tab;
}
/** Сервер отдал новый снимок; ждём очередной опрос (10 с). */
async function update(s: LiveSnapshot) {
  current = s;
  await vi.advanceTimersByTimeAsync(10_000);
  await flush();
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  localStorage.clear();
  document.body.replaceChildren();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { mounted.splice(0).forEach((t) => t.hide()); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("live: фокус переживает обновление снимка", () => {
  it("переключатель периода графика и «Обновить сейчас» остаются теми же узлами и в фокусе", async () => {
    const tab = await mount(snap());
    const seg = qa(tab.el, ".lv-range button").find((b) => b.textContent === "7 д")!;
    seg.focus();
    expect(document.activeElement).toBe(seg);
    await update(snap({ equity: 151, equity_history: [...history, [iso(NOW + 5_000), 151]] }));
    expect(q(tab.el, ".lv-hero .lv-equity")!.textContent).toContain("151");            // снимок действительно применён
    expect(seg.isConnected).toBe(true);
    expect(document.activeElement).toBe(seg);

    const refresh = q(tab.el, 'button[aria-label="Обновить сейчас"]')!;
    refresh.focus();
    await update(snap({ equity: 152, equity_history: [...history, [iso(NOW + 15_000), 152]] }));
    expect(q(tab.el, ".lv-hero .lv-equity")!.textContent).toContain("152");
    expect(refresh.isConnected).toBe(true);
    expect(document.activeElement).toBe(refresh);
  });

  it("«Показать ещё» в фокусе: после обновления блока сделок фокус на кнопке «Показать ещё»", async () => {
    const tab = await mount(snap());
    const more = q(tab.el, ".lv-more") as HTMLButtonElement;
    expect(more).not.toBeNull();
    expect(qa(tab.el, ".lv-trades tbody tr")).toHaveLength(20);
    more.focus();
    await update(snap({ trades: orders(31) }));                                     // пришёл ещё один ордер -> блок перестроен
    const now = document.activeElement as HTMLElement;
    expect(now.classList.contains("lv-more")).toBe(true);
    expect(now.isConnected).toBe(true);
    expect(tab.el.contains(now)).toBe(true);
    now.click();                                                                     // и кнопка по-прежнему рабочая
    expect(qa(tab.el, ".lv-trades tbody tr").length).toBeGreaterThan(20);
  });

  it("неизменный блок сделок вообще не пересоздаётся (узел «Показать ещё» тот же)", async () => {
    const tab = await mount(snap());
    const more = q(tab.el, ".lv-more")!;
    more.focus();
    await update(snap({ equity: 155, equity_history: [...history, [iso(NOW + 5_000), 155]] }));    // изменился только капитал
    expect(q(tab.el, ".lv-more")).toBe(more);
    expect(document.activeElement).toBe(more);
  });

  it("шапка страницы не пересобирается (узел заголовка тот же) и не уходит из DOM между обновлениями", async () => {
    const tab = await mount(snap());
    const head = q(tab.el, ".page-head")!;
    await update(snap({ equity: 151 }));
    expect(q(tab.el, ".page-head")).toBe(head);
    expect(qa(tab.el, ".page-head")).toHaveLength(1);
  });
});

describe("live: role=alert не переозвучивается", () => {
  /** Считает появление новых узлов role=alert внутри вкладки. */
  function alertsAdded(root: HTMLElement) {
    const added: Element[] = [];
    const mo = new MutationObserver((recs) => {
      for (const r of recs) r.addedNodes.forEach((n) => {
        if (!(n instanceof Element)) return;
        if (n.getAttribute("role") === "alert") added.push(n);
        n.querySelectorAll("[role=alert]").forEach((x) => added.push(x));
      });
    });
    mo.observe(root, { childList: true, subtree: true });
    return { added, stop: () => mo.disconnect(), take: () => { const r = mo.takeRecords(); void r; } };
  }

  it("баннер «Аварийная остановка»: при неизменном содержимом остаётся тем же узлом, новых role=alert нет", async () => {
    const tab = await mount(snap({ killed: true, kill_reason: "просадка" }));
    const banner = q(tab.el, ".banner.error[role=alert]")!;
    expect(banner.textContent).toContain("Аварийная остановка");
    const w = alertsAdded(tab.el);
    await update(snap({ killed: true, kill_reason: "просадка", equity: 149, equity_history: [...history, [iso(NOW + 5_000), 149]] }));
    await update(snap({ killed: true, kill_reason: "просадка", equity: 148, equity_history: [...history, [iso(NOW + 15_000), 148]] }));
    expect(q(tab.el, ".lv-hero .lv-equity")!.textContent).toContain("148");
    expect(q(tab.el, ".banner.error[role=alert]")).toBe(banner);
    expect(w.added).toHaveLength(0);
    w.stop();
  });

  it("баннер «Бот не отвечает»: тикер меняет «N мин назад» внутри, но узел и озвучивание не трогаются", async () => {
    const old = iso(NOW - 4 * MIN);
    const tab = await mount(snap({ heartbeat: old }));
    const banner = q(tab.el, ".banner[role=alert]")!;
    expect(banner.textContent).toContain("Бот не отвечает");
    const before = banner.textContent;
    const w = alertsAdded(tab.el);
    await update(snap({ heartbeat: old, equity: 151 }));
    await vi.advanceTimersByTimeAsync(70_000);                                      // прошла ещё минута: тикер обновил возраст (4 -> 5 мин), тон остался янтарным
    expect(q(tab.el, ".banner[role=alert]")).toBe(banner);
    expect(banner.textContent).not.toBe(before);
    expect(w.added).toHaveLength(0);
    w.stop();
  });

  it("изменилось содержимое (другая причина) — баннер заменяется, это новое сообщение", async () => {
    const tab = await mount(snap({ killed: true, kill_reason: "просадка" }));
    const w = alertsAdded(tab.el);
    await update(snap({ killed: true, kill_reason: "дневной убыток" }));
    expect(q(tab.el, ".banner.error[role=alert]")!.textContent).toContain("дневной убыток");
    expect(w.added).toHaveLength(1);
    w.stop();
  });

  it("баннер ошибки загрузки: одна и та же ошибка на каждом опросе не переозвучивается", async () => {
    const tab = await mount(snap());
    vi.stubGlobal("fetch", vi.fn(async () => json(500, { detail: { code: "boom", message: "сломалось" } })));
    await vi.advanceTimersByTimeAsync(10_000); await flush();
    const err = q(tab.el, ".banner.error[role=alert]")!;
    expect(err.textContent).toContain("сломалось");
    const w = alertsAdded(tab.el);
    await vi.advanceTimersByTimeAsync(30_000); await flush();
    expect(q(tab.el, ".banner.error[role=alert]")).toBe(err);
    expect(w.added).toHaveLength(0);
    w.stop();
  });
});

describe("live: «N мин назад» и отсчёты ведёт тикер", () => {
  it("возраст ордеров и событий растёт без перерисовки: узлы строк те же, текст меняется", async () => {
    const tab = await mount(snap());
    const row = q(tab.el, ".lv-trades tbody tr")!;
    const ev = q(tab.el, ".lv-ev time")!;
    const ageOf = (r: HTMLElement) => q(r, "[data-tk=age]")!.textContent;
    expect(ageOf(row)).toBe("60 с назад");
    expect(ev.textContent).toMatch(/^5 мин назад · /);
    await vi.advanceTimersByTimeAsync(120_000);                                     // снимок не менялся: опросы возвращают тот же JSON
    expect(q(tab.el, ".lv-trades tbody tr")).toBe(row);
    expect(q(tab.el, ".lv-ev time")).toBe(ev);
    expect(ageOf(row)).toBe("3 мин назад");
    expect(ev.textContent).toMatch(/^7 мин назад · /);
  });

  it("в диффе снимка нет минутной корзины: тот же снимок через минуту не вызывает перерисовки секций", async () => {
    const tab = await mount(snap());
    const hero = q(tab.el, ".lv-hero")!;
    const pos = q(tab.el, ".lv-pos")!;
    await vi.advanceTimersByTimeAsync(65_000);                                      // сменилась минута; данные те же
    expect(q(tab.el, ".lv-hero")).toBe(hero);
    expect(q(tab.el, ".lv-pos")).toBe(pos);
  });

  it("длительность «держится» у открытой позиции тикает (data-tk=dur)", async () => {
    const tab = await mount(snap());
    const t = (await import("../src/tabs/liveTick"));
    const cell = document.createElement("span");
    document.body.append(cell);
    t.tk("dur", NOW - 90_000, cell);
    expect(cell.textContent).toBe("2 мин");
    t.tickAll(document.body, NOW + 3_510_000);
    expect(cell.textContent).toBe("1 ч");
    void tab;
  });

  it("обратный отсчёт дошёл до нуля (свеча закрылась) — цель переставляется на следующую границу", async () => {
    vi.setSystemTime(Date.parse("2026-10-08T12:59:57Z"));
    const tab = await mount(snap());
    const first = +q(tab.el, ".lv-nextbar [data-tk=cd]")!.dataset.v!;
    expect(first).toBe(Date.parse("2026-10-08T13:00:00Z"));
    await vi.advanceTimersByTimeAsync(5_000);                                       // граница часа прошла
    const next = +q(tab.el, ".lv-nextbar [data-tk=cd]")!.dataset.v!;
    expect(next).toBe(Date.parse("2026-10-08T14:00:00Z"));
  });
});

describe("live: смена режима страницы", () => {
  it("нет снимка -> появился снимок -> ошибка -> снова снимок: каркас собирается заново без дублей", async () => {
    const tab = await mount(snap({ available: false, reason: "no_db" } as Partial<LiveSnapshot>));
    expect(q(tab.el, ".lv-empty")).not.toBeNull();
    expect(q(tab.el, ".lv-hero")).toBeNull();
    await update(snap());
    expect(q(tab.el, ".lv-hero")).not.toBeNull();
    expect(q(tab.el, ".lv-empty")).toBeNull();
    expect(qa(tab.el, ".lv-hero")).toHaveLength(1);
    expect(qa(tab.el, ".page-head")).toHaveLength(1);
    vi.stubGlobal("fetch", vi.fn(async () => json(500, { detail: { code: "x", message: "ошибка сервера" } })));
    await vi.advanceTimersByTimeAsync(10_000); await flush();
    expect(q(tab.el, ".lv-hero")).toBeNull();
    expect(q(tab.el, ".banner.error")).not.toBeNull();
    current = snap();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => (String(url).includes("/api/stats") ? json(404, { detail: "no" }) : json(200, current))));
    await vi.advanceTimersByTimeAsync(10_000); await flush();
    expect(qa(tab.el, ".lv-hero")).toHaveLength(1);
    expect(q(tab.el, ".banner.error")).toBeNull();
  });
});

describe("patchSection / sectionSig", () => {
  const sec = (txt: string, extra = "") => {
    const d = document.createElement("section");
    d.innerHTML = `<p>${txt}</p><button class="a">1</button><button class="b">2</button>${extra}`;
    return d;
  };

  it("одинаковая подпись — DOM не трогается; текст [data-tk] подпись не меняет", () => {
    const slot = document.createElement("div");
    document.body.append(slot);
    const a = sec("x", '<span data-tk="age" data-v="1">5 с назад</span>');
    expect(patchSection(slot, a)).toBe(true);
    const b = sec("x", '<span data-tk="age" data-v="1">9 мин назад</span>');
    expect(sectionSig(a)).toBe(sectionSig(b));
    expect(patchSection(slot, b)).toBe(false);
    expect(slot.firstElementChild).toBe(a);
    const c = sec("x", '<span data-tk="age" data-v="2">5 с назад</span>');    // другой data-v — это изменение
    expect(patchSection(slot, c)).toBe(true);
    expect(slot.firstElementChild).toBe(c);
  });

  it("при замене фокус переходит на элемент с тем же порядковым номером, прокрутка сохраняется, null убирает секцию", () => {
    const slot = document.createElement("div");
    document.body.append(slot);
    const a = sec("one");
    a.className = "table-wrap";
    slot.append(a);
    (a.querySelector(".b") as HTMLElement).focus();
    a.scrollTop = 0;
    const b = sec("two");
    b.className = "table-wrap";
    expect(patchSection(slot, b)).toBe(true);
    expect(document.activeElement).toBe(b.querySelector(".b"));
    expect(patchSection(slot, null)).toBe(true);
    expect(slot.firstElementChild).toBeNull();
    expect(patchSection(slot, null)).toBe(false);
  });
});
