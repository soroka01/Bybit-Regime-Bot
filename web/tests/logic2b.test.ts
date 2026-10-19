// Чистая логика фазы 2b: фильтр/CSV сделок, сравнение с B&H, «изменено», поиск стратегий, веса портфеля и импорт ног бота, сводки панели, оценка walk-forward.
import { describe, expect, it } from "vitest";
import { daysLabel, leverageSummary, marketSummary, overlaysSummary, strategySummary } from "../src/tabs/labLogic";
import { importLiveLegs, weightReport, weightSum } from "../src/tabs/portfolioLogic";
import { ddVersus, isShortPeriod, vsBenchmark } from "../src/tabs/resultLogic";
import { csvCell, csvFileName, csvText, filterTrades, tradeCounts, tradesToCsv } from "../src/tabs/tradesLogic";
import { windowMatrix, windowSpan, wfVerdict } from "../src/tabs/wfLogic";
import type { LegCfg, LiveSnapshot, StrategyMeta, TradeRow, WfWindow } from "../src/types";
import { changedParams, isChanged } from "../src/ui/paramLogic";
import { filterStrategies } from "../src/ui/strategySearch";

const trade = (side: "long" | "short", ret: number | null, extra: Partial<TradeRow> = {}): TradeRow =>
  ({ entry: "2026-01-01 00:00:00", exit: "2026-01-02 00:00:00", side, ret, bars: 5, entry_price: 100, exit_price: 101, ...extra });

describe("сделки: фильтр", () => {
  const rows = [trade("long", 0.05), trade("long", -0.02), trade("short", 0.01), trade("short", -0.04), trade("short", 0), trade("long", null)];
  it("фильтры по стороне и результату", () => {
    expect(filterTrades(rows, "all")).toHaveLength(6);
    expect(filterTrades(rows, "long").map((r) => r.side)).toEqual(["long", "long", "long"]);
    expect(filterTrades(rows, "short")).toHaveLength(3);
    expect(filterTrades(rows, "win").map((r) => r.ret)).toEqual([0.05, 0.01]);
    expect(filterTrades(rows, "loss").map((r) => r.ret)).toEqual([-0.02, -0.04]);
  });
  it("нулевой и пустой результат — ни в прибыльных, ни в убыточных; счётчики совпадают с фильтрами", () => {
    const c = tradeCounts(rows);
    expect(c).toEqual({ all: 6, long: 3, short: 3, win: 2, loss: 2 });
  });
  it("фильтр возвращает копию", () => {
    const all = filterTrades(rows, "all");
    expect(all).not.toBe(rows);
  });
});

describe("сделки: CSV", () => {
  it("ячейки: кавычки, запятая, перевод строки, пустые значения", () => {
    expect(csvCell("a,b")).toBe('"a,b"');
    expect(csvCell('он сказал "да"')).toBe('"он сказал ""да"""');
    expect(csvCell("line\nbreak")).toBe('"line\nbreak"');
    expect(csvCell(null)).toBe("");
    expect(csvCell(NaN)).toBe("");
    expect(csvCell(0)).toBe("0");
    expect(csvCell(-0.0123)).toBe("-0.0123");
  });
  it("заголовок и строки, CRLF, точка в числах", () => {
    const csv = tradesToCsv([trade("long", 0.0512, { entry_price: 85510.5 }), trade("short", null)]);
    const lines = csv.split("\r\n");
    expect(lines[0]).toBe("entry,exit,side,return,bars,entry_price,exit_price");
    expect(lines[1]).toBe("2026-01-01 00:00:00,2026-01-02 00:00:00,long,0.0512,5,85510.5,101");
    expect(lines[2]).toBe("2026-01-01 00:00:00,2026-01-02 00:00:00,short,,5,100,101");
    expect(lines[3]).toBe("");
  });
  it("csvText: формулы в начале экранируются апострофом, обычный текст — нет", () => {
    expect(csvText("=SUM(A1)")).toBe("'=SUM(A1)");
    expect(csvText("+1")).toBe("'+1");
    expect(csvText("-1")).toBe("'-1");
    expect(csvText("@x")).toBe("'@x");
    expect(csvText("\tтаб")).toBe("'\tтаб");
    expect(csvText("long")).toBe("long");
    expect(csvText(null)).toBe("");
  });
  it("текстовые ячейки сделки (время, сторона) защищены от формул; числа — нет", () => {
    const csv = tradesToCsv([trade("=1+1" as never, -0.05, { entry: "@evil" as never })]);
    expect(csv.split("\r\n")[1]).toBe("'@evil,2026-01-02 00:00:00,'=1+1,-0.05,5,100,101");
  });
  it("пустой список — только заголовок", () => {
    expect(tradesToCsv([])).toBe("entry,exit,side,return,bars,entry_price,exit_price\r\n");
  });
  it("имя файла без запрещённых символов", () => {
    expect(csvFileName(["trades", "BTCUSDT", "EMA cross", "1h"])).toBe("trades_BTCUSDT_EMA-cross_1h.csv");
    expect(csvFileName(["a/b:c", null, ""])).toBe("a-b-c.csv");
  });
});

