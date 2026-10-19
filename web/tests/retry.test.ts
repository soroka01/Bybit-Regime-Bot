import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api";
import { abortableSleep, withBusyRetry } from "../src/ui/retry";

const busy = (extra: Record<string, unknown> = {}) => new ApiError(429, "busy", "занят", extra);
const noSleep = () => Promise.resolve();

describe("withBusyRetry", () => {
  it("успех с первой попытки", async () => {
    const fn = vi.fn().mockResolvedValue(5);
    expect(await withBusyRetry(fn, { sleep: noSleep })).toEqual({ ok: true, value: 5 });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("busy, busy, успех — onWait получает «попытка k/n» и waited_s из тела 429", async () => {
    const fn = vi.fn().mockRejectedValueOnce(busy({ waited_s: 20 })).mockRejectedValueOnce(new ApiError(429, "superseded", "x")).mockResolvedValue("ok");
    const waits: [number, number, number | undefined][] = [];
    const r = await withBusyRetry(fn, { sleep: noSleep, onWait: (k, n, _e, w) => waits.push([k, n, w]) });
    expect(r).toEqual({ ok: true, value: "ok" });
    expect(waits).toEqual([[2, 4, 20], [3, 4, undefined]]);
  });

  it("исчерпаны попытки -> {ok:false, reason:busy}", async () => {
    const fn = vi.fn().mockRejectedValue(busy());
    const r = await withBusyRetry(fn, { sleep: noSleep, attempts: 3 });
    expect(r.ok).toBe(false);
    expect(fn).toHaveBeenCalledTimes(3);
    if (!r.ok) { expect(r.reason).toBe("busy"); expect(r.error.code).toBe("busy"); }
  });

  it("предел по времени: не ждём дольше capMs (время подставляем)", async () => {
    let t = 0;
    const fn = vi.fn(async () => { t += 25_000; throw busy(); });   // каждая попытка «ждёт» 25 с на сервере
    const r = await withBusyRetry(fn, { sleep: noSleep, now: () => t, capMs: 60_000, backoff: () => 1000, attempts: 10 });
    expect(r.ok).toBe(false);
    expect(fn).toHaveBeenCalledTimes(3);       // 25, 50, 75 с > 60 с -> стоп после третьей
  });

  it("не-busy ошибка пробрасывается сразу", async () => {
    const fn = vi.fn().mockRejectedValue(new ApiError(422, "bad_request", "плохо"));
    await expect(withBusyRetry(fn, { sleep: noSleep })).rejects.toMatchObject({ code: "bad_request" });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("произвольное исключение оборачивается в ApiError(internal)", async () => {
    await expect(withBusyRetry(() => Promise.reject(new Error("boom")), { sleep: noSleep })).rejects.toMatchObject({ code: "internal", message: "boom" });
  });

  it("abort во время паузы прекращает повторы", async () => {
    const c = new AbortController();
    const fn = vi.fn().mockRejectedValue(busy());
    const r = await withBusyRetry(fn, { signal: c.signal, sleep: async () => { c.abort(); }, attempts: 5 });
    expect(r.ok).toBe(false);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});

describe("abortableSleep", () => {
  it("завершается раньше срока при abort", async () => {
    const c = new AbortController();
    const t0 = Date.now();
    const p = abortableSleep(10_000, c.signal);
    c.abort();
    await p;
    expect(Date.now() - t0).toBeLessThan(1000);
  });
  it("уже отменённый сигнал — без ожидания", async () => {
    const c = new AbortController(); c.abort();
    await abortableSleep(10_000, c.signal);
  });
});
