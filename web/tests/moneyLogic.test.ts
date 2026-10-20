// Деньги по закрытым сделкам: сводки (заработано / потеряно / итого), строка по отфильтрованным строкам, итоги окна, результат в $ и % счёта,
// новые закрытые сделки между опросами, общий кэш /api/stats (вкладки «Статистика» и «Бот (live)»).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StatsResponse, StatsTradeRow } from "../src/types";
import { closedKey, newClosedKeys, upnlAccountFrac } from "../src/tabs/liveCalc";
import { filterStTrades, stTradesToCsv, ST_CSV_HEADER } from "../src/tabs/statsTradeLogic";
import { moneyLine, moneySummary, openAccountPct, periodSummary, resultText, resultTone } from "../src/tabs/statsMoney";
import { freshStats, loadStats, rememberStats, resetStatsFeed } from "../src/tabs/statsFeed";
import golden from "./fixtures/stats_all.json";

const g = golden as unknown as StatsResponse;
const row = (n: number, net: number | null, over: Partial<StatsTradeRow> = {}): StatsTradeRow => ({
  n, entry_ts: 1_700_000_000 + n * 1000, exit_ts: 1_700_000_500 + n * 1000, side: "long", qty: 0.01, entry_price: 100, exit_price: 101, entry_notional: 100,
  gross: (net ?? 0) + 0.2, fees: 0.2, funding: 0, funding_complete: true, exact: true, net, ret_pct: net, ret_margin_pct: net, equity_at_entry: 1000, ret_account_pct: net == null ? null : net / 10,
  hold_s: 500, reason: "бар", reason_group: "бар", reconstructed: false, fills: [], ...over,
});

describe("сводка по закрытым сделкам", () => {
  const rows = [row(1, 12.5), row(2, -4.25, { side: "short" }), row(3, 6, { side: "short" }), row(4, -1.5), row(5, 0), row(6, null)];

  it("заработано = Σ net > 0, потеряно = Σ net < 0 (число ≤ 0), итого = Σ net; ноль и null не считаются ни выигрышем, ни проигрышем", () => {
    const s = moneySummary(rows);
    expect(s).toEqual({ count: 6, wins: 2, losses: 2, profit: 18.5, loss: -5.75, net: 12.75 });
    expect(s.profit + s.loss).toBeCloseTo(s.net, 9);
  });

  it("строка: «Закрыто N: заработано +A $, потеряно −B $, итого ±C $» с типографским минусом", () => {
    const t = moneyLine(moneySummary(rows)).replace(/\s/g, " ");
    expect(t).toBe("Закрыто 6: заработано +18,50 $, потеряно −5,75 $, итого +12,75 $");
    const loss = moneyLine(moneySummary([row(1, -3), row(2, 1)])).replace(/\s/g, " ");
    expect(loss).toBe("Закрыто 2: заработано +1,00 $, потеряно −3,00 $, итого −2,00 $");
  });

  it("без сделок — «Закрытых сделок нет»; без убытков потеряно = 0,00 $ (без плюса)", () => {
    expect(moneyLine(moneySummary([]))).toBe("Закрытых сделок нет");
    expect(moneyLine(moneySummary([row(1, 2)])).replace(/\s/g, " ")).toBe("Закрыто 1: заработано +2,00 $, потеряно 0,00 $, итого +2,00 $");
  });

  it("сводка считается по ОТФИЛЬТРОВАННЫМ строкам (лонг / шорт / прибыльные / убыточные)", () => {
    const sh = moneySummary(filterStTrades(rows, "short"));
    expect(sh).toMatchObject({ count: 2, profit: 6, loss: -4.25, net: 1.75 });
    expect(moneySummary(filterStTrades(rows, "loss"))).toMatchObject({ count: 2, profit: 0, loss: -5.75, net: -5.75 });
    expect(moneySummary(filterStTrades(rows, "win"))).toMatchObject({ count: 2, profit: 18.5, loss: 0 });
  });

  it("золотой ответ: сумма по строкам совпадает с kpis.net_pnl и profit_sum_usdt + loss_sum_usdt", () => {
    const s = moneySummary(g.trades!.rows);
    expect(s.net).toBeCloseTo(g.kpis!.net_pnl!, 3);
    expect(s.profit).toBeCloseTo(g.kpis!.profit_sum_usdt!, 3);
    expect(s.loss).toBeCloseTo(g.kpis!.loss_sum_usdt!, 3);
  });
});

describe("итоги окна (шапка блока «Закрытые сделки»)", () => {
  it("берутся из kpis по ВСЕМ сделкам окна, а не из обрезанных строк", () => {
    const r = JSON.parse(JSON.stringify(g)) as StatsResponse;
    r.trades = { total: 900, truncated: true, rows: r.trades!.rows.slice(0, 2) };
    r.kpis = { ...r.kpis!, trades: 900, wins: 500, losses: 400, profit_sum_usdt: 1234.5, loss_sum_usdt: -234.5, net_pnl: 1000 };
    expect(periodSummary(r)).toEqual({ count: 900, wins: 500, losses: 400, profit: 1234.5, loss: -234.5, net: 1000 });
  });

  it("нет полей суммы в kpis: null (по строкам, в том числе обрезанным, не выдумываем)", () => {
    const r = JSON.parse(JSON.stringify(g)) as StatsResponse;
    delete (r.kpis as unknown as Record<string, unknown>).profit_sum_usdt;
    expect(periodSummary(r)).toBeNull();
    expect(periodSummary({ available: false } as StatsResponse)).toBeNull();
    expect(periodSummary(null)).toBeNull();
  });
});

