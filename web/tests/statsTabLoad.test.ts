// @vitest-environment jsdom
// Вкладка «Статистика», загрузка (F11): при смене диапазона тело заменяется скелетоном, а не показывает цифры прежнего периода;
// возврат на вкладку/в окно перезапрашивает данные только если они старше ~30 с.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Meta, StatsResponse } from "../src/types";
import golden7 from "./fixtures/stats_7d.json";
import { richStats } from "./fixtures/statsRich";

vi.mock("../src/charts/line", () => ({ lineChart: () => ({ setData() {}, setLog() {}, setMarkers() {}, destroy() {} }) }));
vi.mock("../src/charts/bars", () => ({ barChart: () => ({ redraw() {}, destroy() {} }) }));
vi.mock("../src/charts/histogram", () => ({ histogramChart: () => ({ redraw() {}, destroy() {} }) }));

const { createStats } = await import("../src/tabs/stats");

const g7 = golden7 as unknown as StatsResponse;
const flush = async () => { for (let i = 0; i < 8; i++) await vi.advanceTimersByTimeAsync(0); };
const q = (el: HTMLElement, s: string) => el.querySelector(s) as HTMLElement | null;
const qa = (el: HTMLElement, s: string) => [...el.querySelectorAll(s)] as HTMLElement[];
const chip = (el: HTMLElement, label: string) => qa(el, ".st-ranges button").find((b) => b.textContent === label) as HTMLButtonElement;

interface Pending { range: string; resolve(body: StatsResponse): void; signal: AbortSignal }
/** fetch /api/stats, ответом на который управляет тест; запрос учитывает отмену. */
function controlled() {
  const pending: Pending[] = [];
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => new Promise<Response>((res, rej) => {
    const range = new URL(url, "http://x").searchParams.get("range") ?? "";
    calls.push(range);
    const signal = init!.signal!;
    pending.push({ range, signal, resolve: (b) => res(new Response(JSON.stringify({ ...b, range }), { status: 200, headers: { "Content-Type": "application/json" } })) });
    signal.addEventListener("abort", () => rej(Object.assign(new Error("aborted"), { name: "AbortError" })), { once: true });
  })));
  return { pending, calls };
}
/** Ответить на все ожидающие запросы данными по умолчанию. */
const answer = async (pending: Pending[], body: StatsResponse = g7) => { for (const p of pending.splice(0)) if (!p.signal.aborted) p.resolve(body); await flush(); };

