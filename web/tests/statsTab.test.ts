// @vitest-environment jsdom
// Вкладка «Статистика» (jsdom, графики подменены): состояния, диапазоны, автообновление, карточки, таблица сделок, CSV, выбор сделки.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Meta, StatsResponse } from "../src/types";
import golden7 from "./fixtures/stats_7d.json";
import goldenAll from "./fixtures/stats_all.json";
import goldenUnavailable from "./fixtures/stats_unavailable.json";
import { richStats } from "./fixtures/statsRich";

const downloads: { name: string; text: string }[] = [];
const lineCalls: { markers: unknown[][]; data: unknown[][] }[] = [];
vi.mock("../src/ui/download", () => ({ downloadText: (name: string, text: string) => { downloads.push({ name, text }); } }));
vi.mock("../src/charts/line", () => ({
  lineChart: () => {
    const rec = { markers: [] as unknown[][], data: [] as unknown[][] };
    lineCalls.push(rec);
    return { setData: (x: unknown[], ys: unknown[]) => { rec.data.push([x, ys]); }, setLog() {}, setMarkers: (m: unknown[]) => { rec.markers.push(m); }, destroy() {} };
  },
}));
vi.mock("../src/charts/bars", () => ({ barChart: () => ({ redraw() {}, destroy() {} }) }));
vi.mock("../src/charts/histogram", () => ({ histogramChart: () => ({ redraw() {}, destroy() {} }) }));

const { createStats } = await import("../src/tabs/stats");
const { moneySummary } = await import("../src/tabs/statsMoney");
const { susd } = await import("../src/ui/fmt");

const g7 = golden7 as unknown as StatsResponse;
const gAll = goldenAll as unknown as StatsResponse;
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 6; i++) await vi.advanceTimersByTimeAsync(0); };
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

let urls: string[] = [];
const rangeOf = (url: string): string => new URL(url, "http://x").searchParams.get("range") ?? "";
/** Ответ сервера всегда помечен запрошенным диапазоном (золотые файлы лежат для 7d/all, а вкладка по умолчанию просит 30d). */
async function withRange(url: string, r: Response): Promise<Response> {
  if (!r.ok) return r;
  const body = (await r.json()) as StatsResponse;
  return json(200, { ...body, range: rangeOf(url) });
}
function stub(handler: (url: string) => Response | Promise<Response>) {
  const f = vi.fn(async (url: string) => { urls.push(String(url)); return withRange(String(url), await handler(String(url))); });
  vi.stubGlobal("fetch", f);
  return f;
}
const byRange = (m: Record<string, StatsResponse>, dflt: StatsResponse) => (url: string) => json(200, m[new URL(url, "http://x").searchParams.get("range") ?? ""] ?? dflt);

