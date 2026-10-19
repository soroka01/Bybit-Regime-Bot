// api.ts: запасные варианты AbortSignal.timeout / AbortSignal.any (старые браузеры) — таймаут 120 с и отмена вызывающим работают одинаково.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { anySignal, api, ApiError, REQUEST_TIMEOUT_MS, timeoutSignal } from "../src/api";

type Statics = { timeout?: unknown; any?: unknown };
const S = AbortSignal as unknown as Statics;
const nativeTimeout = S.timeout, nativeAny = S.any;
const removeStatics = () => { S.timeout = undefined; S.any = undefined; };

/** fetch, который висит, пока его не отменят через signal. */
function hangingFetch() {
  return vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_res, rej) => {
    const sig = init?.signal;
    const fail = () => rej(Object.assign(new Error("aborted"), { name: "AbortError" }));
    if (sig?.aborted) fail(); else sig?.addEventListener("abort", fail, { once: true });
  }));
}
const okFetch = () => vi.fn(async () => new Response(JSON.stringify({ available: false }), { status: 200, headers: { "Content-Type": "application/json" } }));

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => {
  S.timeout = nativeTimeout; S.any = nativeAny;
  vi.useRealTimers(); vi.unstubAllGlobals();
});

describe("запасные варианты AbortSignal", () => {
  it("timeoutSignal без AbortSignal.timeout: срабатывает через ms, clear() снимает таймер", () => {
    removeStatics();
    const a = timeoutSignal(1000);
    expect(a.signal.aborted).toBe(false);
    vi.advanceTimersByTime(999);
    expect(a.signal.aborted).toBe(false);
    vi.advanceTimersByTime(2);
    expect(a.signal.aborted).toBe(true);
    const b = timeoutSignal(1000);
    b.clear();
    vi.advanceTimersByTime(5000);
    expect(b.signal.aborted).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("anySignal без AbortSignal.any: срабатывает от любого из сигналов, в том числе уже отменённого; clear() отписывает", () => {
    removeStatics();
    const c1 = new AbortController(), c2 = new AbortController();
    const m = anySignal([c1.signal, c2.signal]);
    expect(m.signal.aborted).toBe(false);
    c2.abort();
    expect(m.signal.aborted).toBe(true);

    const c3 = new AbortController();
    c3.abort();
    expect(anySignal([new AbortController().signal, c3.signal]).signal.aborted).toBe(true);

    const c4 = new AbortController();
    const m4 = anySignal([c4.signal]);
    m4.clear();
    c4.abort();
    expect(m4.signal.aborted).toBe(false);
  });

  it("запрос без AbortSignal.timeout/any: обычный ответ проходит, таймер страховки не остаётся", async () => {
    removeStatics();
    vi.stubGlobal("fetch", okFetch());
    const r = await api.live(new AbortController().signal);
    expect(r.available).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("запрос без AbortSignal.timeout/any: зависший запрос обрывается через 120 с кодом timeout", async () => {
    removeStatics();
    vi.stubGlobal("fetch", hangingFetch());
    const p = api.live(new AbortController().signal).catch((e) => e as ApiError);
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 10);
    const e = await p;
    expect(e).toBeInstanceOf(ApiError);
    expect((e as ApiError).code).toBe("timeout");
  });

  it("запрос без AbortSignal.timeout/any: отмена вызывающим — aborted (тихая), а не timeout", async () => {
    removeStatics();
    vi.stubGlobal("fetch", hangingFetch());
    const ctl = new AbortController();
    const p = api.live(ctl.signal).catch((e) => e as ApiError);
    await vi.advanceTimersByTimeAsync(1000);
    ctl.abort();
    const e = (await p) as ApiError;
    expect(e.code).toBe("aborted");
    expect(e.silent).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("запрос без сигнала вызывающего (только таймаут) тоже работает без AbortSignal.timeout", async () => {
    removeStatics();
    vi.stubGlobal("fetch", okFetch());
    const r = await api.live();
    expect(r.available).toBe(false);
  });
});
