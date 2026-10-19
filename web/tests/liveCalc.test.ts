import { describe, expect, it } from "vitest";
import {
  drawdownFrac, eventKind, filterEvents, fmtCountdown, gaugePos, heartbeatState, isLongSide, isNoiseEvent, legRows, legTotals, nextClose, splitLegs,
  parseSeries, plToday, positionView, rangeStart, riskMeter, sliceSeries, stopRisk, stopRiskWarning, sumWeights, tfMs, totalPl, tradeMarkers, valueAt,
} from "../src/tabs/liveCalc";

const T = (iso: string) => Date.parse(iso);

describe("P/L за сутки (UTC)", () => {
  const s = parseSeries([["2026-10-07T23:55:00+00:00", 150], ["2026-10-08T00:10:00+00:00", 160], ["2026-10-08T12:00:00+00:00", 158]]);
  it("база — первая точка текущих суток", () => {
    const r = plToday(s, 156, T("2026-10-08T15:00:00Z"))!;
    expect(r.base).toBe(160);
    expect(r.abs).toBeCloseTo(-4);
    expect(r.pct).toBeCloseTo(-0.025);
  });
  it("без equity берётся последняя точка", () => {
    expect(plToday(s, null, T("2026-10-08T15:00:00Z"))!.cur).toBe(158);
  });
  it("нет точек за сегодня -> null", () => {
    expect(plToday(s, 158, T("2026-10-09T01:00:00Z"))).toBeNull();
    expect(plToday([], 1, Date.now())).toBeNull();
  });
  it("мусор в ряду отбрасывается", () => {
    expect(parseSeries([["bad", 1], ["2026-10-08T00:00:00Z", null], ["2026-10-08T00:00:00Z", 5]]).length).toBe(1);
  });
  it("общий результат за период графика: первая точка окна -> текущий кошелёк", () => {
    const all = totalPl(s, 158.8)!;                                  // вся история: база — первая точка ряда (150)
    expect(all.base).toBe(150);
    expect(all.abs).toBeCloseTo(8.8);
    expect(all.pct).toBeCloseTo(8.8 / 150);
    expect(all.since).toBe(T("2026-10-07T23:55:00Z"));
    const day = totalPl(s, 158.8, T("2026-10-08T00:00:00Z"))!;       // окно с 00:00: «входное» значение на старте окна = 150, как у графика
    expect(day.base).toBe(150);
    expect(day.since).toBe(T("2026-10-08T00:00:00Z"));
    const late = totalPl(s, 156, T("2026-10-08T00:10:00Z"))!;        // старт ровно на точке: база 160
    expect(late.base).toBe(160);
    expect(late.abs).toBeCloseTo(-4);
    expect(late.pct).toBeCloseTo(-0.025);
  });
  it("общий результат: нет equity — берётся последняя точка; нет истории, нулевая база, мусор — null без падения", () => {
    expect(totalPl(s, null)!.cur).toBe(158);
    expect(totalPl(s, undefined)!.abs).toBeCloseTo(8);
    expect(totalPl([], 100)).toBeNull();
    expect(totalPl(null, 100)).toBeNull();
    expect(totalPl(undefined, 100)).toBeNull();
    expect(totalPl([{ t: 1, v: 0 }], 100)).toBeNull();                // процент от нуля не определён
    expect(totalPl([{ t: 1, v: 100 }], Number.NaN)!.cur).toBe(100);   // NaN equity -> последняя точка
    expect(totalPl([], null)).toBeNull();
    expect(totalPl(s, 158, T("2026-10-09T00:00:00Z"))).toBeNull();           // окно позже последней точки: данных внутри окна нет
  });
  it("просадка от пика", () => {
    expect(drawdownFrac(90, 100)).toBeCloseTo(0.1);
    expect(drawdownFrac(110, 100)).toBe(0);
    expect(drawdownFrac(null, 100)).toBeNull();
  });
});

describe("сигнал жизни", () => {
  const now = T("2026-10-08T12:00:00Z");
  it("пороги 3 и 10 минут", () => {
    expect(heartbeatState("2026-10-08T11:58:00Z", now).tone).toBe("ok");
    expect(heartbeatState("2026-10-08T11:56:00Z", now).tone).toBe("warn");
    expect(heartbeatState("2026-10-08T11:45:00Z", now).tone).toBe("bad");
    expect(heartbeatState(null, now)).toEqual({ ageS: null, tone: "bad" });
  });
});

