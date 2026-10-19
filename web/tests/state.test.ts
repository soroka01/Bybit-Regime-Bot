// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { persisted, takeFlag, validate } from "../src/state";

const defaults = { symbol: "BTCUSDT", days: 730, on: false, list: [] as string[], nested: { a: 1 }, nul: null as null | number };

beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe("validate", () => {
  it("оставляет только ключи defaults с тем же типом", () => {
    const raw = { symbol: "ETHUSDT", days: "730", on: true, list: {}, nested: [], nul: 5, extra: 1 };
    expect(validate(defaults, raw)).toEqual({ symbol: "ETHUSDT", on: true });
  });
  it("различает null / массив / объект", () => {
    expect(validate(defaults, { nested: null })).toEqual({});
    expect(validate(defaults, { list: ["a"], nested: { b: 2 } })).toEqual({ list: ["a"], nested: { b: 2 } });
    expect(validate(defaults, { nul: null })).toEqual({ nul: null });
  });
  it("не принимает не-объекты и не-конечные числа", () => {
    for (const raw of [null, 1, "x", [], undefined]) expect(validate(defaults, raw)).toEqual({});
    expect(validate(defaults, { days: Infinity })).toEqual({});
    expect(validate(defaults, { days: NaN })).toEqual({});
  });
  it("белые списки значений", () => {
    const checks = { days: (v: number) => [180, 365].includes(v), symbol: (v: string) => v === "BTCUSDT" };
    expect(validate(defaults, { days: 90, symbol: "DOGE" }, checks)).toEqual({});
    expect(validate(defaults, { days: 365, symbol: "BTCUSDT" }, checks)).toEqual({ days: 365, symbol: "BTCUSDT" });
  });
  it("проверка, бросающая исключение, считается отказом", () => {
    expect(validate(defaults, { days: 1 }, { days: () => { throw new Error("x"); } })).toEqual({});
  });
});

describe("persisted", () => {
  it("битый JSON -> дефолты без исключений", () => {
    localStorage.setItem("tb.t1", "{не json");
    const p = persisted("t1", defaults);
    expect(p.state).toEqual(defaults);
  });
  it("значения неверного типа заменяются дефолтами, остальные сохраняются", () => {
    localStorage.setItem("tb.t2", JSON.stringify({ symbol: 42, days: 365, on: "yes" }));
    const p = persisted("t2", defaults);
    expect(p.state.symbol).toBe("BTCUSDT");
    expect(p.state.days).toBe(365);
    expect(p.state.on).toBe(false);
  });
  it("whitelist: значение вне допустимых -> дефолт", () => {
    localStorage.setItem("tb.t3", JSON.stringify({ days: 90 }));
    const p = persisted("t3", defaults, { days: (v) => [180, 365, 730, 1095].includes(v) });
    expect(p.state.days).toBe(730);
  });
  it("save() откладывает запись на 200 мс и склеивает вызовы", () => {
    const p = persisted("t4", defaults);
    p.state.days = 180;
    p.save(); p.save();
    expect(localStorage.getItem("tb.t4")).toBeNull();
    vi.advanceTimersByTime(199);
    expect(localStorage.getItem("tb.t4")).toBeNull();
    vi.advanceTimersByTime(2);
    expect(JSON.parse(localStorage.getItem("tb.t4")!).days).toBe(180);
  });
  it("reset(): отложенная запись не воскрешает сброшенное состояние", () => {
    const p = persisted("t5", defaults);
    p.state.days = 180;
    p.save();                       // таймер запущен
    p.reset();                      // сброс до срабатывания таймера
    vi.advanceTimersByTime(1000);
    expect(localStorage.getItem("tb.t5")).toBeNull();
    expect(p.state.days).toBe(730);
  });
  it("reset() возвращает глубокие копии дефолтов", () => {
    const p = persisted("t6", defaults);
    p.state.nested.a = 99;
    p.state.list.push("x");
    p.reset();
    expect(p.state.nested.a).toBe(1);
    expect(p.state.list).toEqual([]);
    expect(defaults.nested.a).toBe(1);
  });
});

describe("takeFlag", () => {
  it("одноразовый deep-link флаг: читает и убирает query из адреса", () => {
    location.hash = "#/lab?opt";
    expect(takeFlag("lab", "opt")).toBe(true);
    expect(location.hash).toBe("#/lab");
    expect(takeFlag("lab", "opt")).toBe(false);
  });
  it("нет флага -> false, адрес не меняется", () => {
    location.hash = "#/best";
    expect(takeFlag("best", "run")).toBe(false);
    expect(location.hash).toBe("#/best");
  });
  it("другой флаг не считается", () => {
    location.hash = "#/best?runner";
    expect(takeFlag("best", "run")).toBe(false);
  });
});
