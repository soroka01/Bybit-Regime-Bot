import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api";
import { showError } from "../src/ui/banner";

afterEach(() => { vi.unstubAllGlobals(); });

describe("showError (A4): баннер «скачать данные» не затирается во время загрузки", () => {
  const dataMissing = () => new ApiError(409, "data_missing", "нет данных", { symbol: "BTCUSDT", tf: "1h" });

  it("без идущей загрузки busy заменяет баннер как обычно", () => {
    const slot = document.createElement("div");
    showError(slot, dataMissing(), () => {});
    showError(slot, new ApiError(429, "busy", "занят"), () => {});
    expect(slot.querySelector("[data-code=busy]")).not.toBeNull();
  });

  it("пока идёт fetch данных, busy/superseded не заменяют data_missing; другие ошибки — заменяют", async () => {
    let finish!: (r: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((res) => { finish = res; })));
    const job = api.fetchData("BTCUSDT", "1h");
    const slot = document.createElement("div");
    showError(slot, dataMissing(), () => {});
    showError(slot, new ApiError(429, "busy", "занят"), () => {});
    showError(slot, new ApiError(429, "superseded", "x"), () => {});
    expect(slot.querySelector("[data-code=data_missing]")).not.toBeNull();
    expect(slot.querySelector("[data-code=busy]")).toBeNull();
    showError(slot, new ApiError(0, "network", "сервер недоступен"), () => {});
    expect(slot.querySelector("[data-code=network]")).not.toBeNull();
    finish(new Response(JSON.stringify({ bars: 1 }), { status: 200 }));
    await job.catch(() => {});
  });
});

describe("meta: кэш/ETag", () => {
  it("обычный вызов не задаёт cache (работает ETag/304), после загрузки данных — no-cache", async () => {
    const f = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", f);
    await api.meta();
    await api.meta(undefined, true);
    expect((f.mock.calls[0] as unknown as [string, RequestInit])[1].cache).toBeUndefined();
    expect((f.mock.calls[1] as unknown as [string, RequestInit])[1].cache).toBe("no-cache");
  });
});
