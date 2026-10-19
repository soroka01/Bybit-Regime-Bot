// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, asApiError, dataJobsActive, onDataJobs } from "../src/api";
import { createRunner } from "../src/ui/runner";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const busy = () => json(429, { detail: { code: "busy", message: "сервер занят", waited_s: 20 } });
const ok = (v: unknown) => json(200, v);

/** fetch, который отвечает по сценарию; учитывает AbortSignal (как настоящий). */
function mockFetch(script: (call: number, init: RequestInit) => Response | Promise<Response>) {
  let n = 0;
  const f = vi.fn((_url: string, init: RequestInit = {}) => {
    const call = ++n;
    return new Promise<Response>((resolve, reject) => {
      const sig = init.signal;
      const onAbort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
      if (sig?.aborted) return onAbort();
      sig?.addEventListener("abort", onAbort, { once: true });
      Promise.resolve(script(call, init)).then(resolve, reject);
    });
  });
  vi.stubGlobal("fetch", f);
  return f;
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

interface R { v: number }
const mk = (over: Partial<Parameters<typeof createRunner<number, R>>[0]> = {}) => {
  const log = { start: 0, data: [] as R[], err: [] as ApiError[], retry: [] as [number, number][] };
  const runner = createRunner<number, R>({
    delay: 100,
    run: (b, sig) => api.backtest({ b } as never, sig) as unknown as Promise<R>,
    onStart: () => { log.start++; },
    onData: (r) => log.data.push(r),
    onError: (e) => log.err.push(e),
    onRetry: (k, max) => log.retry.push([k, max]),
    ...over,
  });
  return { runner, log };
};

describe("api.request", () => {
  it("разбирает detail-объект с кодом и extra", async () => {
    mockFetch(() => busy());
    const e = await api.backtest({} as never).catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e.code).toBe("busy");
    expect(e.status).toBe(429);
    expect(e.extra.waited_s).toBe(20);
  });
  it("сетевая ошибка -> network, отмена -> aborted (silent)", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("fail"))));
    expect((await api.meta().catch((x) => x)).code).toBe("network");
    const c = new AbortController();
    mockFetch(() => new Promise<Response>(() => {}));
    const p = api.backtest({} as never, c.signal).catch((x) => x);
    c.abort();
    const e = await p;
    expect(e.code).toBe("aborted");
    expect(e.silent).toBe(true);
  });
  it("asApiError оборачивает что угодно", () => {
    expect(asApiError(new Error("x")).message).toBe("x");
    expect(asApiError("строка").code).toBe("internal");
    const e = new ApiError(1, "c", "m");
    expect(asApiError(e)).toBe(e);
  });
  it("счётчик загрузок данных: растёт на время fetch и всегда уменьшается (в т.ч. при ошибке)", async () => {
    const seen: boolean[] = [];
    const off = onDataJobs(() => seen.push(dataJobsActive()));
    mockFetch(() => json(500, { detail: "boom" }));
    await expect(api.fetchData("BTCUSDT", "1h")).rejects.toBeInstanceOf(ApiError);
    expect(dataJobsActive()).toBe(false);
    expect(seen).toEqual([true, false]);
    off();
  });
  it("загрузка данных не имеет 120-секундной страховки, обычный вызов — имеет", async () => {
    const f = mockFetch(() => ok({}));
    await api.dataStatus("BTCUSDT", "1h");
    const withGuard = (f.mock.calls[0]![1] as RequestInit).signal;
    expect(withGuard).toBeDefined();
    await api.fetchData("BTCUSDT", "1h").catch(() => {});
    expect((f.mock.calls[1]![1] as RequestInit).signal).toBeUndefined();
  });
});

