// «Статистика»: чистая логика (диапазоны, длительности, календарь, гистограмма, сделки/CSV, KPI, сравнение, предупреждения, состояния).
import { describe, expect, it } from "vitest";
import type { StatsKpis, StatsResponse, StatsTradeRow, StatsWarning } from "../src/types";
import golden7 from "./fixtures/stats_7d.json";
import goldenAll from "./fixtures/stats_all.json";
import { spctU } from "../src/ui/fmt";
import { BACKTEST_TAKER, feeNote, FUNDING_HINT, warningChips } from "../src/tabs/statsWarnings";
import { binLabel, binTone, histModel } from "../src/charts/histModel";
import { bucketLabel, bucketTitle, bucketValues } from "../src/tabs/statsBuckets";
import { calendarGrid, calendarIsCompact, calendarList, calendarSections, calLevel } from "../src/charts/calendarModel";
import { cmpView, defText, heroKpis, miniKpis } from "../src/tabs/statsKpi";
import { DEFAULT_STATS_RANGE, isStatsRange, rangeDef, rangeLabel, STATS_RANGES } from "../src/tabs/statsRange";
import { csvText, fillBasisText, filterStTrades, isStTradeFilter, stTradeCounts, stTradesToCsv } from "../src/tabs/statsTradeLogic";
import { equityMapper, tradeMarkers, valueAt } from "../src/tabs/statsMarkers";
import { fmtDuration } from "../src/ui/fmtTime";
import { selectState, trimMessage, unavailableCopy } from "../src/tabs/statsPage";

const g7 = golden7 as unknown as StatsResponse;
const gAll = goldenAll as unknown as StatsResponse;
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

const row = (n: number, over: Partial<StatsTradeRow> = {}): StatsTradeRow => ({
  n, entry_ts: 1_700_000_000 + n * 1000, exit_ts: 1_700_000_500 + n * 1000, side: "long", qty: 0.01, entry_price: 100, exit_price: 101, entry_notional: 1,
  gross: 1, fees: 0.1, funding: 0, funding_complete: true, exact: true, net: 0.9, ret_pct: 0.9, ret_margin_pct: 1.8, equity_at_entry: 100, ret_account_pct: 0.9, hold_s: 500, reason: "бар", reason_group: "бар",
  reconstructed: false, fills: [], ...over,
});

describe("диапазоны", () => {
  it("11 кнопок в порядке 1ч…Всё и ключи API", () => {
    expect(STATS_RANGES.map((r) => r.label)).toEqual(["1ч", "1д", "3д", "1н", "1м", "3м", "6м", "1г", "2г", "3г", "Всё"]);
    expect(STATS_RANGES.map((r) => r.key)).toEqual(["1h", "1d", "3d", "7d", "30d", "90d", "180d", "365d", "730d", "1095d", "all"]);
    expect(DEFAULT_STATS_RANGE).toBe("30d");
  });
  it("валидация и подписи; неизвестное значение — месяц", () => {
    expect(isStatsRange("7d")).toBe(true);
    expect(isStatsRange("2w")).toBe(false);
    expect(isStatsRange(7)).toBe(false);
    expect(rangeLabel("7d")).toBe("1н");
    expect(rangeDef("zzz" as never).key).toBe("30d");
  });
});

describe("длительности и время", () => {
  it("fmtDuration", () => {
    expect(fmtDuration(null)).toBe("—");
    expect(fmtDuration(-5)).toBe("—");
    expect(fmtDuration(0)).toBe("0 с");
    expect(fmtDuration(45)).toBe("45 с");
    expect(fmtDuration(59.6)).toBe("1 мин");
    expect(fmtDuration(60)).toBe("1 мин");
    expect(fmtDuration(3569)).toBe("59 мин");
    expect(fmtDuration(3570)).toBe("1 ч");         // 59,5 мин округляется до часа, а не до «60 мин»
    expect(fmtDuration(3599)).toBe("1 ч");
    expect(fmtDuration(3600)).toBe("1 ч");
    expect(fmtDuration(5 * 3600 + 20 * 60)).toBe("5 ч 20 мин");
    expect(fmtDuration(86400 - 10)).toBe("24 ч");
    expect(fmtDuration(86400)).toBe("1 д");
    expect(fmtDuration(135000)).toBe("1 д 14 ч");
    expect(fmtDuration(3 * 86400 + 4 * 3600)).toBe("3 д 4 ч");
    expect(fmtDuration(NaN)).toBe("—");
  });
});