describe("результат сделки: доллары и % счёта вместе", () => {
  it("знак и цвет по P/L; процент рядом; нет кошелька на входе — «% счёта: —»", () => {
    const norm = (s: string) => s.replace(/\s/g, " ");
    expect(resultText(12.5, 1.25)).toMatchObject({ tone: "pos" });
    expect(norm(resultText(12.5, 1.25).usd)).toBe("+12,50 $");
    expect(norm(resultText(12.5, 1.25).pct)).toBe("+1,25% счёта");
    expect(norm(resultText(-4.25, -0.43).pct)).toBe("−0,43% счёта");
    expect(resultText(-4.25, -0.43).tone).toBe("neg");
    expect(resultText(5, null).pct).toBe("% счёта: —");
    expect(resultText(0, 0).tone).toBe("");
  });

  it("цвет результата зависит только от net, а не от стороны сделки", () => {
    for (const side of ["long", "short"] as const) {
      expect(resultTone(row(1, 3, { side }))).toBe("pos");
      expect(resultTone(row(1, -3, { side }))).toBe("neg");
      expect(resultTone(row(1, 0.001, { side }))).toBe("");
    }
  });

  it("нереализованный результат в % кошелька: upnl / кошелёк; нет данных — null", () => {
    expect(openAccountPct(7.5, 1000)).toBeCloseTo(0.75, 9);
    expect(openAccountPct(-3, 600)).toBeCloseTo(-0.5, 9);
    expect(openAccountPct(1, 0)).toBeNull();
    expect(openAccountPct(null, 100)).toBeNull();
    expect(upnlAccountFrac(7.5, 1000)).toBeCloseTo(0.0075, 9);
    expect(upnlAccountFrac(undefined, 1000)).toBeNull();
  });

  it("CSV: колонки equity_at_entry и ret_account_pct в конце, пустые для null", () => {
    expect(ST_CSV_HEADER.slice(-2)).toEqual(["equity_at_entry", "ret_account_pct"]);
    const [head, a, b] = stTradesToCsv([row(1, 5), row(2, 5, { equity_at_entry: null, ret_account_pct: null })]).split("\r\n");
    expect(head!.endsWith(",equity_at_entry,ret_account_pct")).toBe(true);
    expect(a!.endsWith(",1000,0.5")).toBe(true);
    expect(b!.endsWith(",,")).toBe(true);
  });
});

describe("новые закрытые сделки между опросами", () => {
  const a = row(1, 1), b = row(2, 2), c = row(3, 3);
  it("первый опрос (prev = null) ничего не подсвечивает; потом — только ключи, которых не было", () => {
    expect(newClosedKeys(null, [a, b])).toEqual([]);
    const known = new Set([a, b].map(closedKey));
    expect(newClosedKeys(known, [a, b])).toEqual([]);
    expect(newClosedKeys(known, [a, b, c])).toEqual([closedKey(c)]);
    expect(newClosedKeys(new Set(), [a])).toEqual([closedKey(a)]);
  });
  it("ключ — вход+выход: не зависит от номера n", () => {
    expect(closedKey(a)).toBe(closedKey({ entry_ts: a.entry_ts, exit_ts: a.exit_ts }));
    expect(closedKey(a)).not.toBe(closedKey(b));
  });
});

describe("общий кэш /api/stats", () => {
  const resp = (range: StatsResponse["range"], tag: number): StatsResponse => ({ ...(g as StatsResponse), range, computed_at: tag });
  beforeEach(() => { resetStatsFeed(); vi.useFakeTimers(); vi.setSystemTime(1_000_000); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("свежий ответ отдаётся из кэша; старше maxAge — нет; диапазоны независимы", () => {
    rememberStats(resp("30d", 1), 1_000_000 - 10_000);
    expect(freshStats("30d", 30_000)?.computed_at).toBe(1);
    expect(freshStats("30d", 5_000)).toBeNull();
    expect(freshStats("7d", 30_000)).toBeNull();
  });

  it("loadStats: параллельные вызовы делят один запрос; следующий вызов в пределах maxAge — без сети; ошибка пробрасывается как ApiError", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify(resp("30d", 7)), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", f);
    const [x, y] = await Promise.all([loadStats("30d"), loadStats("30d")]);
    expect(x).toBe(y);
    expect(f).toHaveBeenCalledTimes(1);
    await loadStats("30d", { maxAgeMs: 30_000 });
    expect(f).toHaveBeenCalledTimes(1);
    vi.setSystemTime(1_000_000 + 31_000);
    await loadStats("30d", { maxAgeMs: 30_000 });
    expect(f).toHaveBeenCalledTimes(2);

    resetStatsFeed();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 })));
    await expect(loadStats("30d")).rejects.toMatchObject({ status: 404 });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(resp("30d", 8)), { status: 200 })));
    expect((await loadStats("30d")).computed_at).toBe(8);                  // после ошибки запрос не «залипает»
  });
});
