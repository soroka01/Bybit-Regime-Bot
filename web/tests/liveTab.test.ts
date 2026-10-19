// @vitest-environment jsdom
// Вкладка «Бот (live)» (jsdom, график подменён): герой (чип результата за окно графика), карточка лимитов риска, предупреждение «стоп × плечо × веса».
// Бот торгует на весь баланс счёта: поле settings.allocated_capital старого сервера игнорируется, его отсутствие ничего не ломает.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveSnapshot, Meta } from "../src/types";

vi.mock("../src/charts/line", () => ({
  lineChart: () => ({ setData() {}, setLog() {}, setMarkers() {}, destroy() {} }),
}));

const { createLive } = await import("../src/tabs/live");

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 8; i++) await vi.advanceTimersByTimeAsync(0); };
const q = (el: HTMLElement, s: string) => el.querySelector(s) as HTMLElement | null;
const qa = (el: HTMLElement, s: string) => [...el.querySelectorAll(s)] as HTMLElement[];

const NOW = Date.parse("2026-10-08T12:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();
const H = 3_600_000;
/** Кошелёк: 100 неделю назад, 120 двое суток назад, 130 сутки назад, 140 два часа назад, 150 сейчас (последняя точка). */
const history: [string, number][] = [[iso(NOW - 7 * 24 * H), 100], [iso(NOW - 48 * H), 120], [iso(NOW - 24 * H), 130], [iso(NOW - 2 * H), 140], [iso(NOW), 150]];

const leg = (key: string, weight: number, enabled: boolean) => ({ key, strategy: key, tf: "1h", params: {}, long_only: false, weight, enabled, signal: 1, updated: null });
const SAFE = { stop_pct: 0.05, leverage: 2, max_leverage: 10, kill_dd: 0.25, kill_daily: 0.15 };      // риск стопа 10 % < 15 %

function snap(over: Partial<LiveSnapshot> = {}): LiveSnapshot {
  return {
    available: true, env: "mainnet", enabled: true, killed: false, latch: false, equity: 150, peak_equity: 160,
    position: null, target_exposure: 0, last_bar: iso(NOW - H), heartbeat: iso(NOW - 30_000),
    settings: { ...SAFE }, legs: [leg("a", 1, true)], trades: [], equity_history: history, events: [],
    ...over,
  };
}

let current: LiveSnapshot;
const mounted: { hide(): void }[] = [];
async function mount(s: LiveSnapshot) {
  current = s;
  vi.stubGlobal("fetch", vi.fn(async () => json(200, current)));
  const tab = createLive({} as Meta);
  mounted.push(tab);
  document.body.append(tab.el);
  tab.show();
  await flush();
  return tab;
}
const totalChip = (el: HTMLElement) => qa(el, ".lv-pl").find((c) => !(c.textContent ?? "").includes("сегодня")) ?? null;
const rangeBtn = (el: HTMLElement, label: string) => qa(el, ".lv-range button").find((b) => b.textContent === label) as HTMLButtonElement;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  localStorage.clear();
  document.body.replaceChildren();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { mounted.splice(0).forEach((t) => t.hide()); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("вкладка «Бот (live)»: результат за период графика", () => {
  it("по умолчанию окно 24 ч: база — кошелёк сутки назад (130), чип с пояснением", async () => {
    const tab = await mount(snap());
    const c = totalChip(tab.el)!;
    expect(c).not.toBeNull();
    expect(c.textContent).toContain("за 24 ч");
    expect(c.textContent).not.toContain("всего");
    expect(c.textContent).toMatch(/\+20,00\s?\$/);                                  // 150 − 130
    expect(c.textContent).toContain("+15,38%");                                      // 20 / 130
    expect(c.getAttribute("title")).toContain("кошелька");
    expect(c.getAttribute("title")).toContain("ввод и вывод");
  });

  it("смена периода графика пересчитывает чип: 7 д и Всё -> база 100 (+50 %); чип один, не дублируется", async () => {
    const tab = await mount(snap());
    rangeBtn(tab.el, "7 д").click();
    await flush();
    let c = totalChip(tab.el)!;
    expect(c.textContent).toContain("за 7 дн.");                                    // снимок покрывает 7 суток целиком -> подпись полного периода
    expect(c.textContent).toContain("+50,00%");                                      // 150 / 100 − 1
    rangeBtn(tab.el, "Всё").click();
    await flush();                                                                    // /api/stats в этом тесте отдаёт тот же снимок (без equity) -> честный откат на снимок
    c = totalChip(tab.el)!;
    expect(c.textContent).toContain("за доступную историю (≈ 7,0 дн.)");
    expect(c.textContent).not.toContain("за всё время");
    expect(c.textContent).toContain("+50,00%");
    expect(qa(tab.el, ".lv-pl").filter((x) => !(x.textContent ?? "").includes("сегодня"))).toHaveLength(1);
    expect(qa(tab.el, ".lv-hero")).toHaveLength(1);
  });

  it("без settings.allocated_capital и со старой формой снимка (поле ещё приходит) вкладка рисуется одинаково", async () => {
    const fresh = await mount(snap());
    const textNew = totalChip(fresh.el)!.textContent;
    fresh.hide();
    document.body.replaceChildren();
    const tab = await mount(snap({ settings: { ...SAFE, allocated_capital: 160 } }));        // старый бэкенд всё ещё присылает поле
    expect(totalChip(tab.el)!.textContent).toBe(textNew);                            // 160 игнорируется: «к $160» больше нет
    expect(tab.el.textContent).not.toMatch(/всего к|выделенн/i);
    expect(qa(tab.el, ".lv-risks .chip").map((c) => c.textContent ?? "").join("|")).not.toContain("Капитал");
    expect(q(tab.el, ".lv-risks")).not.toBeNull();
  });

  it("риск-карточка: чипа «Капитал» нет, стоп и плечо остались; формулировки про кошелёк", async () => {
    const tab = await mount(snap({ settings: { ...SAFE, allocated_capital: 160 } }));
    expect(qa(tab.el, ".lv-risks .chip").map((c) => c.textContent ?? "")).toEqual(["Стоп5%", "Плечо2,0× из 10×"]);
    const text = q(tab.el, ".lv-risks")!.textContent ?? "";
    expect(text).toContain("Считается от пика кошелька");
    expect(text).toContain("Считается от кошелька на начало суток UTC");
    expect(text).not.toMatch(/капитал бота|выделенн/i);
  });

  it("нет истории equity: чипов P/L нет, вкладка не падает", async () => {
    const tab = await mount(snap({ equity_history: [] }));
    expect(totalChip(tab.el)).toBeNull();
    expect(tab.el.textContent).toContain("За сегодня данных пока нет");
    const bare = await mount(snap({ equity_history: undefined, equity: undefined, settings: undefined, legs: undefined }));
    expect(totalChip(bare.el)).toBeNull();
    expect(q(bare.el, ".lv-risks")).not.toBeNull();
    expect(q(bare.el, ".lv-stoprisk")).toBeNull();
  });

  it("минимальный снимок без полей: ни исключений, ни чипа, ни предупреждения", async () => {
    const tab = await mount({ available: true } as LiveSnapshot);
    expect(totalChip(tab.el)).toBeNull();
    expect(q(tab.el, ".lv-stoprisk")).toBeNull();
    expect(q(tab.el, ".lv-hero")).not.toBeNull();
  });
});

describe("вкладка «Бот (live)»: предупреждение «стоп × плечо × веса»", () => {
  const risky = (over: Record<string, number> = {}): LiveSnapshot => snap({ settings: { stop_pct: 0.2, leverage: 2, max_leverage: 10, kill_dd: 0.25, kill_daily: 0.4, ...over } });

  it("стоп 20 % × плечо 2 × веса 100 % = 40 % счёта > лимита просадки 25 %: предупреждение с точными числами", async () => {
    const tab = await mount(risky());
    const w = q(tab.el, ".lv-risks .lv-stoprisk")!;
    expect(w).not.toBeNull();
    expect(w.getAttribute("role")).toBe("note");
    const t = w.textContent ?? "";
    expect(t).toContain("Стоп 20% при плече 2,0× и весах 100% ≈ 40% счёта за одну сделку");
    expect(t).toContain("больше лимита (просадка 25%)");
    expect(t).toContain("сначала сработает биржевой стоп, затем kill-switch");
  });

  it("называет лимит убытка за день, если он меньше просадки", async () => {
    const tab = await mount(risky({ kill_daily: 0.1 }));
    expect(q(tab.el, ".lv-stoprisk")!.textContent).toContain("больше лимита (убыток за день 10%)");
  });

  it("веса считаются только по включённым ногам", async () => {
    // включена одна нога 0.5 -> 0.2·2·0.5 = 20 % < 25 %: предупреждения нет, хотя вместе с выключенной сумма была бы 100 %
    const tab = await mount({ ...risky(), legs: [leg("a", 0.5, true), leg("b", 0.5, false)] });
    expect(q(tab.el, ".lv-stoprisk")).toBeNull();
    const tab2 = await mount({ ...risky(), legs: [leg("a", 0.5, true), leg("b", 0.5, true)] });
    expect(q(tab2.el, ".lv-stoprisk")!.textContent).toContain("весах 100%");
  });

  it("ниже лимита предупреждения нет; ровно на лимите — «равно лимиту»", async () => {
    expect(q((await mount(snap())).el, ".lv-stoprisk")).toBeNull();                  // 5 % · 2 · 1 = 10 % < 15 %
    const eq = await mount(snap({ settings: { ...SAFE, stop_pct: 0.075 } }));        // 15 % == лимит убытка за день
    expect(q(eq.el, ".lv-stoprisk")!.textContent).toContain("равно лимиту (убыток за день 15%)");
  });

  it("нет настроек, ног или лимитов — без предупреждения и без падения", async () => {
    expect(q((await mount(snap({ settings: undefined }))).el, ".lv-stoprisk")).toBeNull();
    expect(q((await mount({ ...risky(), legs: undefined })).el, ".lv-stoprisk")).toBeNull();
    expect(q((await mount(snap({ settings: { stop_pct: 0.2, leverage: 2 } }))).el, ".lv-stoprisk")).toBeNull();
  });
});