describe("корзины P/L", () => {
  const t = Date.UTC(2026, 4, 25, 14, 5) / 1000;     // Пн 25.05.2026 14:05 UTC
  it("подписи по виду корзины", () => {
    expect(bucketLabel("5min", t)).toBe("14:05");
    expect(bucketLabel("hour", t)).toBe("14:00");
    expect(bucketLabel("hour", Date.UTC(2026, 4, 25) / 1000)).toBe("25.05");
    expect(bucketLabel("day", t)).toBe("25.05");
    expect(bucketLabel("week", t)).toBe("25.05");
    expect(bucketLabel("month", t)).toBe("Май 26");
  });
  it("полные названия для подсказки", () => {
    expect(bucketTitle("day", t)).toBe("Пн 25.05.26");
    expect(bucketTitle("week", t)).toBe("Неделя с 25.05.26");
    expect(bucketTitle("month", t)).toBe("Май 2026");
    expect(bucketTitle("hour", t)).toBe("25.05.26 14:05 UTC");
  });
  it("пустые корзины — разрыв (null), не нулевой столбец", () => {
    expect(bucketValues([{ t: 1, n: 0, wins: 0, net: 0 }, { t: 2, n: 2, wins: 1, net: -1.5 }, { t: 3, n: 1, wins: 0, net: 0 }])).toEqual([null, -1.5, 0]);
  });
});