describe("результат: сравнение с B&H", () => {
  it("доходность в п.п.", () => {
    expect(vsBenchmark(0.42, -0.02)).toEqual({ tone: "pos", text: "+44 п.п. к B&H" });
    expect(vsBenchmark(-0.28, -0.34)).toEqual({ tone: "pos", text: "+6,0 п.п. к B&H" });
    expect(vsBenchmark(0.1, 0.3)).toEqual({ tone: "neg", text: "−20 п.п. к B&H" });
    expect(vsBenchmark(0.1, 0.1).text).toBe("как B&H");
    expect(vsBenchmark(null, 0.1)).toEqual({ tone: "", text: "" });
  });
  it("просадка: мельче B&H — хорошо", () => {
    expect(ddVersus(-0.2, -0.5)).toEqual({ tone: "pos", text: "на 30 п.п. мельче B&H" });
    expect(ddVersus(-0.6, -0.5)).toEqual({ tone: "neg", text: "на 10 п.п. глубже B&H" });
    expect(ddVersus(-0.5, -0.5).tone).toBe("");
    expect(ddVersus(undefined, -0.5).text).toBe("");
  });
  it("короткий период — меньше 180 дней", () => {
    expect(isShortPeriod({ data_range: { start: "2026-01-01", end: "2026-03-01", bars: 1, step_min: 60 } })).toBe(true);
    expect(isShortPeriod({ data_range: { start: "2025-01-01", end: "2026-01-01", bars: 1, step_min: 60 } })).toBe(false);
  });
});

describe("параметры: изменено по сравнению с умолчанием", () => {
  it("полшага — порог шума", () => {
    expect(isChanged(12, 12, 1)).toBe(false);
    expect(isChanged(12.4, 12, 1)).toBe(false);
    expect(isChanged(13, 12, 1)).toBe(true);
    expect(isChanged(2.05, 2, 0.1)).toBe(false);
    expect(isChanged(2.2, 2, 0.1)).toBe(true);
    expect(isChanged(NaN, 2, 0.1)).toBe(false);
  });
  it("список изменённых имён", () => {
    const specs = { fast: { default: 12, step: 1 }, slow: { default: 50, step: 1 } };
    expect(changedParams({ fast: 12, slow: 60 }, specs)).toEqual(["slow"]);
    expect(changedParams({ fast: 12 }, specs)).toEqual([]);
  });
});

describe("поиск стратегий", () => {
  const list = [
    { name: "EMA cross", desc: "Лонг когда быстрая EMA выше медленной", derivative: false },
    { name: "Funding fade", desc: "Против перекоса фандинга", derivative: true },
    { name: "OI flush", desc: "Вход после обвала открытого интереса", derivative: true },
    { name: "Donchian breakout", desc: "Пробой N-свечного максимума", derivative: false },
    { name: "RSI reversion", desc: "Покупка перепроданности, EMA не нужна", derivative: false },
  ];
  it("пустой запрос — всё, в исходном порядке", () => {
    expect(filterStrategies(list, "  ").map((s) => s.name)).toEqual(list.map((s) => s.name));
  });
  it("имя важнее описания; регистр не важен", () => {
    expect(filterStrategies(list, "ema").map((s) => s.name)).toEqual(["EMA cross", "RSI reversion"]);
    expect(filterStrategies(list, "FUND")[0]!.name).toBe("Funding fade");
  });
  it("все слова обязательны; поиск по описанию и по слову «деривативы»", () => {
    expect(filterStrategies(list, "ema лонг").map((s) => s.name)).toEqual(["EMA cross"]);
    expect(filterStrategies(list, "деривативы").map((s) => s.name)).toEqual(["Funding fade", "OI flush"]);
    expect(filterStrategies(list, "пробой").map((s) => s.name)).toEqual(["Donchian breakout"]);
    expect(filterStrategies(list, "ёёё")).toEqual([]);
  });
});