describe("закрытие свечи", () => {
  it("разбор таймфрейма", () => {
    expect(tfMs("1h")).toBe(3_600_000);
    expect(tfMs("15m")).toBe(900_000);
    expect(tfMs("1d")).toBe(86_400_000);
    expect(tfMs("x")).toBeNull();
  });
  it("следующая граница в UTC", () => {
    const now = T("2026-10-08T20:52:45Z");
    expect(new Date(nextClose("1h", now)!).toISOString()).toBe("2026-10-08T21:00:00.000Z");
    expect(new Date(nextClose("4h", now)!).toISOString()).toBe("2026-10-09T00:00:00.000Z");
    expect(new Date(nextClose("6h", now)!).toISOString()).toBe("2026-10-09T00:00:00.000Z");
    expect(new Date(nextClose("6h", T("2026-10-08T05:59:59Z"))!).toISOString()).toBe("2026-10-08T06:00:00.000Z");
  });
  it("ровно на границе — следующая, а не текущая", () => {
    expect(new Date(nextClose("1h", T("2026-10-08T21:00:00Z"))!).toISOString()).toBe("2026-10-08T22:00:00.000Z");
  });
  it("недельная свеча — понедельник 00:00 UTC", () => {
    const n = new Date(nextClose("1w", T("2026-10-08T12:00:00Z"))!);   // четверг
    expect(n.toISOString()).toBe("2026-10-12T00:00:00.000Z");
    expect(n.getUTCDay()).toBe(1);
  });
  it("формат отсчёта", () => {
    expect(fmtCountdown(23 * 60_000 + 14_000)).toBe("23:14");
    expect(fmtCountdown(3 * 3600_000 + 5 * 60_000 + 9_000)).toBe("3:05:09");
    expect(fmtCountdown(26 * 3600_000)).toBe("1 д 02:00:00");
    expect(fmtCountdown(-5)).toBe("00:00");
  });
});

describe("позиция: стоп и ликвидация", () => {
  const set = { leverage: 2, stop_pct: 0.2 };
  it("лонг", () => {
    const v = positionView({ side: "long", qty: 0.003, entry: 80000, upnl: 3, mark: 81000 }, set)!;
    expect(v.long).toBe(true);
    expect(v.notional).toBeCloseTo(243);
    expect(v.margin).toBeCloseTo(121.5);
    expect(v.upnlPct).toBeCloseTo(3 / 121.5);
    expect(v.stop).toBeCloseTo(64000);
    expect(v.liq).toBeCloseTo(80000 * (1 - (0.5 - 0.005)));
    expect(v.stopDist).toBeCloseTo((81000 - 64000) / 81000);
    expect(v.stopBeyondLiq).toBe(false);
  });
  it("шорт: стоп и ликвидация выше входа", () => {
    const v = positionView({ side: "Sell", qty: 1, entry: 100, upnl: 0, mark: 100 }, { leverage: 5, stop_pct: 0.1 })!;
    expect(v.long).toBe(false);
    expect(v.stop).toBeCloseTo(110);
    expect(v.liq).toBeCloseTo(100 * (1 + 0.2 - 0.005));
    expect(v.stopDist).toBeCloseTo(0.1);
    expect(v.stopBeyondLiq).toBe(false);
  });
  it("стоп дальше ликвидации", () => {
    const v = positionView({ side: "long", qty: 1, entry: 100, upnl: 0, mark: 100 }, { leverage: 5, stop_pct: 0.3 })!;
    expect(v.stopBeyondLiq).toBe(true);
  });
  it("нет позиции / нет настроек", () => {
    expect(positionView(null, set)).toBeNull();
    expect(positionView({ side: "long", qty: 0, entry: 1, upnl: 0, mark: 1 }, set)).toBeNull();
    const v = positionView({ side: "long", qty: 1, entry: 100, upnl: 1, mark: 100 }, undefined)!;
    expect(v.stop).toBeNull();
    expect(v.liq).toBeNull();
    expect(v.margin).toBeNull();
  });
  it("сторона в разных написаниях", () => {
    expect(isLongSide("Buy")).toBe(true);
    expect(isLongSide("long")).toBe(true);
    expect(isLongSide("Sell")).toBe(false);
    expect(isLongSide("short")).toBe(false);
  });
});

describe("лимиты риска", () => {
  it("пороги цвета 60% / 85%", () => {
    expect(riskMeter(0.05, 0.25)!.tone).toBe("ok");
    expect(riskMeter(0.149, 0.25)!.tone).toBe("ok");
    expect(riskMeter(0.15, 0.25)!.tone).toBe("warn");
    expect(riskMeter(0.2124, 0.25)!.tone).toBe("warn");
    expect(riskMeter(0.2125, 0.25)!.tone).toBe("bad");
    expect(riskMeter(0.3, 0.25)!.ratio).toBeCloseTo(1.2);
  });
  it("запас и невалидные входы", () => {
    expect(riskMeter(0.1, 0.25)!.left).toBeCloseTo(0.15);
    expect(riskMeter(-0.1, 0.25)!.ratio).toBe(0);
    expect(riskMeter(null, 0.25)).toBeNull();
    expect(riskMeter(0.1, 0)).toBeNull();
  });
});