describe("календарь: сетка, понедельник первым, месяцы", () => {
  it("границы недель: пн 25.05 – пн 01.06 = 2 колонки, 8 дней", () => {
    const g = calendarGrid("2026-05-25", "2026-06-01", [{ date: "2026-05-26", net: 3.5, trades: 1 }, { date: "2026-06-01", net: -2, trades: 2 }]);
    expect(g.cols).toBe(2);
    const inr = g.days.filter((d) => d.inRange);
    expect(inr).toHaveLength(8);
    expect(inr[0]).toMatchObject({ date: "2026-05-25", row: 0, col: 0 });          // понедельник — строка 0
    expect(inr[6]).toMatchObject({ date: "2026-05-31", row: 6, col: 0 });          // воскресенье — строка 6
    expect(inr[7]).toMatchObject({ date: "2026-06-01", row: 0, col: 1 });          // новая неделя — новая колонка
    expect(g.days.find((d) => d.date === "2026-05-26")).toMatchObject({ net: 3.5, trades: 1 });
    expect(g.days.find((d) => d.date === "2026-06-01")).toMatchObject({ net: -2, trades: 2 });
    expect(g.maxAbs).toBe(3.5);
    expect(g.days.filter((d) => !d.inRange)).toHaveLength(6);                       // хвост последней недели (вт–вс)
  });
  it("начало не в понедельник: недостающие дни слева помечены вне диапазона", () => {
    const g = calendarGrid("2026-05-27", "2026-05-31", []);                          // ср … вс
    expect(g.cols).toBe(1);
    expect(g.days.map((d) => [d.row, d.inRange])).toEqual([[0, false], [1, false], [2, true], [3, true], [4, true], [5, true], [6, true]]);
  });
  it("год и високосный день не ломают сетку (29.02.2028 — вторник)", () => {
    const g = calendarGrid("2028-02-28", "2028-03-01", []);
    expect(g.days.filter((d) => d.inRange).map((d) => [d.date, d.row])).toEqual([["2028-02-28", 0], ["2028-02-29", 1], ["2028-03-01", 2]]);
  });
  it("подписи месяцев: первая колонка + колонка с 1-м числом; близкие не дублируются", () => {
    const g = calendarGrid("2026-09-09", "2026-10-09", []);
    expect(g.months.map((m) => m.label)).toEqual(["Сен", "Окт"]);
    const oct1 = g.days.find((d) => d.date === "2026-10-01")!;
    expect(g.months[1]!.col).toBe(oct1.col);
    // 1-е число уже в первой колонке — она подписана новым месяцем
    const g1 = calendarGrid("2026-09-28", "2026-10-20", []);
    expect(g1.months).toEqual([{ col: 0, label: "Окт" }]);
    // неполный первый месяц (колонка 0) уступает следующему, если тот начался в соседней колонке
    const g2 = calendarGrid("2026-09-21", "2026-10-20", []);
    expect(g2.months.map((m) => m.label)).toEqual(["Окт"]);
    expect(g2.months[0]!.col).toBe(1);
  });
  it("год: 12 подписей, без перекрытий (≥ 3 колонок)", () => {
    const g = calendarGrid("2025-10-10", "2026-10-09", []);
    expect(g.months.length).toBeGreaterThanOrEqual(12);
    for (let i = 1; i < g.months.length; i++) expect(g.months[i]!.col - g.months[i - 1]!.col).toBeGreaterThanOrEqual(3);
  });
  it("некорректные даты и обратный порядок — пустая сетка", () => {
    expect(calendarGrid("bad", "2026-01-01", []).cols).toBe(0);
    expect(calendarGrid("2026-02-01", "2026-01-01", []).days).toEqual([]);
  });
  it("calLevel: знак, порог бледности, нули", () => {
    expect(calLevel(null, 10)).toBe(0);
    expect(calLevel(0, 10)).toBe(0);
    expect(calLevel(5, 0)).toBe(0);
    expect(calLevel(10, 10)).toBe(1);
    expect(calLevel(-10, 10)).toBe(-1);
    expect(calLevel(0.01, 10)).toBe(0.22);
    expect(calLevel(-0.01, 10)).toBe(-0.22);
    expect(calLevel(5, 10)).toBeGreaterThan(calLevel(2, 10));
  });
  it("секции: до ~года — одна; 3 года — по годам (свежий сверху), годы до первой сделки пропущены", () => {
    expect(calendarSections("2026-09-09", "2026-10-09", [])).toHaveLength(1);
    expect(calendarSections("2025-10-10", "2026-10-09", [])[0]!.year).toBeNull();
    const cells = [{ date: "2025-11-05", net: 1, trades: 1 }, { date: "2026-03-01", net: -1, trades: 1 }];
    const s = calendarSections("2023-10-09", "2026-10-09", cells);
    expect(s.map((x) => x.year)).toEqual([2026, 2025]);
    expect(s[0]!.grid.days.find((d) => d.date === "2026-03-01")!.net).toBe(-1);
    expect(s[1]!.grid.days.filter((d) => d.inRange)[0]!.date).toBe("2025-01-01");
    // без сделок — только год окончания (а не пустые простыни за 3 года)
    expect(calendarSections("2023-10-09", "2026-10-09", []).map((x) => x.year)).toEqual([2026]);
  });
  it("окна короче 14 суток — компактный список; новые дни сверху", () => {
    expect(calendarIsCompact("2026-05-25", "2026-06-01")).toBe(true);     // 8 дней
    expect(calendarIsCompact("2026-05-19", "2026-06-01")).toBe(false);    // 14 дней
    expect(calendarIsCompact("2026-06-01", "2026-06-01")).toBe(true);
    const l = calendarList("2026-05-30", "2026-06-01", [{ date: "2026-05-31", net: 2, trades: 1 }]);
    expect(l.map((d) => d.date)).toEqual(["2026-06-01", "2026-05-31", "2026-05-30"]);
    expect(l[1]).toMatchObject({ net: 2, trades: 1 });
    expect(l[0]).toMatchObject({ net: null, trades: 0 });
  });
  it("золотой ответ 7d: сетка по окну окна", () => {
    const w = g7.window!;
    const g = calendarGrid(w.start_date, w.end_date, g7.series!.calendar);
    expect(g.days.filter((d) => d.net != null)).toHaveLength(g7.series!.calendar.length);
  });
});

describe("гистограмма доходностей", () => {
  it("окраска по знаку корзины и подписи", () => {
    expect(binTone(-3, -1)).toBe("neg");
    expect(binTone(-1, 0)).toBe("neg");
    expect(binTone(0, 2)).toBe("pos");
    expect(binTone(-1, 1)).toBe("mixed");
    expect(binLabel(-2.4299, -0.9918)).toBe("−2,4…−1,0%");
    expect(binLabel(0.1, 0.12)).toBe("0,100…0,120%");
    expect(binLabel(1, 3)).toBe("1,0…3,0%");
  });
  it("модель по золотому ответу: корзины, доли, нулевая линия", () => {
    const m = histModel(g7.series!.histogram)!;
    expect(m.bins).toHaveLength(5);
    expect(m.total).toBe(2);
    expect(m.bins[0]).toMatchObject({ tone: "neg", count: 1 });
    expect(m.bins[4]).toMatchObject({ tone: "pos", count: 1 });
    expect(m.bins[2]!.tone).toBe("mixed");                       // корзина −0,99…0,45 содержит 0
    expect(m.zeroPos).toBeGreaterThan(0);
    expect(m.zeroPos).toBeLessThan(1);
    expect(m.bins[0]!.share).toBeCloseTo(0.5);
  });
  it("пусто / битые данные -> null; все значения одинаковы -> одна корзина", () => {
    expect(histModel(undefined)).toBeNull();
    expect(histModel({ edges: [], counts: [] })).toBeNull();
    expect(histModel({ edges: [1, 2], counts: [1, 2] })).toBeNull();
    expect(histModel({ edges: [null, 2], counts: [1] })).toBeNull();
    const one = histModel({ edges: [1.5, 2.5], counts: [3] })!;
    expect(one.bins).toHaveLength(1);
    expect(one.zeroPos).toBeNull();
    expect(one.bins[0]!.tone).toBe("pos");
  });
});

