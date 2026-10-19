// @vitest-environment jsdom
// Рынок (перп/спот): чистая логика, сохранённое состояние, «Копировать для бота» и поведение вкладок при смене рынка.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  effLeverage, effLongOnly, FEE_PCT, isMarket, marketNote, marketUi, SPOT_APPROX_NOTE, switchMarket, type MarketFields,
} from "../src/market";
import { persisted } from "../src/state";
import { botNotes, labPayload } from "../src/ui/clipboard";

describe("логика рынка", () => {
  it("белый список рынков", () => {
    expect(["perp", "spot"].every(isMarket)).toBe(true);
    for (const bad of ["Spot", "", "futures", null, 1, undefined]) expect(isMarket(bad)).toBe(false);
  });

  it("спот: плечо 1×, только лонг; перп — как выбрано", () => {
    expect(effLeverage("spot", 20)).toBe(1);
    expect(effLeverage("perp", 20)).toBe(20);
    expect(effLongOnly("spot", false)).toBe(true);
    expect(effLongOnly("perp", false)).toBe(false);
    expect(marketUi("spot")).toEqual({ levDisabled: true, longLocked: true });
    expect(marketUi("perp")).toEqual({ levDisabled: false, longLocked: false });
  });

  it("комиссия по умолчанию: перп 0.055 %, спот 0.10 %", () => {
    expect(FEE_PCT).toEqual({ perp: 0.055, spot: 0.1 });
  });

  it("смена рынка переключает комиссию на умолчание, пока её не правили", () => {
    const s: MarketFields = { market: "perp", fee: 0.055, feeTouched: false };
    expect(switchMarket(s, "spot")).toBe(true);
    expect(s).toEqual({ market: "spot", fee: 0.1, feeTouched: false });
    expect(switchMarket(s, "perp")).toBe(true);
    expect(s.fee).toBe(0.055);
  });

  it("правленная вручную комиссия не трогается", () => {
    const s: MarketFields = { market: "perp", fee: 0.07, feeTouched: true };
    expect(switchMarket(s, "spot")).toBe(false);
    expect(s).toEqual({ market: "spot", fee: 0.07, feeTouched: true });
    expect(switchMarket(s, "perp")).toBe(false);
    expect(s.fee).toBe(0.07);
  });

  it("пояснения: спот — приближение свечей и деривативные сигналы от перпа", () => {
    expect(marketNote("perp", true)).toBe("");
    expect(marketNote("spot", false)).toContain(SPOT_APPROX_NOTE);
    expect(marketNote("spot", false)).not.toContain("фандинга/OI/LSR");
    expect(marketNote("spot", true)).toMatch(/фандинга\/OI\/LSR считаются по данным перпа/);
  });
});

describe("сохранённое состояние: market", () => {
  beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });
  const defaults = { market: "perp" as "perp" | "spot", leverage: 1, feeTouched: false };
  const checks = { market: isMarket, leverage: (v: number) => v >= 1 && v <= 100 };

  it("принимает perp|spot, мусор заменяется умолчанием", () => {
    localStorage.setItem("tb.m1", JSON.stringify({ market: "spot", leverage: 50, feeTouched: true }));
    expect(persisted("m1", defaults, checks).state).toEqual({ market: "spot", leverage: 50, feeTouched: true });
    localStorage.setItem("tb.m2", JSON.stringify({ market: "futures", leverage: 101 }));
    expect(persisted("m2", defaults, checks).state).toEqual(defaults);
    localStorage.setItem("tb.m3", JSON.stringify({ market: 5, leverage: 0.5 }));
    expect(persisted("m3", defaults, checks).state).toEqual(defaults);
  });
});

describe("копирование для бота", () => {
  const base = {
    strategy: "EMA cross", tf: "1h", params: { fast: 12 }, symbol: "BTCUSDT", longOnly: false, leverage: 1,
    feePct: 0.055, slipPct: 0.02, days: 365, overlays: { htf: null, stop: null }, result: "доход +5%",
  };

  it("перп: строка «Рынок», строка Leg(...), без комментариев", () => {
    const t = labPayload({ ...base, market: "perp" });
    expect(t).toContain("Рынок: Перп");
    expect(t.split("\n")).toHaveLength(2);
    expect(t.split("\n")[1]).toMatch(/^Leg\("EMA cross", "1h", \{"fast": 12\}, weight=0\.5, long_only=False\)$/);
  });

  it("спот: «Рынок: Спот» и комментарий «# бот торгует только перп»; Leg(...) не меняется", () => {
    const t = labPayload({ ...base, market: "spot", longOnly: true });
    expect(t).toContain("Рынок: Спот");
    const lines = t.split("\n");
    expect(lines[1]).toMatch(/^Leg\(.*long_only=True\)$/);
    expect(lines).toContain("# бот торгует только перп");
  });

  it("плечо выше 3× — «бот ограничен 3×»", () => {
    expect(botNotes("perp", 3)).toEqual([]);
    expect(botNotes("perp", 20)[0]).toMatch(/^# бот ограничен 3×/);
    expect(labPayload({ ...base, leverage: 20 })).toContain("бот ограничен 3×");
    expect(labPayload({ ...base, leverage: 2 })).not.toContain("бот ограничен");
  });
});