describe("стоп × плечо × веса", () => {
  const leg = (weight: number, enabled: boolean | undefined) => ({ key: String(Math.random()), strategy: "S", tf: "1h", params: {}, long_only: false, weight, enabled: enabled as boolean, signal: 1, updated: null });
  it("stopRisk = стоп × плечо × Σ весов", () => {
    expect(stopRisk(0.2, 2, 1)).toBeCloseTo(0.4);
    expect(stopRisk(0.05, 2, 0.5)).toBeCloseTo(0.05);
    expect(stopRisk(0.08, 1, 1)).toBeCloseTo(0.08);
  });
  it("stopRisk: нет данных, нули и мусор -> null", () => {
    expect(stopRisk(null, 2, 1)).toBeNull();
    expect(stopRisk(0.2, undefined, 1)).toBeNull();
    expect(stopRisk(0.2, 2, null)).toBeNull();
    expect(stopRisk(0, 2, 1)).toBeNull();
    expect(stopRisk(0.2, 0, 1)).toBeNull();
    expect(stopRisk(0.2, 2, 0)).toBeNull();
    expect(stopRisk(Number.NaN, 2, 1)).toBeNull();
  });
  it("sumWeights: только ноги с enabled === true; нет списка — null", () => {
    expect(sumWeights([leg(0.5, true), leg(0.25, true), leg(1, false), leg(1, undefined)])).toBeCloseTo(0.75);
    expect(sumWeights([leg(1, false)])).toBe(0);
    expect(sumWeights([])).toBe(0);
    expect(sumWeights(undefined)).toBeNull();
    expect(sumWeights(null)).toBeNull();
  });
  const st = { stop_pct: 0.2, leverage: 2, kill_dd: 0.25, kill_daily: 0.4 };
  it("предупреждение: риск 40 % >= лимита просадки 25 % — лимит назван", () => {
    const w = stopRiskWarning(st, [leg(1, true)])!;
    expect(w).toMatchObject({ stopPct: 0.2, leverage: 2, weights: 1, limit: 0.25, limitKey: "kill_dd", limitLabel: "просадка" });
    expect(w.risk).toBeCloseTo(0.4);
  });
  it("предупреждение берёт меньший из kill_dd / kill_daily", () => {
    expect(stopRiskWarning({ ...st, kill_daily: 0.1 }, [leg(1, true)])).toMatchObject({ limit: 0.1, limitKey: "kill_daily", limitLabel: "убыток за день" });
    expect(stopRiskWarning({ ...st, kill_dd: 0.3, kill_daily: 0.3 }, [leg(1, true)])).toMatchObject({ limitKey: "kill_dd" });   // при равенстве — просадка, как в боте
    expect(stopRiskWarning({ stop_pct: 0.2, leverage: 2, kill_daily: 0.3 }, [leg(1, true)])).toMatchObject({ limitKey: "kill_daily" });   // второго лимита нет
  });
  it("граница: риск ровно равен лимиту — предупреждение есть; чуть меньше — нет", () => {
    expect(stopRiskWarning({ stop_pct: 0.125, leverage: 2, kill_dd: 0.25, kill_daily: 0.25 }, [leg(1, true)])).not.toBeNull();
    expect(stopRiskWarning({ stop_pct: 0.12, leverage: 2, kill_dd: 0.25, kill_daily: 0.25 }, [leg(1, true)])).toBeNull();
  });
  it("ниже лимита, выключенные ноги и вес 0 — предупреждения нет", () => {
    expect(stopRiskWarning({ ...st, stop_pct: 0.05 }, [leg(1, true)])).toBeNull();                         // 0.05·2·1 = 10 % < 25 %
    expect(stopRiskWarning(st, [leg(1, false)])).toBeNull();                                               // нет включённых ног
    expect(stopRiskWarning(st, [leg(0, true)])).toBeNull();
    expect(stopRiskWarning(st, [leg(0.5, true), leg(1, false)])).toBeNull();                               // 0.2·2·0.5 = 20 % < 25 %
  });
  it("нет настроек / ног / лимитов — null без падения", () => {
    expect(stopRiskWarning(undefined, [leg(1, true)])).toBeNull();
    expect(stopRiskWarning({}, [leg(1, true)])).toBeNull();
    expect(stopRiskWarning(st, undefined)).toBeNull();
    expect(stopRiskWarning(st, null)).toBeNull();
    expect(stopRiskWarning({ stop_pct: 0.2, leverage: 2 }, [leg(1, true)])).toBeNull();                    // лимитов kill нет
    expect(stopRiskWarning({ stop_pct: 0.2, leverage: null, kill_dd: 0.1, kill_daily: 0.1 }, [leg(1, true)])).toBeNull();
  });
});