describe("сделки: фильтры, сортировка, CSV", () => {
  const rows = [
    row(1, { net: 2, side: "long" }), row(2, { net: -1, side: "short" }), row(3, { net: 0, side: "long" }),
    row(4, { net: -3, side: "short", reconstructed: true }), row(5, { net: null as never, side: "long" }),
  ];
  it("счётчики и фильтры", () => {
    expect(stTradeCounts(rows)).toEqual({ all: 5, long: 3, short: 2, win: 1, loss: 2, recon: 1 });
    expect(filterStTrades(rows, "all")).toHaveLength(5);
    expect(filterStTrades(rows, "win").map((r) => r.n)).toEqual([1]);
    expect(filterStTrades(rows, "loss").map((r) => r.n)).toEqual([2, 4]);
    expect(filterStTrades(rows, "short").map((r) => r.n)).toEqual([2, 4]);
    expect(filterStTrades(rows, "recon").map((r) => r.n)).toEqual([4]);
    expect(filterStTrades(rows, "all")).not.toBe(rows);
    expect(isStTradeFilter("recon")).toBe(true);
    expect(isStTradeFilter("x")).toBe(false);
  });
  it("csvText: кавычки, запятые, переводы строк, защита от формул", () => {
    expect(csvText("бар")).toBe("бар");
    expect(csvText("старт, с опозданием 12 мин")).toBe('"старт, с опозданием 12 мин"');
    expect(csvText('a"b')).toBe('"a""b"');
    expect(csvText("=1+1")).toBe("'=1+1");
    expect(csvText("@cmd")).toBe("'@cmd");
    expect(csvText("-5")).toBe("'-5");
    expect(csvText(null)).toBe("");
    expect(csvText("a\nb")).toBe('"a\nb"');
  });
  it("CSV: заголовок, ISO UTC, числа с точкой, CRLF, пустые для null", () => {
    const csv = stTradesToCsv([row(7, { reason: "закрытие вручную, мгновенно", net: null as never, reconstructed: true, funding_complete: false })]);
    const lines = csv.split("\r\n");
    expect(lines[0]).toBe("n,entry_utc,exit_utc,side,qty,entry_price,exit_price,gross,fees_est,funding_est,net,ret_pct,ret_margin_pct,hold_s,reason,reconstructed,funding_complete,equity_at_entry,ret_account_pct");
    expect(lines[1]).toMatch(/^7,2023-11-15T00:10:00Z,2023-11-15T00:18:20Z,long,0\.01,100,101,1,0\.1,0,,0\.9,1\.8,500,"закрытие вручную, мгновенно",1,0,100,0\.9$/);
    expect(lines[2]).toBe("");
    expect(csv.endsWith("\r\n")).toBe(true);
    expect(stTradesToCsv([])).toBe(lines[0] + "\r\n");
  });
  it("CSV: текстовые поля (сторона, причина) защищены от формул, числа с минусом — нет", () => {
    const csv = stTradesToCsv([row(1, { side: "=cmd|' /C calc'!A0" as never, reason: "+1+1", net: -2.5, gross: -1 })]);
    const line = csv.split("\r\n")[1]!;
    expect(line).toContain(",'=cmd|' /C calc'!A0,");
    expect(line).toContain(",'+1+1,");
    expect(line).toContain(",-1,0.1,0,-2.5,");   // отрицательные числа остаются числами
  });
  it("золотой ответ -> CSV с тем же числом строк", () => {
    const r = gAll.trades!.rows;
    expect(stTradesToCsv(r).trim().split("\r\n")).toHaveLength(r.length + 1);
  });
  it("basis fill", () => {
    expect(fillBasisText("stop_level")).toBe("по стоп-уровню");
    expect(fillBasisText(undefined)).toBe("");
    expect(fillBasisText("new_basis")).toBe("new_basis");
  });
});