const mounted: { hide(): void }[] = [];
const mount = () => {
  const tab = createStats({} as Meta);
  mounted.push(tab);
  document.body.append(tab.el);
  tab.show();
  return tab;
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(Date.parse("2026-10-08T12:00:00Z"));
  localStorage.clear();
  document.body.replaceChildren();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { mounted.splice(0).forEach((t) => t.hide()); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("смена диапазона", () => {
  it("пока грузится новый период, на месте цифр прежнего — скелетон; после ответа — данные нового периода", async () => {
    const { pending, calls } = controlled();
    const tab = mount();
    await answer(pending);                                              // 30d (по умолчанию)
    expect(q(tab.el, ".st-ready")).not.toBeNull();
    expect(qa(tab.el, ".st-hero .st-tile")).toHaveLength(8);
    chip(tab.el, "3м").click();
    await flush();
    expect(calls).toEqual(["30d", "90d"]);
    expect(q(tab.el, ".st-skel")).not.toBeNull();                       // скелетон
    expect(q(tab.el, ".st-ready")).toBeNull();                          // цифр прежнего периода на экране нет
    expect(tab.el.getAttribute("aria-busy")).toBe("true");
    expect(q(tab.el, ".st-window")).toBeNull();                         // строка окна/качества тоже убрана до ответа
    await answer(pending, richStats());
    expect(q(tab.el, ".st-skel")).toBeNull();
    expect(qa(tab.el, ".st-trades tbody tr")).toHaveLength(40);
    expect(tab.el.getAttribute("aria-busy")).toBeNull();
  });

  it("автообновление того же периода скелетон не показывает (данные остаются на экране, лишь приглушаются)", async () => {
    const { pending } = controlled();
    const tab = mount();
    await answer(pending);
    await vi.advanceTimersByTimeAsync(60_000);                          // сработал таймер: запрос ушёл
    expect(pending).toHaveLength(1);
    expect(q(tab.el, ".st-ready")).not.toBeNull();
    expect(q(tab.el, ".st-skel")).toBeNull();
    await answer(pending);
    expect(q(tab.el, ".st-ready")).not.toBeNull();
  });

  it("быстрое «1м → 3м → 1м»: скелетон не зависает, когда ответ на возвращённый период не отличается от уже показанного", async () => {
    const { pending, calls } = controlled();
    const tab = mount();
    await answer(pending);
    chip(tab.el, "3м").click();
    await flush();
    chip(tab.el, "1м").click();                                         // назад до ответа на 3м
    await flush();
    expect(calls).toEqual(["30d", "90d", "30d"]);
    await answer(pending);                                              // 90d отменён, 30d отвечает теми же данными
    expect(q(tab.el, ".st-skel")).toBeNull();
    expect(q(tab.el, ".st-ready")).not.toBeNull();
    expect(chip(tab.el, "1м").getAttribute("aria-pressed")).toBe("true");
  });

  it("ошибка загрузки нового периода показывает ошибку, а не цифры прежнего", async () => {
    const calls: string[] = [];
    let fail = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      return fail ? new Response(JSON.stringify({ detail: { code: "db_error", message: "сломалось" } }), { status: 500, headers: { "Content-Type": "application/json" } })
        : new Response(JSON.stringify({ ...g7, range: new URL(url, "http://x").searchParams.get("range") }), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    const tab = mount();
    await flush();
    expect(q(tab.el, ".st-ready")).not.toBeNull();
    fail = true;
    chip(tab.el, "3м").click();
    await flush();
    expect(q(tab.el, ".st-ready")).toBeNull();
    expect(q(tab.el, ".st-err")).not.toBeNull();
  });
});

describe("возврат на вкладку", () => {
  it("моложе 30 с — без нового запроса; старше — перезапрос; таймер автообновления считается от последнего ответа", async () => {
    const { pending, calls } = controlled();
    const tab = mount();
    await answer(pending);
    expect(calls).toHaveLength(1);

    tab.hide();
    await vi.advanceTimersByTimeAsync(10_000);
    tab.show();                                                          // 10 с — свежие данные
    await flush();
    expect(calls).toHaveLength(1);
    expect(q(tab.el, ".st-ready")).not.toBeNull();
    expect(q(tab.el, ".st-updated")!.textContent).toMatch(/обновлено 10 с назад/);

    await vi.advanceTimersByTimeAsync(49_000);                           // всего 59 с от ответа — ещё не время
    expect(calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1_500);                            // 60 с — сработал таймер (остаток минуты, а не новая полная минута)
    expect(calls).toHaveLength(2);
    await answer(pending);

    tab.hide();
    await vi.advanceTimersByTimeAsync(45_000);
    tab.show();                                                          // 45 с — устарели
    await flush();
    expect(calls).toHaveLength(3);
  });

  it("возврат в окно браузера (visibilitychange): то же правило 30 с", async () => {
    const { pending, calls } = controlled();
    const tab = mount();
    await answer(pending);
    const setHidden = (v: boolean) => { Object.defineProperty(document, "hidden", { configurable: true, get: () => v }); document.dispatchEvent(new Event("visibilitychange")); };
    try {
      setHidden(true);
      await vi.advanceTimersByTimeAsync(5_000);
      setHidden(false);
      await flush();
      expect(calls).toHaveLength(1);                                     // 5 с — не перезапрашиваем
      setHidden(true);
      await vi.advanceTimersByTimeAsync(40_000);
      setHidden(false);
      await flush();
      expect(calls).toHaveLength(2);                                     // 45 с — перезапрашиваем
    } finally {
      setHidden(false);
    }
    void tab;
  });

  it("после ошибки или при смене периода «свежесть» не применяется: возврат перезапрашивает", async () => {
    const { pending, calls } = controlled();
    const tab = mount();
    await answer(pending);
    chip(tab.el, "3м").click();
    await flush();
    expect(calls).toEqual(["30d", "90d"]);
    tab.hide();                                                          // запрос 90d отменён, данных этого периода нет
    tab.show();
    await flush();
    expect(calls).toEqual(["30d", "90d", "90d"]);
  });

  it("кнопка «Обновить сейчас» перезапрашивает всегда", async () => {
    const { pending, calls } = controlled();
    const tab = mount();
    await answer(pending);
    q(tab.el, 'button[aria-label="Обновить сейчас"]')!.click();
    await flush();
    expect(calls).toHaveLength(2);
  });
});