describe("ноги, события, маркеры, диапазоны", () => {
  const legs = [
    { key: "a", strategy: "A", tf: "6h", params: {}, long_only: false, weight: 1, enabled: true, signal: 1, updated: null },
    { key: "b", strategy: "B", tf: "1h", params: {}, long_only: false, weight: 0, enabled: true, signal: -1, updated: null },
    { key: "c", strategy: "C", tf: "4h", params: {}, long_only: false, weight: 0.5, enabled: false, signal: 1, updated: null },
  ];
  it("вклад = сигнал × вес; вес 0 и выключенные не торгуются", () => {
    const rows = legRows(legs, T("2026-10-08T12:00:00Z"));
    expect(rows.map((r) => r.traded)).toEqual([true, false, false]);
    expect(rows[0]!.contribution).toBe(1);
    expect(rows[1]!.contribution).toBeNull();
    expect(legTotals(rows)).toEqual({ weights: 1, contribution: 1 });
  });
  it("splitLegs: активные (traded) отдельно от вес-0 и выключенных, порядок сохраняется", () => {
    const rows = legRows(legs, T("2026-10-08T12:00:00Z"));
    const { active, idle } = splitLegs(rows);
    expect(active.map((r) => r.name)).toEqual(["A"]);
    expect(idle.map((r) => r.name)).toEqual(["B", "C"]);
    expect(legTotals(active)).toEqual(legTotals(rows));
    expect(splitLegs([])).toEqual({ active: [], idle: [] });
  });
  it("фильтр событий и порядок новые-первыми", () => {
    const ev = [
      { ts: "2026-10-08T10:00:00Z", level: "info", text: "x" },
      { ts: "2026-10-08T12:00:00Z", level: "error", text: "e" },
      { ts: "2026-10-08T11:00:00Z", level: "trade", text: "t" },
      { ts: "2026-10-08T09:00:00Z", level: "warn", text: "w" },
    ];
    expect(filterEvents(ev, "all").map((e) => e.text)).toEqual(["e", "t", "x", "w"]);
    expect(filterEvents(ev, "alerts").map((e) => e.text)).toEqual(["e", "w"]);
    expect(filterEvents(ev, "trades").map((e) => e.text)).toEqual(["t"]);
    expect(filterEvents(null, "all")).toEqual([]);
    expect(eventKind("WARNING")).toBe("warn");
    expect(eventKind("???")).toBe("info");
    expect(isNoiseEvent("Действую с опозданием на 39 мин")).toBe(true);
    expect(isNoiseEvent("Бот запущен")).toBe(false);
  });
  it("окна графика и срез ряда", () => {
    const s = parseSeries([["2026-10-01T00:00:00Z", 1], ["2026-10-07T00:00:00Z", 2], ["2026-10-08T00:00:00Z", 3]]);
    const anchor = T("2026-10-08T00:00:00Z");
    expect(rangeStart("all", anchor)).toBe(-Infinity);
    const cut = sliceSeries(s, rangeStart("24h", anchor));
    expect(cut.map((p) => p.v)).toEqual([2, 3]);               // входное значение на старте окна
    expect(cut[0]!.t).toBe(anchor - 86_400_000);
    expect(sliceSeries(s, rangeStart("all", anchor)).length).toBe(3);
  });
  it("маркеры сделок только внутри окна, цена — по ряду капитала", () => {
    const eq = parseSeries([["2026-10-08T00:00:00Z", 100], ["2026-10-08T10:00:00Z", 110]]);
    const m = tradeMarkers([
      { ts: "2026-10-08T12:00:00Z", side: "buy", qty: 1, price: 1, reason: "", order_id: "" },
      { ts: "2026-10-08T05:00:00Z", side: "sell", qty: 1, price: 1, reason: "", order_id: "" },
      { ts: "2026-09-01T05:00:00Z", side: "sell", qty: 1, price: 1, reason: "", order_id: "" },
    ], eq, T("2026-10-08T01:00:00Z"));
    expect(m.length).toBe(2);
    expect(m[0]).toMatchObject({ side: "long", p: 110 });
    expect(m[1]).toMatchObject({ side: "short", p: 100 });
    expect(valueAt([], 1)).toBeNull();
  });
  it("шкала цели −1…+1", () => {
    expect(gaugePos(-1)).toBe(0);
    expect(gaugePos(0)).toBe(0.5);
    expect(gaugePos(1)).toBe(1);
    expect(gaugePos(5)).toBe(1);
  });
});