describe("маркеры сделок и единицы графика", () => {
  const t = [100, 200, 300, 400];
  const v = [10, 20, null, 40];
  it("valueAt: последняя точка ≤ t, пропуски null обходятся", () => {
    expect(valueAt(t, v, 50)).toBe(10);
    expect(valueAt(t, v, 100)).toBe(10);
    expect(valueAt(t, v, 250)).toBe(20);
    expect(valueAt(t, v, 300)).toBe(20);
    expect(valueAt(t, v, 999)).toBe(40);
    expect(valueAt([], [], 1)).toBeNull();
  });
  it("маркеры внутри окна; вне окна пропущены; выбранная сделка отмечена active", () => {
    const rs = [row(1, { entry_ts: 150, exit_ts: 250 }), row(2, { entry_ts: 10, exit_ts: 20 }), row(3, { entry_ts: 350, exit_ts: 390, side: "short" })];
    const m = tradeMarkers(rs, t, v, 100, 400, 3);
    expect(m.map((x) => [x.kind, x.t, x.p, !!x.active])).toEqual([["entry", 150, 10, false], ["exit", 250, 20, false], ["entry", 350, 20, true], ["exit", 390, 20, true]]);
    expect(m[2]!.side).toBe("short");
  });
  it("> 150 сделок: показывается только выбранная", () => {
    const many = Array.from({ length: 151 }, (_, i) => row(i + 1, { entry_ts: 150, exit_ts: 250 }));
    expect(tradeMarkers(many, t, v, 100, 400, null)).toHaveLength(0);
    expect(tradeMarkers(many, t, v, 100, 400, 7)).toHaveLength(2);
    const few = many.slice(0, 150);
    expect(tradeMarkers(few, t, v, 100, 400, null)).toHaveLength(300);
  });
  it("перевод единиц: USDT как есть; «% к старту окна» = (v − v₀) / v₀ (на конце окна совпадает с ROI счёта)", () => {
    expect(equityMapper("usdt", 160)(170)).toBe(170);
    expect(equityMapper("pct", 160)(168)).toBeCloseTo(5);               // (168 − 160) / 160 · 100
    expect(equityMapper("pct", 160)(160)).toBeCloseTo(0);
    expect(equityMapper("pct", 200)(190)).toBeCloseTo(-5);
    expect(equityMapper("pct", null)(170)).toBe(170);                    // нет стартового значения — без пересчёта
    expect(equityMapper("pct", 0)(170)).toBe(170);                       // нулевая/отрицательная база — без деления на ноль
    expect(equityMapper("pct", -5)(170)).toBe(170);
  });
});

