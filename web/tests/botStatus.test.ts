// @vitest-environment jsdom
// Плашка статуса бота: не больше одного опроса за раз, отмена при скрытии/свежем снимке от live, таймаут одного опроса, регистр стороны, порог «нет сигнала жизни».
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveSnapshot } from "../src/types";
import { botStatusPill, publishLive } from "../src/ui/botStatus";

const NOW = Date.parse("2026-10-08T12:00:00Z");
const clean = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();
const snap = (over: Partial<LiveSnapshot> = {}): LiveSnapshot => ({
  available: true, enabled: true, killed: false, equity: 1000, peak_equity: 1000, heartbeat: new Date(NOW - 20_000).toISOString(),
  position: null, ...over,
});
const json = (s: unknown) => new Response(JSON.stringify(s), { status: 200, headers: { "Content-Type": "application/json" } });

interface Pending { resolve(s: LiveSnapshot): void; signal: AbortSignal }
/** fetch, ответ которого отдаёт тест; отмена через signal отклоняет запрос как настоящий fetch. */
function controlledFetch() {
  const pending: Pending[] = [];
  const f = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((res, rej) => {
    const signal = init!.signal!;
    pending.push({ resolve: (s) => res(json(s)), signal });
    signal.addEventListener("abort", () => rej(Object.assign(new Error("aborted"), { name: "AbortError" })), { once: true });
  }));
  vi.stubGlobal("fetch", f);
  return { f, pending };
}
const flush = async () => { for (let i = 0; i < 6; i++) await vi.advanceTimersByTimeAsync(0); };
const setHidden = (v: boolean) => { Object.defineProperty(document, "hidden", { configurable: true, get: () => v }); document.dispatchEvent(new Event("visibilitychange")); };

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); document.body.replaceChildren(); });
afterEach(() => { setHidden(false); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("плашка статуса бота: опрос", () => {
  it("пока опрос идёт, visibilitychange и таймер не создают второй запрос", async () => {
    const { f, pending } = controlledFetch();
    const pill = botStatusPill();
    document.body.append(pill);
    await flush();
    expect(f).toHaveBeenCalledTimes(1);
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(15_000);        // сработал интервал — опрос всё ещё идёт
    expect(f).toHaveBeenCalledTimes(1);
    pending[0]!.resolve(snap());
    await flush();
    expect(clean(pill.textContent)).toBe("Торговля вкл");
    await vi.advanceTimersByTimeAsync(15_000);        // предыдущий завершён — следующий можно
    expect(f).toHaveBeenCalledTimes(2);
  });

  it("свежий снимок от вкладки live отменяет идущий опрос; его поздний ответ плашку не затирает", async () => {
    const { f, pending } = controlledFetch();
    const pill = botStatusPill();
    document.body.append(pill);
    await flush();
    expect(f).toHaveBeenCalledTimes(1);
    publishLive(snap());
    expect(pending[0]!.signal.aborted).toBe(true);
    expect(clean(pill.textContent)).toBe("Торговля вкл");
    pending[0]!.resolve(snap({ killed: true }));      // уже отменён: ответ не должен дойти
    await flush();
    expect(clean(pill.textContent)).toBe("Торговля вкл");
  });

  it("скрытие вкладки браузера прерывает опрос без «Нет связи»", async () => {
    const { pending } = controlledFetch();
    const pill = botStatusPill();
    document.body.append(pill);
    await flush();
    expect(pill.hidden).toBe(true);                  // первого ответа ещё нет
    setHidden(true);
    expect(pending[0]!.signal.aborted).toBe(true);
    await flush();
    expect(pill.hidden).toBe(true);                  // отмена — не ошибка связи
  });

  it("зависший опрос обрывается через 20 с: «Нет связи», следующий опрос снова возможен", async () => {
    const { f, pending } = controlledFetch();
    const pill = botStatusPill();
    document.body.append(pill);
    await flush();
    await vi.advanceTimersByTimeAsync(20_001);
    expect(pending[0]!.signal.aborted).toBe(true);
    expect(clean(pill.textContent)).toBe("Нет связи");
    await vi.advanceTimersByTimeAsync(15_000);
    expect(f.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});

describe("плашка статуса бота: сторона и сигнал жизни", () => {
  async function paint(s: LiveSnapshot) {
    vi.stubGlobal("fetch", vi.fn(async () => json(s)));
    const pill = botStatusPill();
    document.body.append(pill);
    await flush();
    return pill;
  }
  const pos = (side: string) => ({ side, qty: 0.003, entry: 100000, upnl: 1, mark: 100000 });

  it("сторона распознаётся без учёта регистра и пробелов (Buy / LONG / long / Sell)", async () => {
    for (const side of ["Buy", "BUY", "Long", " LONG", "long"]) {
      document.body.replaceChildren();
      expect(clean((await paint(snap({ position: pos(side) }))).querySelector(".bp-pos")!.textContent), side).toBe("△ ЛОНГ 0,0030");
    }
    for (const side of ["Sell", "SELL", "short", "Short"]) {
      document.body.replaceChildren();
      expect(clean((await paint(snap({ position: pos(side) }))).querySelector(".bp-pos")!.textContent), side).toBe("▽ ШОРТ 0,0030");
    }
  });

  it("порог «нет сигнала жизни» общий с вкладкой live: 3 минуты", async () => {
    const fresh = await paint(snap({ heartbeat: new Date(NOW - 170_000).toISOString() }));
    expect(clean(fresh.querySelector(".bp-trade")!.textContent)).toBe("Торговля вкл");
    document.body.replaceChildren();
    const stale = await paint(snap({ heartbeat: new Date(NOW - 190_000).toISOString() }));
    expect(clean(stale.querySelector(".bp-trade")!.textContent)).toBe("Нет сигнала жизни");
    document.body.replaceChildren();
    const none = await paint(snap({ heartbeat: null }));
    expect(clean(none.querySelector(".bp-trade")!.textContent)).toBe("Нет сигнала жизни");
  });
});