describe("портфель: веса", () => {
  it("сумма весов игнорирует нечисла", () => {
    expect(weightSum([{ weight: 0.5 }, { weight: 0.25 }, { weight: NaN }])).toBeCloseTo(0.75);
  });
  it("Σ ≤ 100 %: ок; меньше — остаток в деньгах", () => {
    const r = weightReport([{ weight: 0.5 }, { weight: 0.5 }], 1);
    expect(r).toMatchObject({ tone: "ok", over: false, zero: [] });
    expect(r.lines).toEqual([]);
    const r2 = weightReport([{ weight: 0.5 }, { weight: 0.25 }], 1);
    expect(r2.tone).toBe("ok");
    expect(r2.lines[0]).toContain("Свободно 25%");
  });
  it("Σ > 100 %: предупреждение про потолок живого бота", () => {
    const r = weightReport([{ weight: 0.8 }, { weight: 0.7 }], 2);
    expect(r.over).toBe(true);
    expect(r.tone).toBe("warn");
    expect(r.lines[0]).toContain("Живой бот ограничивает суммарный вес 100");
    expect(r.lines[0]).toContain("3,00×");
  });
  it("нулевой вес ноги отмечается; все нули — bad", () => {
    const r = weightReport([{ weight: 0.5 }, { weight: 0 }, { weight: 0 }], 1);
    expect(r.zero).toEqual([1, 2]);
    expect(r.tone).toBe("warn");
    expect(r.lines.join(" ")).toContain("Ноги 2, 3 с весом 0");
    expect(weightReport([{ weight: 0 }], 1).tone).toBe("bad");
  });
});

describe("портфель: импорт ног живого бота", () => {
  const sp = (min: number, max: number, def: number) => ({ min, max, step: 1, default: def, kind: "int" as const, grid: [min, max] });
  const strategies = new Map<string, StrategyMeta>([
    ["EMA cross", { name: "EMA cross", desc: "", derivative: false, params: { fast: sp(3, 75, 12), slow: sp(14, 300, 50) } }],
    ["OI flush", { name: "OI flush", desc: "", derivative: true, params: { n: sp(2, 50, 10) } }],
  ]);
  const defaults: LegCfg[] = [{ strategy: "EMA cross", tf: "4h", params: {}, weight: 0.3, long_only: true }];
  const leg = (o: Partial<NonNullable<LiveSnapshot["legs"]>[number]>) =>
    ({ key: "k", strategy: "EMA cross", tf: "4h", params: { fast: 10, slow: 40 }, long_only: false, weight: 0.3, enabled: true, signal: 0, updated: null, ...o });
  const snap = (legs: ReturnType<typeof leg>[]): LiveSnapshot => ({ available: true, legs });

  it("переносит включённые ноги: параметры по спецификации, long_only из снимка", () => {
    const r = importLiveLegs(snap([leg({}), leg({ strategy: "OI flush", tf: "1h", params: { n: 500, junk: 1 }, weight: 0.2 }), leg({ enabled: false })]), strategies, ["1h", "4h"], defaults);
    expect(r.ok).toBe(true);
    expect(r.disabled).toBe(1);
    expect(r.legs).toEqual([
      { strategy: "EMA cross", tf: "4h", params: { fast: 10, slow: 40 }, weight: 0.3, long_only: false },
      { strategy: "OI flush", tf: "1h", params: { n: 50 }, weight: 0.2, long_only: false },
    ]);
  });
  it("long_only берётся из снимка (true/false), у одноимённой ноги по умолчанию — только как запасной вариант", () => {
    const r = importLiveLegs(snap([leg({ long_only: false }), leg({ strategy: "OI flush", tf: "1h", params: {}, long_only: true })]), strategies, ["1h", "4h"], defaults);
    expect(r.legs.map((l) => l.long_only)).toEqual([false, true]);       // у EMA cross@4h по умолчанию true, но снимок главнее
    expect(r.legacyLongOnly).toBe(false);
  });
  it("старый снимок без long_only: запасной вариант — одноимённая нога по умолчанию, иначе false; флаг legacyLongOnly", () => {
    const old = (o: object) => { const l = leg(o) as Record<string, unknown>; delete l.long_only; return l as ReturnType<typeof leg>; };
    const r = importLiveLegs(snap([old({}), old({ strategy: "OI flush", tf: "1h", params: {} })]), strategies, ["1h", "4h"], defaults);
    expect(r.legs.map((l) => l.long_only)).toEqual([true, false]);
    expect(r.legacyLongOnly).toBe(true);
    const mixed = importLiveLegs(snap([leg({ long_only: false }), old({ strategy: "OI flush", tf: "1h", params: {} })]), strategies, ["1h", "4h"], defaults);
    expect(mixed.legacyLongOnly).toBe(true);
  });
  it("недостающие параметры — по умолчанию; неизвестная стратегия и таймфрейм пропускаются с причиной", () => {
    const r = importLiveLegs(snap([leg({ params: {} }), leg({ strategy: "Нет такой" }), leg({ tf: "15m" })]), strategies, ["1h", "4h"], defaults);
    expect(r.legs).toHaveLength(1);
    expect(r.legs[0]!.params).toEqual({ fast: 12, slow: 50 });
    expect(r.skipped).toHaveLength(2);
    expect(r.skipped[0]).toContain("нет такой стратегии");
    expect(r.skipped[1]).toContain("таймфрейм");
  });
  it("нет снимка / бот не запущен / нет ног / все выключены — ok=false с причиной", () => {
    expect(importLiveLegs(null, strategies, ["4h"], defaults).ok).toBe(false);
    expect(importLiveLegs({ available: false, reason: "бот не запущен" }, strategies, ["4h"], defaults).reason).toBe("бот не запущен");
    expect(importLiveLegs({ available: true, legs: [] }, strategies, ["4h"], defaults).ok).toBe(false);
    const r = importLiveLegs(snap([leg({ enabled: false })]), strategies, ["4h"], defaults);
    expect(r).toMatchObject({ ok: false, disabled: 1 });
  });
  it("не больше 8 ног; вес зажимается в 1.5", () => {
    const many = Array.from({ length: 10 }, () => leg({ weight: 0.1 }));
    const r = importLiveLegs(snap(many), strategies, ["4h"], defaults);
    expect(r.legs).toHaveLength(8);
    expect(r.skipped).toHaveLength(2);
    expect(importLiveLegs(snap([leg({ weight: 9 })]), strategies, ["4h"], defaults).legs[0]!.weight).toBe(1.5);
  });
});