describe("KPI: плитки и обработка null", () => {
  it("золотой ответ 7d: 8 плиток с ожидаемыми значениями", () => {
    const k = heroKpis(g7);
    expect(k.map((x) => x.key)).toEqual(["roi", "win", "pf", "dd", "sharpe", "trades", "costs", "vsbh"]);
    const by = Object.fromEntries(k.map((x) => [x.key, x]));
    expect(by.roi!.value).toMatch(/^\+?\d+,\d{2}%$/);
    expect(by.roi!.cmp?.field).toBe("wallet_return_pct");
    expect(by.roi!.label).toBe("ROI счёта");
    expect(by.roi!.def).toBe("wallet_return_pct");
    expect(by.roi!.value).toBe(spctU(g7.equity!.wallet_return_pct, 2));
    expect(by.win!.value).toBe("50,0%");
    expect(by.trades!.value).toBe("2");
    expect(by.trades!.sub).toBe("лонг 1 · шорт 1");
    expect(by.sharpe!.value).toBe("—");
    expect(by.sharpe!.why).toContain("14");
    expect(by.vsbh!.value).toMatch(/п\.п\.$/);
    expect(by.costs!.est).toBe(true);
  });
  it("Sharpe null -> «—» с причиной; число -> значение без причины", () => {
    const r = clone(g7);
    r.equity!.sharpe = null;
    expect(heroKpis(r).find((x) => x.key === "sharpe")).toMatchObject({ value: "—", why: expect.stringContaining("14") });
    r.equity!.sharpe = 1.234;
    expect(heroKpis(r).find((x) => x.key === "sharpe")).toMatchObject({ value: "1,23", tone: "pos", why: undefined });
  });
  it("Profit factor: null без убытков -> «∞», без сделок -> «—»", () => {
    const r = clone(g7);
    r.kpis!.profit_factor = null; r.kpis!.losses = 0; r.kpis!.wins = 3; r.kpis!.trades = 3;
    expect(heroKpis(r).find((x) => x.key === "pf")!.value).toBe("∞");
    r.kpis!.wins = 0; r.kpis!.trades = 0;
    const pf = heroKpis(r).find((x) => x.key === "pf")!;
    expect(pf.value).toBe("—");
    expect(pf.why).toBeTruthy();
  });
  it("Win rate без сделок — «—»; vs B&H без свечей — «—» с причиной", () => {
    const r = clone(g7);
    r.kpis!.trades = 0; r.kpis!.wins = 0; r.kpis!.losses = 0;
    expect(heroKpis(r).find((x) => x.key === "win")!.value).toBe("—");
    r.kpis!.buy_hold_pct = null;
    const v = heroKpis(r).find((x) => x.key === "vsbh")!;
    expect(v.value).toBe("—");
    expect(v.why).toContain("свеч");
  });
  it("vs B&H = ROI − buy&hold (п.п.)", () => {
    const r = clone(g7);
    r.equity!.wallet_return_pct = 5; r.kpis!.buy_hold_pct = 2;
    expect(heroKpis(r).find((x) => x.key === "vsbh")!.value).toBe("+3,00 п.п.");
    r.kpis!.buy_hold_pct = 9;
    expect(heroKpis(r).find((x) => x.key === "vsbh")).toMatchObject({ value: "−4,00 п.п.", tone: "neg" });
  });
  it("комиссии + фандинг: сумма и обе части (фандинг может быть отрицательным)", () => {
    const r = clone(g7);
    r.kpis!.fees_total = 2.5; r.kpis!.funding_total = -0.5;
    const c = heroKpis(r).find((x) => x.key === "costs")!;
    expect(c.value).toBe("2,00 $");
    expect(c.sub).toContain("комиссии 2,50 $");
    expect(c.sub).toContain("фандинг −0,50 $");
  });
  it("недоступный ответ — пустые списки", () => {
    const u = { available: false, range: "30d", computed_at: 1, warnings: [], definitions: {} } as StatsResponse;
    expect(heroKpis(u)).toEqual([]);
    expect(miniKpis(u)).toEqual([]);
  });
  it("«Все метрики»: 20 плиток (без дубля ROI счёта); Sortino/Calmar null — с причиной; время — человеческое", () => {
    const m = miniKpis(gAll);
    expect(m).toHaveLength(20);
    expect(m.some((x) => x.def === "wallet_return_pct")).toBe(false);          // доходность кошелька = заголовочная плитка «ROI счёта»
    const by = Object.fromEntries(m.map((x) => [x.key, x]));
    expect(by.hold_mean!.value).toMatch(/^\d+ д( \d+ ч)?$/);
    expect(by.streaks!.value).toBe("1 / 3");   // золотая БД: сделка №7 (стоп) теперь по данным биржи — убыток, серия убытков 6-7-8
    expect(by.best!.sub).toMatch(/^#9 · \+3,32%$/);
    const r = clone(gAll);
    r.equity!.sortino = null; r.equity!.calmar = null; r.kpis!.best_trade = null; r.kpis!.hold_mean_s = null;
    const m2 = Object.fromEntries(miniKpis(r).map((x) => [x.key, x]));
    expect(m2.sortino).toMatchObject({ value: "—", why: expect.any(String) });
    expect(m2.calmar!.why).toContain("30");
    expect(m2.best!.value).toBe("—");
    expect(m2.hold_mean!.value).toBe("—");
  });
  it("определения: сервер приоритетнее, потом локальные, иначе пусто", () => {
    expect(defText({ sharpe: "S" }, "sharpe")).toBe("S");
    expect(defText({}, "avg_win")).toContain("Средний Net");
    expect(defText({}, "nope")).toBe("");
    expect(defText(undefined, "nope")).toBe("");
    for (const k of miniKpis(gAll).map((x) => x.def).concat(heroKpis(gAll).map((x) => x.def))) expect(defText(gAll.definitions, k), k).not.toBe("");
  });
});

describe("сравнение с предыдущим окном", () => {
  it("доступно: направление, цвет по «лучше/хуже», подсказка с прежним значением", () => {
    const r = clone(g7);
    r.comparison = { available: true, prev_start: 1_000_000, prev_end: 1_600_000, kpis: { win_rate_pct: { cur: 50, prev: 40, delta: 10 }, fees_total: { cur: 3, prev: 1, delta: 2 }, trades: { cur: 5, prev: 8, delta: -3 } },
      equity: { wallet_return_pct: { cur: 1, prev: 3, delta: -2 }, max_drawdown_pct: { cur: -2, prev: -5, delta: 3 } } };
    const c = (k: string) => heroKpis(r).find((x) => x.key === k)!.cmp;
    expect(cmpView(r, c("win"))).toMatchObject({ kind: "chip", dir: "up", good: "pos", text: "+10,0 п.п." });
    expect(cmpView(r, c("roi"))).toMatchObject({ dir: "down", good: "neg", text: "−2,0 п.п." });
    expect(cmpView(r, c("dd"))).toMatchObject({ dir: "up", good: "pos" });          // просадка стала мельче — это хорошо
    expect(cmpView(r, c("costs"))).toMatchObject({ dir: "up", good: "", text: "+2,00 $" });   // комиссии — нейтрально
    expect(cmpView(r, c("trades"))).toMatchObject({ dir: "down", good: "", text: "−3" });
    const v = cmpView(r, c("win")) as { title: string };
    expect(v.title).toContain("40,00%");
    expect(v.title).toContain("50,00%");
  });
  it("нет изменения -> «без изменений», нейтрально", () => {
    const r = clone(g7);
    r.comparison = { available: true, kpis: { win_rate_pct: { cur: 50, prev: 50, delta: 0.001 } }, equity: {} };
    expect(cmpView(r, heroKpis(r)[1]!.cmp)).toMatchObject({ dir: "flat", good: "", text: "без изменений" });
  });
  it("недоступно: причина в подсказке; delta null — отдельная причина; без спецификации — null", () => {
    const spec = heroKpis(g7)[0]!.cmp;
    const r = clone(g7);
    r.comparison = { available: false, reason: "range_too_short" };
    expect(cmpView(r, spec)).toMatchObject({ kind: "na", title: expect.stringContaining("от диапазона") });
    r.comparison = { available: false, reason: "all_range" };
    expect((cmpView(r, spec) as { title: string }).title).toContain("«Всё»");
    r.comparison = { available: false, reason: "no_previous_data" };
    expect((cmpView(r, spec) as { title: string }).title).toContain("Нет данных");
    r.comparison = { available: true, equity: { wallet_return_pct: { cur: 1, prev: null, delta: null } }, kpis: {} };
    expect(cmpView(r, spec)).toMatchObject({ kind: "na" });
    expect(cmpView(r, undefined)).toBeNull();
    delete (r as { comparison?: unknown }).comparison;
    expect(cmpView(r, spec)).toMatchObject({ kind: "na" });
  });
});

describe("предупреждения -> чипы", () => {
  const w = (code: string, level: "warn" | "info", extra: Partial<StatsWarning> = {}): StatsWarning => ({ code: code as never, level, text: `t:${code}`, ...extra });
  it("порядок: warn раньше info; подписи со счётчиками", () => {
    const c = warningChips([w("equity_gaps", "info", { count: 3 }), w("few_trades", "warn", { count: 7 }), w("reconstructed_trades", "warn", { count: 2 })]);
    expect(c.map((x) => x.label)).toEqual(["Мало сделок (7)", "Реконструкция: 2", "Пропуски капитала: 3"]);
    expect(c.map((x) => x.level)).toEqual(["warn", "warn", "info"]);
    expect(c.every((x) => !!x.hint)).toBe(true);
  });
  it("few_trades объясняет ненадёжность", () => {
    expect(warningChips([w("few_trades", "warn", { count: 3 })])[0]!.hint).toContain("случайны");
  });
  it("funding_stale: свой текст (без «вкладки Данные») и подсказка про «Подгрузить новые свечи»", () => {
    const c = warningChips([w("funding_stale", "warn", { trades: 4, funding_coverage_until: 1_780_000_000, text: "обновите данные на вкладке «Данные»" })])[0]!;
    expect(c.label).toBe("Фандинг устарел");
    expect(c.text).not.toContain("вкладке «Данные»");
    expect(c.text).toContain("4 сделок");
    expect(c.hint).toBe(FUNDING_HINT);
    expect(c.hint).toContain("Подгрузить новые свечи");
    expect(warningChips([w("funding_stale", "warn", { funding_coverage_until: null })])[0]!.text).toContain("нет данных");
  });
  it("неизвестный код и пустой список; серверный текст сохраняется", () => {
    const c = warningChips([w("brand_new" as never, "warn")])[0]!;
    expect(c).toMatchObject({ label: "brand_new", text: "t:brand_new" });
    expect(c.hint).toBeUndefined();
    expect(warningChips([])).toEqual([]);
    expect(warningChips(undefined)).toEqual([]);
    expect(warningChips([w("bad_fills", "warn")])[0]!.text).toBe("t:bad_fills");
  });
  it("золотые ответы разбираются без исключений", () => {
    expect(warningChips(g7.warnings).map((x) => x.code)).toEqual(["few_trades"]);
    expect(warningChips(gAll.warnings).map((x) => x.code)).toEqual(["reconstructed_trades", "equity_gaps"]);
  });
});

describe("выбор состояния страницы", () => {
  const ok = g7;
  it("сеть/сервер -> ошибка с кодом", () => {
    expect(selectState({ res: null, err: { status: 0, code: "network", message: "сервер недоступен" } })).toEqual({ kind: "error", message: "сервер недоступен", code: "network" });
    expect(selectState({ res: null, err: { status: 500, code: "http", message: "HTTP 500" } }).kind).toBe("error");
  });
  it("нет ответа -> загрузка; доступный -> ready; недоступный -> причина и сообщение", () => {
    expect(selectState({ res: null, err: null })).toEqual({ kind: "loading" });
    expect(selectState({ res: ok, err: null })).toEqual({ kind: "ready" });
    const u = golden7 as unknown as StatsResponse;
    expect(u.available).toBe(true);
    const un = { available: false, reason: "no_trades", message: "нет", range: "30d", computed_at: 1, warnings: [], definitions: {} } as StatsResponse;
    expect(selectState({ res: un, err: null })).toEqual({ kind: "unavailable", reason: "no_trades", message: "нет" });
    expect(selectState({ res: { ...un, reason: undefined, message: undefined }, err: null })).toEqual({ kind: "unavailable", reason: "error", message: "" });
  });
  it("тексты недоступности: все коды с заголовком и подсказкой, неизвестный — как error", () => {
    for (const r of ["no_db", "no_trades", "no_equity", "db_error", "error"]) {
      const c = unavailableCopy(r);
      expect(c.title.length).toBeGreaterThan(5);
      expect(c.hint.length).toBeGreaterThan(20);
    }
    expect(unavailableCopy("zzz")).toEqual(unavailableCopy("error"));
  });
  it("trimMessage: не повторяет заголовок", () => {
    expect(trimMessage("База бота не найдена", "База бота не найдена: бот ещё не запускался.")).toBe("Бот ещё не запускался.");
    expect(trimMessage("Заголовок", "Заголовок")).toBe("");
    expect(trimMessage("Заголовок", "Другое сообщение")).toBe("Другое сообщение");
    expect(trimMessage("X", "")).toBe("");
  });
});

describe("комиссия счёта против бэктестов", () => {
  it("0,15% против 0,10% — заметка; совпадает с бэктестом (0,10%) / нет данных — null", () => {
    expect(BACKTEST_TAKER).toBe(0.001);
    expect(feeNote(0.0015)).toBe("Реальная комиссия счёта 0,15% против 0,10% в бэктестах — результаты стратегий на сайте оптимистичнее");
    expect(feeNote(0.001)).toBeNull();
    expect(feeNote(BACKTEST_TAKER)).toBeNull();
    expect(feeNote(null)).toBeNull();
    expect(feeNote(undefined)).toBeNull();
    expect(feeNote(0.0006)).toContain("0,060%");
  });
});

describe("типы ответа соответствуют золотым данным", () => {
  it("ключи KPI и equity присутствуют", () => {
    const k = gAll.kpis as StatsKpis;
    for (const key of ["trades", "wins", "losses", "win_rate_pct", "profit_factor", "net_pnl", "fees_total", "funding_total", "buy_hold_pct"]) expect(key in k, key).toBe(true);
    expect(gAll.equity!.wallet_return_pct).toBeTypeOf("number");
    expect(rangeLabel(gAll.range)).toBe("Всё");
  });
});