describe("createRunner", () => {
  it("дебаунс: несколько schedule подряд -> один запрос с последним телом", async () => {
    const f = mockFetch(() => ok({ v: 1 }));
    const { runner, log } = mk();
    runner.schedule(1); runner.schedule(2); runner.schedule(3);
    await vi.advanceTimersByTimeAsync(99);
    expect(f).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2);
    await flush();
    expect(f).toHaveBeenCalledTimes(1);
    expect(JSON.parse(f.mock.calls[0]![1]!.body as string).b).toBe(3);
    expect(log.data).toEqual([{ v: 1 }]);
  });

  it("seq: устаревший ответ отбрасывается, побеждает последний запрос", async () => {
    const pending: ((r: Response) => void)[] = [];
    mockFetch(() => new Promise<Response>((res) => { pending.push(res); }));
    const { runner, log } = mk();
    void runner.now(1);
    void runner.now(2);                 // первый запрос отменяется (abort) и устаревает
    await flush();
    pending[1]!(ok({ v: 2 }));
    await flush();
    pending[0]!(ok({ v: 1 }));          // поздний ответ старого запроса
    await flush();
    expect(log.data).toEqual([{ v: 2 }]);
    expect(log.err).toEqual([]);
  });

  it("cancel(): ответ после отмены не доходит до onData, abort тихий", async () => {
    mockFetch(() => new Promise<Response>(() => {}));
    const { runner, log } = mk();
    void runner.now(1);
    await flush();
    runner.cancel();
    await flush();
    expect(log.data).toEqual([]);
    expect(log.err).toEqual([]);
  });

  it("busy: повторяет с onRetry и в итоге получает данные", async () => {
    const f = mockFetch((n) => (n < 3 ? busy() : ok({ v: 7 })));
    const { runner, log } = mk();
    void runner.now(1);
    await flush();
    await vi.advanceTimersByTimeAsync(800); await flush();
    await vi.advanceTimersByTimeAsync(1600); await flush();
    expect(f).toHaveBeenCalledTimes(3);
    expect(log.retry).toEqual([[1, 3], [2, 3]]);
    expect(log.data).toEqual([{ v: 7 }]);
    expect(log.err).toEqual([]);
  });

  it("busy: после maxRetries сообщает об ошибке busy", async () => {
    const f = mockFetch(() => busy());
    const { runner, log } = mk();
    void runner.now(1);
    for (let i = 0; i < 6; i++) { await flush(); await vi.advanceTimersByTimeAsync(5000); }
    await flush();
    expect(f).toHaveBeenCalledTimes(4);     // 1 + 3 повтора
    expect(log.err.map((e) => e.code)).toEqual(["busy"]);
    expect(log.data).toEqual([]);
  });

  it("busy: суммарное ожидание ограничено capMs — повтор не планируется", async () => {
    const f = mockFetch(() => busy());
    const { runner, log } = mk({ capMs: 500, backoff: () => 800 });
    void runner.now(1);
    await flush();
    await vi.advanceTimersByTimeAsync(10_000); await flush();
    expect(f).toHaveBeenCalledTimes(1);
    expect(log.retry).toEqual([]);
    expect(log.err.map((e) => e.code)).toEqual(["busy"]);
  });

  it("новый schedule во время ожидания повтора отменяет старый повтор", async () => {
    const f = mockFetch((n) => (n === 1 ? busy() : ok({ v: n })));
    const { runner, log } = mk();
    void runner.now(1);
    await flush();                              // первый ответ busy, повтор запланирован
    runner.schedule(2);
    await vi.advanceTimersByTimeAsync(5000); await flush();
    expect(f).toHaveBeenCalledTimes(2);         // не 3: старый повтор не сработал
    expect(log.data).toEqual([{ v: 2 }]);
  });

  it("другие ошибки идут в onError без повторов; silent-ошибки подавляются", async () => {
    mockFetch(() => json(422, { detail: { code: "bad_request", message: "плохо" } }));
    const { runner, log } = mk();
    await runner.now(1);
    await flush();
    expect(log.err.map((e) => e.code)).toEqual(["bad_request"]);
    expect(log.retry).toEqual([]);

    mockFetch(() => json(429, { detail: { code: "superseded", message: "вытеснен" } }));
    const b = mk();
    await b.runner.now(1);
    await flush();
    expect(b.log.err).toEqual([]);
  });
});