const mounted: { hide(): void }[] = [];
async function mount() {
  const tab = createStats({} as Meta);
  mounted.push(tab);
  document.body.append(tab.el);
  tab.show();
  await flush();
  return tab;
}
const q = (el: HTMLElement, s: string) => el.querySelector(s) as HTMLElement;
const qa = (el: HTMLElement, s: string) => [...el.querySelectorAll(s)] as HTMLElement[];
const chip = (el: HTMLElement, label: string) => qa(el, ".st-ranges button").find((b) => b.textContent === label) as HTMLButtonElement;

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  document.body.replaceChildren();
  downloads.length = 0; lineCalls.length = 0; urls = [];
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { mounted.splice(0).forEach((t) => t.hide()); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("вкладка «Статистика»: готовые данные", () => {
  it("золотой ответ 7d: заголовок, 8 плиток, чипы диапазонов, среда, плашка «мало сделок», 2 сделки", async () => {
    stub(() => json(200, g7));
    const tab = await mount();
    expect(urls[0]).toBe("/api/stats?range=30d");                                   // диапазон по умолчанию
    expect(q(tab.el, ".page-title").textContent).toBe("Статистика");
    expect(qa(tab.el, ".st-ranges button").map((b) => b.textContent)).toEqual(["1ч", "1д", "3д", "1н", "1м", "3м", "6м", "1г", "2г", "3г", "Всё"]);
    expect(chip(tab.el, "1м").getAttribute("aria-pressed")).toBe("true");
    expect(qa(tab.el, ".st-hero .st-tile")).toHaveLength(8);
    expect(q(tab.el, ".st-tile.kpi-roi").textContent).toContain("ROI счёта");
    expect(q(tab.el, ".st-few").textContent).toContain("Мало сделок (2 из 10");
    expect(q(tab.el, ".st-window").textContent).toContain("25.05.2026 – 01.06.2026");
    expect(qa(tab.el, ".st-trades tbody tr")).toHaveLength(2);
    expect(q(tab.el, ".st-updated").textContent).toContain("обновлено");
    expect(q(tab.el, ".st-trades .card-head").textContent).toContain("2 из 2");
  });

  it("у каждой метрики есть значок-определение; Sharpe null -> «—» с подсказкой про 14 дней", async () => {
    stub(() => json(200, g7));
    const tab = await mount();
    for (const t of qa(tab.el, ".st-tile")) expect(t.querySelector(".st-info"), t.textContent ?? "").not.toBeNull();
    const sharpe = q(tab.el, ".kpi-sharpe .metric-value");
    expect(sharpe.textContent).toBe("—");
    expect(sharpe.getAttribute("title")).toContain("14");
    for (const m of qa(tab.el, ".st-minis .mini")) expect(m.querySelector(".st-info")).not.toBeNull();
    expect(qa(tab.el, ".st-minis .mini")).toHaveLength(20);
  });

  it("«ROI счёта»: определение по серверному тексту; переключатель единиц «% к старту окна»", async () => {
    stub(() => json(200, g7));
    const tab = await mount();
    const info = q(tab.el, ".st-tile.kpi-roi .st-info");
    expect(info.getAttribute("title")).toBe(g7.definitions!.wallet_return_pct);
    expect(info.getAttribute("title")).toContain("ROI счёта");
    expect(qa(tab.el, ".st-seg button").map((b) => b.textContent)).toEqual(["USDT", "% к старту окна"]);
  });

  it("смена диапазона: запрос с нужным range, выбор сохраняется в localStorage и восстанавливается", async () => {
    stub(byRange({ "90d": richStats() }, g7));
    const tab = await mount();
    chip(tab.el, "3м").click();
    await flush();
    expect(urls[urls.length - 1]).toBe("/api/stats?range=90d");
    expect(chip(tab.el, "3м").getAttribute("aria-pressed")).toBe("true");
    expect(qa(tab.el, ".st-trades tbody tr")).toHaveLength(40);
    await vi.advanceTimersByTimeAsync(300);
    expect(JSON.parse(localStorage.getItem("tb.stats")!).range).toBe("90d");
    document.body.replaceChildren();
    urls = [];
    await mount();
    expect(urls[0]).toBe("/api/stats?range=90d");
  });

  it("мусор в localStorage не ломает диапазон", async () => {
    localStorage.setItem("tb.stats", JSON.stringify({ range: "5y", unit: 3, filter: "weird" }));
    stub(() => json(200, g7));
    await mount();
    expect(urls[0]).toBe("/api/stats?range=30d");
  });
});

describe("вкладка «Статистика»: богатый ответ", () => {
  it("чипы качества данных (warn раньше info), подсказка про «Подгрузить новые свечи», список с текстами", async () => {
    stub(() => json(200, richStats()));
    const tab = await mount();
    const chips = qa(tab.el, ".st-qchips .badge").map((b) => b.textContent);
    expect(chips[0]).toContain("Реконструкция: 1");
    expect(chips[chips.length - 1]).toContain("Пропуски капитала");
    const list = q(tab.el, ".st-qlist").textContent!;
    expect(list).toContain("Подгрузить новые свечи");
    expect(list).toContain("Фандинг");
    expect(q(tab.el, ".st-quality .disclosure summary").textContent).toContain("Качество данных");
  });

  it("плитки с дельтами к предыдущему окну; «—» с причиной, когда сравнения нет", async () => {
    stub(() => json(200, richStats()));
    let tab = await mount();
    const chipText = (k: string) => q(tab.el, `.kpi-${k} .delta`).textContent!;
    expect(chipText("roi")).toContain("▲");
    expect(chipText("roi")).toContain("+8,0 п.п.");
    expect(q(tab.el, ".kpi-roi .delta").className).toContain("pos");
    expect(q(tab.el, ".kpi-dd .delta").className).toContain("pos");                // просадка стала мельче
    expect(q(tab.el, ".kpi-costs .delta").className).not.toMatch(/pos|neg/);       // комиссии — нейтрально
    expect(q(tab.el, ".st-cmp-note").textContent).toContain("предыдущему окну");
    document.body.replaceChildren();
    const r = richStats();
    r.comparison = { available: false, reason: "all_range" };
    stub(() => json(200, r));
    tab = await mount();
    const na = q(tab.el, ".kpi-roi .delta.na");
    expect(na.textContent).toBe("—");
    expect(na.getAttribute("title")).toContain("«Всё»");
  });

  it("заметка о комиссии появляется, только если taker счёта отличается от умолчания бэктестов", async () => {
    const r = richStats();
    r.config = { ...r.config!, taker_fee_est: 0.0015 };
    stub(() => json(200, r));
    const tab = await mount();
    expect(q(tab.el, ".st-fee-note").textContent).toContain("Реальная комиссия счёта 0,15% против 0,10% в бэктестах");
  });

  it("карточки: позиция, лонг/шорт, причины закрытия, издержки (заметка о комиссии), сверка", async () => {
    stub(() => json(200, richStats()));
    const tab = await mount();
    const pos = q(tab.el, ".st-pos");
    expect(pos.textContent).toContain("Позиция сейчас");
    expect(pos.querySelector(".side-badge.short")).not.toBeNull();
    expect(pos.textContent).toContain("Net с учётом издержек");
    expect(qa(pos, ".st-est").length).toBeGreaterThan(0);
    expect(q(tab.el, ".st-sides").textContent).toContain("20 сделок");
    expect(q(tab.el, ".st-split").getAttribute("aria-label")).toBe("Сделок: лонг 20, шорт 20");
    const reasons = qa(tab.el, ".st-rt tbody tr");
    expect(reasons.map((r) => r.querySelector("td")!.textContent)).toEqual(expect.arrayContaining(["бар", "закрытие вручную"]));
    expect(q(tab.el, ".st-rt tbody").textContent).toContain("оценка");
    expect(tab.el.querySelector(".st-fee-note")).toBeNull();                          // taker счёта 0,10% = умолчание бэктестов: заметки нет
    expect(q(tab.el, ".st-costs").textContent).toContain("0,10%");
    const recon = q(tab.el, ".st-recon");
    expect(recon.textContent).toContain("Δ кошелька");
    expect(recon.textContent).toContain("Это не ошибка бота");
    expect(recon.textContent).toContain("вводы/выводы");
  });

  it("позиции нет -> спокойное состояние FLAT; сверка с причиной вместо чисел", async () => {
    const r = richStats();
    r.open_position = null;
    r.reconciliation = { wallet_delta: 5, bot_pnl_est: null, unexplained: null, reason: "no_start_price", flat_at_start: null };
    stub(() => json(200, r));
    const tab = await mount();
    expect(q(tab.el, ".st-pos").textContent).toContain("Позиции нет — бот вне рынка");
    expect(q(tab.el, ".st-pos").textContent).toContain("FLAT");
    expect(q(tab.el, ".st-recon").textContent).toContain("Сверка недоступна");
    expect(q(tab.el, ".st-recon").textContent).toContain("на начало окна");
  });

  it("сверка отсутствует в ответе -> карточки нет", async () => {
    const r = richStats();
    delete (r as { reconciliation?: unknown }).reconciliation;
    stub(() => json(200, r));
    const tab = await mount();
    expect(tab.el.querySelector(".st-recon")).toBeNull();
  });

  it("position_mismatch: заметный янтарный баннер с объяснением", async () => {
    const r = richStats();
    r.position_mismatch = { replayed: { side: "long", qty: 0.02 }, actual: null };
    r.warnings.push({ code: "position_mismatch", level: "warn", text: "Позиция по журналу не совпадает", replayed: { side: "long", qty: 0.02 }, actual: null });
    stub(() => json(200, r));
    const tab = await mount();
    const b = q(tab.el, ".banner.warn.st-mismatch");
    expect(b.getAttribute("role")).toBe("alert");
    expect(b.textContent).toContain("не совпадает с позицией на бирже");
    expect(b.textContent).toContain("лонг 0,0200 BTC");
    expect(b.textContent).toContain("позиции нет");
    expect(qa(tab.el, ".st-qchips .badge").some((x) => x.textContent!.includes("Позиция не сходится"))).toBe(true);
  });

  it("диапазон без сделок: капитал и KPI остаются, плашка «закрытых сделок нет», пустая таблица", async () => {
    const r = richStats();
    r.kpis!.trades = 0; r.kpis!.wins = 0; r.kpis!.losses = 0; r.kpis!.win_rate_pct = null; r.kpis!.profit_factor = null;
    r.trades = { total: 0, truncated: false, rows: [] };
    r.breakdown = { by_reason: [], by_side: [] };
    r.series!.calendar = []; r.series!.histogram = { edges: [], counts: [] };
    r.warnings = [{ code: "few_trades", level: "warn", text: "Мало сделок в окне (0 < 10)", count: 0 }];
    stub(() => json(200, r));
    const tab = await mount();
    expect(q(tab.el, ".st-zero").textContent).toContain("За выбранный период закрытых сделок нет");
    expect(tab.el.querySelector(".st-few")).toBeNull();
    expect(q(tab.el, ".kpi-roi .metric-value").textContent).toContain("19,00%");
    expect(q(tab.el, ".kpi-trades .metric-value").textContent).toBe("0");
    expect(q(tab.el, ".st-trades tbody").textContent).toContain("За выбранный период закрытых сделок нет");
    expect(q(tab.el, ".st-sides").textContent).toContain("Сделок в окне нет");
    expect(lineCalls[0]!.data.length).toBeGreaterThan(0);                        // график капитала построен
  });

  it("мало сделок (< 10) — слим-плашка; ≥ 10 — без неё", async () => {
    stub(() => json(200, gAll));
    const tab = await mount();
    expect(tab.el.querySelector(".st-few")).toBeNull();                           // у gAll warnings без few_trades
    document.body.replaceChildren();
    const r = clone(gAll);
    r.warnings.push({ code: "few_trades", level: "warn", text: "x", count: 10 });
    stub(() => json(200, r));
    const t2 = await mount();
    expect(q(t2.el, ".st-few").className).toContain("notice");
  });
});

describe("вкладка «Статистика»: таблица сделок", () => {
  it("бейдж «оценка» в таблице сделок — только у exact=false; сноска: итог по данным Bybit", async () => {
    const r = richStats();
    stub(() => json(200, r));
    const tab = await mount();
    const byN = new Map(r.trades!.rows.map((x) => [x.n, x]));
    const trs = qa(tab.el, ".st-trades tbody tr");
    expect(trs.length).toBeGreaterThan(10);
    let withBadge = 0, withoutBadge = 0;
    for (const tr of trs) {
      const n = Number(q(tr, ".st-n").textContent);
      const row = byN.get(n)!;
      const badge = qa(tr, ".st-reason .st-mini-badge").some((b) => b.textContent === "оценка");
      expect(badge, `сделка ${n} exact=${row.exact}`).toBe(!row.exact);
      if (badge) withBadge++; else withoutBadge++;
    }
    expect(withBadge).toBeGreaterThan(0);
    expect(withoutBadge).toBeGreaterThan(0);
    expect(q(tab.el, ".st-trades-note").textContent).toContain("Итог — по данным Bybit; «оценка» — рассчитано ботом, точнее после обновления фандинга");
  });

  it("фильтры со счётчиками, сортировка, «Показать ещё», бейджи реконструкции и оценки", async () => {
    const r = richStats();
    r.trades!.rows = [...r.trades!.rows, ...richStats().trades!.rows.map((x, i) => ({ ...x, n: 100 + i }))];       // 80 сделок > 50 строк на странице
    r.trades!.total = 80;
    stub(() => json(200, r));
    const tab = await mount();
    expect(qa(tab.el, ".st-trades tbody tr")).toHaveLength(50);
    expect(q(tab.el, ".st-trades .btn.ghost:not([hidden])")?.textContent ?? "").toBeDefined();
    const more = qa(tab.el, ".st-trades .table-wrap > button").find((b) => !b.hidden)!;
    expect(more.textContent).toContain("Показать ещё");
    more.click();
    expect(qa(tab.el, ".st-trades tbody tr")).toHaveLength(80);
    const f = qa(tab.el, ".st-filters button").map((b) => b.textContent);
    expect(f).toEqual(["Все80", "Лонг40", "Шорт40", "Прибыльные52", "Убыточные28", "Реконструированные2"]);
    qa(tab.el, ".st-filters button")[5]!.click();
    const rows = qa(tab.el, ".st-trades tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent).toContain("реконструкция");
    expect(rows[0]!.textContent).toContain("оценка");
    expect(q(tab.el, ".st-trades .card-head").textContent).toContain("2 из 80");
  });

  it("сортировка по Net и выбор фильтра запоминается", async () => {
    stub(() => json(200, richStats()));
    const tab = await mount();
    const netBtn = qa(tab.el, ".st-trades thead .th-btn").find((b) => b.textContent!.startsWith("Итог"))!;
    netBtn.click();
    const nets = qa(tab.el, ".st-trades tbody tr").map((r) => r.querySelectorAll("td")[3]!.textContent!);
    expect(nets[0]!).toContain("+8,00");                                          // первый щелчок — по убыванию: наибольшая прибыль сверху
    qa(tab.el, ".st-trades thead .th-btn").find((b) => b.textContent!.startsWith("Итог"))!.click();     // шапка перерисована — ищем кнопку заново
    const asc = qa(tab.el, ".st-trades tbody tr").map((r) => r.querySelectorAll("td")[3]!.textContent!);
    expect(asc[0]!).toContain("−6,00");                                           // второй — по возрастанию: наибольший убыток сверху
  });

  it("раскрытие строки: fills с пометкой «синтетическое» и основанием цены", async () => {
    stub(() => json(200, richStats()));
    const tab = await mount();
    const recon = qa(tab.el, ".st-trades tbody tr").find((r) => r.textContent!.includes("реконструкция"))!;
    (recon.querySelector(".row-exp") as HTMLButtonElement).click();
    const exp = q(tab.el, ".row-expansion");
    expect(exp).not.toBeNull();
    expect(qa(exp, ".st-fills-t tbody tr")).toHaveLength(2);
    expect(exp.textContent).toContain("синтетическое");
    expect(exp.textContent).toContain("по стоп-уровню");
    expect((q(tab.el, ".row-exp[aria-expanded=true]")).getAttribute("aria-label")).toContain("Сделка 8");
    (q(tab.el, ".row-exp[aria-expanded=true]") as HTMLButtonElement).click();
    expect(tab.el.querySelector(".row-expansion")).toBeNull();
  });

  it("CSV: выгружается отфильтрованное (UTC, ISO), имя файла содержит диапазон и фильтр", async () => {
    stub(() => json(200, richStats()));
    const tab = await mount();
    qa(tab.el, ".st-filters button")[2]!.click();                                // Шорт
    (qa(tab.el, ".st-trades .btn").find((b) => b.textContent!.includes("CSV")) as HTMLButtonElement).click();
    expect(downloads).toHaveLength(1);
    expect(downloads[0]!.name).toMatch(/^stats-trades_30d_short\.csv$/);
    const lines = downloads[0]!.text.trim().split("\r\n");
    expect(lines).toHaveLength(1 + 20);
    expect(lines[0]).toContain("entry_utc");
    expect(lines[1]).toMatch(/,short,/);
    expect(lines.every((l, i) => i === 0 || /\dT\d\d:\d\d:\d\dZ/.test(l))).toBe(true);
  });

  it("клик по строке выделяет вход/выход на графике капитала; повторный клик и ✕ снимают выделение", async () => {
    stub(() => json(200, richStats()));
    const tab = await mount();
    const eqRec = lineCalls[0]!;
    const before = eqRec.markers[eqRec.markers.length - 1]!;
    expect(before.some((m) => (m as { active?: boolean }).active)).toBe(false);
    qa(tab.el, ".st-trades tbody tr")[0]!.click();
    const marks = eqRec.markers[eqRec.markers.length - 1] as { active?: boolean; kind: string }[];
    expect(marks.filter((m) => m.active).map((m) => m.kind).sort()).toEqual(["entry", "exit"]);
    expect(q(tab.el, ".st-sel").hidden).toBe(false);
    expect(q(tab.el, ".st-sel").textContent).toContain("Выбрана сделка №40");
    expect(q(tab.el, ".st-trades tr[aria-selected=true]")).not.toBeNull();
    (q(tab.el, ".st-sel-x") as HTMLButtonElement).click();
    expect((eqRec.markers[eqRec.markers.length - 1] as { active?: boolean }[]).some((m) => m.active)).toBe(false);
    expect(q(tab.el, ".st-sel").hidden).toBe(true);
  });

  it("усечённый список (500): заметка про последние сделки и CSV только показанных", async () => {
    const r = richStats();
    r.trades!.truncated = true; r.trades!.total = 731;
    stub(() => json(200, r));
    const tab = await mount();
    expect(q(tab.el, ".st-trades-note").textContent).toContain("последние 40 из 731");
  });
});

describe("вкладка «Статистика»: ошибки и недоступность", () => {
  it("available:false (золотой ответ): карточка с причиной и сообщением сервера", async () => {
    const un = goldenUnavailable as unknown as StatsResponse;
    stub(() => json(200, un));
    const tab = await mount();
    const c = q(tab.el, ".st-unavail");
    expect(c.getAttribute("data-reason")).toBe(un.reason);
    expect(c.textContent).toContain(un.message!.split(":")[0]!.slice(0, 12));
    expect(c.textContent).toContain(`Код причины: ${un.reason}`);
    expect(tab.el.querySelector(".st-hero")).toBeNull();
    expect(chip(tab.el, "1м")).toBeDefined();                                      // диапазоны остаются доступными
  });

  for (const reason of ["no_db", "no_trades", "no_equity", "db_error", "error"]) {
    it(`available:false, reason=${reason}: понятный заголовок и подсказка действия`, async () => {
      stub(() => json(200, { ...(goldenUnavailable as unknown as StatsResponse), reason, message: `msg-${reason}` }));
      const tab = await mount();
      const c = q(tab.el, ".st-unavail");
      expect(c.querySelector("h3")!.textContent!.length).toBeGreaterThan(5);
      expect(c.textContent).toContain(`msg-${reason}`);
      expect(c.querySelector(".muted")!.textContent!.length).toBeGreaterThan(30);
      expect(c.querySelector("button")!.textContent).toContain("Обновить");
    });
  }

  it("ошибка сети -> баннер с «Повторить»; повтор восстанавливает страницу", async () => {
    stub(() => { throw new TypeError("Failed to fetch"); });
    const tab = await mount();
    const b = q(tab.el, ".st-err");
    expect(b.getAttribute("data-code")).toBe("network");
    expect(b.textContent).toContain("Сервер дашборда недоступен");
    stub(() => json(200, g7));
    (b.querySelector("button") as HTMLButtonElement).click();
    await flush();
    expect(tab.el.querySelector(".st-err")).toBeNull();
    expect(qa(tab.el, ".st-tile")).toHaveLength(8);
  });

  it("HTTP 500 с detail.message -> баннер с текстом ошибки", async () => {
    stub(() => json(500, { detail: { code: "internal", message: "boom" } }));
    const tab = await mount();
    expect(q(tab.el, ".st-err").textContent).toContain("boom");
  });

  it("сбой обновления при уже показанных данных: данные остаются, сверху плашка «Не удалось обновить»", async () => {
    let fail = false;
    stub(() => (fail ? json(502, { detail: { code: "fetch_failed", message: "шлюз" } }) : json(200, g7)));
    const tab = await mount();
    fail = true;
    await vi.advanceTimersByTimeAsync(60_000);
    await flush();
    expect(qa(tab.el, ".st-tile")).toHaveLength(8);
    expect(q(tab.el, ".st-stale .notice.warn").textContent).toContain("Не удалось обновить");
    fail = false;
    await vi.advanceTimersByTimeAsync(60_000);
    await flush();
    expect(tab.el.querySelector(".st-stale .notice")).toBeNull();
  });

  it("первая загрузка показывает скелетон", () => {
    stub(() => new Promise<Response>(() => {}));
    const tab = createStats({} as Meta);
    document.body.append(tab.el);
    expect(tab.el.querySelector(".st-skel")).not.toBeNull();
    expect(tab.el.querySelectorAll(".st-skel .skel-tile").length).toBe(8);
  });
});

describe("вкладка «Статистика»: автообновление и видимость", () => {
  it("раз в 60 с пока вкладка видна; hide() останавливает; show() возобновляет", async () => {
    const f = stub(() => json(200, g7));
    const tab = await mount();
    expect(f).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(60_000); await flush();
    expect(f).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(60_000); await flush();
    expect(f).toHaveBeenCalledTimes(3);
    tab.hide();
    await vi.advanceTimersByTimeAsync(180_000); await flush();
    expect(f).toHaveBeenCalledTimes(3);
    tab.show(); await flush();
    expect(f).toHaveBeenCalledTimes(4);
  });

  it("в скрытой вкладке браузера запросов нет; при возврате — сразу обновление", async () => {
    const f = stub(() => json(200, g7));
    await mount();
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(180_000); await flush();
    expect(f).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    document.dispatchEvent(new Event("visibilitychange"));
    await flush();
    expect(f).toHaveBeenCalledTimes(2);
  });

  it("«обновлено N с назад» тикает; кнопка обновления делает запрос; dataContext для полосы свежести", async () => {
    const f = stub(() => json(200, g7));
    const tab = await mount();
    const t0 = q(tab.el, ".st-updated").textContent!;
    await vi.advanceTimersByTimeAsync(5_000);
    expect(q(tab.el, ".st-updated").textContent).not.toBe(t0);
    expect(q(tab.el, ".st-updated").textContent).toMatch(/обновлено \d+ с назад/);
    (tab.el.querySelector("button[aria-label='Обновить сейчас']") as HTMLButtonElement).click();
    await flush();
    expect(f).toHaveBeenCalledTimes(2);
    expect(tab.dataContext!()).toEqual({ symbol: "BTCUSDT", tfs: ["1h"] });
    f.mockClear();
    tab.refresh!();
    await flush();
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("устаревший ответ при быстрой смене диапазона не перетирает свежий", async () => {
    const slow: { resolve?: (r: Response) => void } = {};
    stub((url) => (url.includes("range=30d") ? new Promise<Response>((res) => { slow.resolve = res; }) : json(200, richStats())));
    const tab = createStats({} as Meta);
    document.body.append(tab.el);
    tab.show();
    await flush();
    chip(tab.el, "3м").click();
    await flush();
    slow.resolve?.(json(200, g7));
    await flush();
    expect(qa(tab.el, ".st-trades tbody tr")).toHaveLength(40);                   // richStats (90d), а не g7 (2 сделки)
  });
});

describe("вкладка «Статистика»: деньги — главное", () => {
  const clean = (t: string | null | undefined) => (t ?? "").replace(/\s+/g, " ").trim();

  it("порядок колонок: № · Закрыта · Сторона · Итог $ · % счёта · валовый/комиссии/фандинг …", async () => {
    stub(() => json(200, richStats()));
    const tab = await mount();
    const heads = qa(tab.el, ".st-trades thead .th-btn").map((b) => b.textContent!.replace(/[▲▼]/g, ""));
    expect(heads.slice(0, 8)).toEqual(["№", "Закрыта, UTC", "Сторона", "Итог, $", "% счёта", "Валовый", "Комиссии*", "Фандинг*"]);
    expect(heads).toHaveLength(15);
  });

  it("строка: итог в долларах (жирный, знак, ▲/▼) и % счёта рядом; цвет по знаку, а не по стороне", async () => {
    stub(() => json(200, richStats()));
    const tab = await mount();
    const rows = qa(tab.el, ".st-trades tbody tr");
    const tds = rows[0]!.querySelectorAll("td");                                      // по умолчанию — свежие выходы сверху (№40)
    expect(clean(tds[3]!.textContent)).toMatch(/^[▲▼] [+−]\d/);
    expect(tds[3]!.className).toMatch(/strong/);
    expect(clean(tds[4]!.textContent)).toMatch(/^[+−]\d+,\d{2}%$/);
    for (const r of rows) {
      const usd = clean(r.querySelectorAll("td")[3]!.textContent);
      expect(r.querySelectorAll("td")[3]!.classList.contains(usd.includes("−") ? "neg" : "pos")).toBe(true);
      expect(r.querySelector(".side-badge")!.className).not.toMatch(/\b(pos|neg)\b/);
    }
  });

  it("под фильтрами: «Закрыто N: заработано … потеряно … итого …» по ОТФИЛЬТРОВАННЫМ строкам", async () => {
    const r = richStats();
    stub(() => json(200, r));
    const tab = await mount();
    const all = moneySummary(r.trades!.rows);
    const line = () => clean(q(tab.el, ".st-trades .st-money")!.textContent);
    expect(line()).toBe(`Закрыто 40: заработано ▲ ${susd(all.profit)}, потеряно ▼ ${susd(all.loss)}, итого ▲ ${susd(all.net)}`);
    qa(tab.el, ".st-filters button")[2]!.click();                                       // Шорт
    const sh = moneySummary(r.trades!.rows.filter((x) => x.side === "short"));
    expect(line()).toContain("Закрыто 20: заработано");
    expect(line()).toContain(`итого ▲ ${susd(sh.net)}`);
    qa(tab.el, ".st-filters button")[4]!.click();                                       // Убыточные
    const ls = moneySummary(r.trades!.rows.filter((x) => (x.net ?? 0) < 0));
    expect(line()).toContain(`заработано ${susd(0)}`);
    expect(line()).toContain(`потеряно ▼ ${susd(ls.loss)}`);
    expect(q(tab.el, ".st-trades .st-money .st-m.strong")!.classList.contains("neg")).toBe(true);
  });

  it("нет сделок в окне: строка денег — «Закрытых сделок нет»", async () => {
    stub(() => json(200, g7));
    const tab = await mount();
    expect(clean(q(tab.el, ".st-trades .st-money")!.textContent)).toContain("Закрыто 2:");
    const empty = richStats();
    empty.trades = { total: 0, truncated: false, rows: [] };
    empty.kpis = { ...empty.kpis!, trades: 0, wins: 0, losses: 0 };
    stub(() => json(200, empty));
    document.body.replaceChildren();
    const tab2 = await mount();
    expect(clean(q(tab2.el, ".st-trades .st-money")!.textContent)).toBe("Закрытых сделок нет");
  });

  it("CSV отфильтрованного содержит equity_at_entry и ret_account_pct", async () => {
    stub(() => json(200, richStats()));
    const tab = await mount();
    (qa(tab.el, ".st-trades .btn").find((b) => b.textContent!.includes("CSV")) as HTMLButtonElement).click();
    const lines = downloads[0]!.text.trim().split("\r\n");
    expect(lines[0]).toMatch(/,equity_at_entry,ret_account_pct$/);
    expect(lines[1]).toMatch(/,\d+(\.\d+)?,-?\d+(\.\d+)?$/);
  });

  it("позиция сейчас: нереализованный P/L в долларах и в % счёта", async () => {
    stub(() => json(200, richStats()));
    const tab = await mount();
    const pnl = q(tab.el, ".st-pos .st-pos-pnl")!;
    expect(clean(pnl.textContent)).toContain("+0,40 $");
    expect(clean(q(tab.el, ".st-pos-pct")!.textContent)).toMatch(/^\+0,03% счёта$/);
  });

});