describe("Лаборатория: сводки секций", () => {
  it("период и рынок", () => {
    expect(daysLabel(365)).toBe("1 год");
    expect(daysLabel(730)).toBe("2 года");
    expect(daysLabel(1095)).toBe("3 года");
    expect(daysLabel(180)).toBe("180 д");
    expect(marketSummary({ symbol: "BTCUSDT", tf: "1h", days: 365, market: "perp" })).toBe("BTCUSDT · 1h · 1 год");
    expect(marketSummary({ symbol: "ETHUSDT", tf: "4h", days: 730, market: "spot" })).toBe("ETHUSDT · 4h · 2 года · спот");
  });
  it("стратегия, плечо, оверлеи", () => {
    expect(strategySummary("EMA cross", 0, 2)).toBe("EMA cross · параметры по умолчанию");
    expect(strategySummary("EMA cross", 1, 2)).toBe("EMA cross · изменено 1 из 2");
    expect(leverageSummary(3, false)).toBe("3×");
    expect(leverageSummary(20, true)).toContain("спот");
    expect(overlaysSummary({ htfOn: false, htf: 50, stopOn: false, stop: 3 })).toBe("выключены");
    expect(overlaysSummary({ htfOn: true, htf: 50, stopOn: true, stop: 3 })).toBe("HTF EMA 50 · ATR-стоп 3×");
  });
});

describe("walk-forward: оценка и окна", () => {
  const base = { total_return: 0.5, trades: 100, windows_positive: 0.75, avg_month: 0.03 };
  it("вердикты по порядку приоритета", () => {
    expect(wfVerdict({ ...base, trades: 10 }).key).toBe("few");
    expect(wfVerdict({ ...base, trades: null }).key).toBe("few");
    expect(wfVerdict({ ...base, total_return: -0.1 }).key).toBe("loss");
    expect(wfVerdict(base).key).toBe("stable");
    expect(wfVerdict({ ...base, windows_positive: 0.4 }).key).toBe("unstable");
    expect(wfVerdict({ ...base, avg_month: -0.01 }).key).toBe("unstable");
  });
  const w = (strategy: string, start: string, v: number | null): WfWindow =>
    ({ tf: "1h", strategy, variant: "base", test_start: start, params: "", test_return: v });
  const windows = [w("A", "2025-01-06", 0.1), w("A", "2025-04-07", -0.05), w("B", "2025-01-06", 0.02), w("B", "2024-10-07", 0.3)];
  it("сводка: число окон и границы", () => {
    expect(windowSpan(windows)).toEqual({ count: 3, first: "2024-10-07", lastStart: "2025-04-07" });
    expect(windowSpan([])).toBeNull();
  });
  it("матрица окно × вариант с подсчётом прибыльных", () => {
    const m = windowMatrix(["1h|A|base", "1h|B|base"], windows, (x) => `${x.tf}|${x.strategy}|${x.variant}`);
    expect(m.starts).toEqual(["2024-10-07", "2025-01-06", "2025-04-07"]);
    expect(m.rows[0]).toMatchObject({ cells: [null, 0.1, -0.05], positive: 1, total: 2 });
    expect(m.rows[1]).toMatchObject({ cells: [0.3, 0.02, null], positive: 2, total: 2 });
  });
});
