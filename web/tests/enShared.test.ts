// @vitest-environment jsdom
// Английский интерфейс общего слоя (F5): баннеры ошибок по всем известным кодам, командная палитра, графики (тепловые карты, календарь, гистограмма, столбцы),
// полоса свежести данных, мелкие компоненты ui/. Русский вывод тех же компонентов проверяют существующие тесты (tests/setup.ts закрепляет ru) и несколько
// точных проверок ниже (серверные сообщения известных кодов в ru совпадают с исходными).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cyrillicIn, importEn } from "./enHelpers";
import { testMeta } from "./fixtures/meta";
import type { ApiError } from "../src/api";

beforeEach(() => {
  document.body.replaceChildren();
  localStorage.clear();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
/** Ожидаем отказ промиса и возвращаем ошибку API. */
const rejected = (p: Promise<unknown>): Promise<ApiError> => p.then(() => { throw new Error("ожидалась ошибка"); }, (e) => e as ApiError);

// ---------------------------------------------------------------- ошибки API
interface Case { code: string; status: number; message: string; extra?: Record<string, unknown>; title: string; en: string }
const estimate = { days: 365, klines_requests: 6, derivs_requests: 4, requests: 10, minutes: 1 };
const CASES: Case[] = [
  { code: "data_missing", status: 409, message: "Нет локальных данных BTCUSDT 1h: BTCUSDT_1h.parquet, BTCUSDT_funding.parquet. Скачайте их явно (POST /api/data/fetch).",
    extra: { symbol: "BTCUSDT", tf: "1h", missing: ["BTCUSDT_1h.parquet", "BTCUSDT_funding.parquet"], estimate },
    title: "No local data", en: "No local data for BTCUSDT 1h: BTCUSDT_1h.parquet, BTCUSDT_funding.parquet. Download it explicitly (POST /api/data/fetch)." },
  { code: "shutting_down", status: 503, message: "сервер останавливается", title: "Error", en: "the server is shutting down" },
  { code: "db_error", status: 503, message: "база данных временно недоступна", title: "Error", en: "the database is temporarily unavailable" },
  { code: "bad_results", status: 500, message: "файл результатов повреждён (EOFError)", title: "Error", en: "the results file is corrupted (EOFError)" },
  { code: "timeout", status: 504, message: "расчёт дольше 600 с", title: "The calculation took too long", en: "the calculation took longer than 600 s" },
  { code: "superseded", status: 429, message: "запрос вытеснен более новым", title: "Error", en: "the request was superseded by a newer one" },
  { code: "busy", status: 429, message: "сервер занят другим расчётом", extra: { waited_s: 5, queue_wait_s: 5 }, title: "Server busy", en: "the server is busy with another calculation" },
  { code: "busy", status: 429, message: "уже идёт загрузка данных", extra: { waited_s: 5, queue_wait_s: 5 }, title: "Server busy", en: "a data download is already in progress" },
  { code: "cancelled", status: 499, message: "клиент отменил запрос", title: "Error", en: "the client cancelled the request" },
  { code: "no_results", status: 404, message: "нет файла результатов walk-forward: [Errno 2] No such file or directory: '…/wf_BTCUSDT.csv'", title: "No results",
    en: "no walk-forward results file: [Errno 2] No such file or directory: '…/wf_BTCUSDT.csv'" },
  { code: "update_failed", status: 502, message: "не удалось обновить данные: ConnectError: boom", title: "Error", en: "failed to update the data: ConnectError: boom" },
  { code: "fetch_failed", status: 502, message: "ReadTimeout: slow", title: "Error", en: "download failed: ReadTimeout: slow" },
];

describe("English: баннеры ошибок API по известным кодам", () => {
  const load = async () => {
    const { api } = await importEn(() => import("../src/api"));
    const { errorBanner } = await import("../src/ui/banner");
    const i18n = await import("../src/i18n");
    const fail = async (c: Case) => {
      vi.stubGlobal("fetch", vi.fn(async () => json({ detail: { code: c.code, message: c.message, ...c.extra } }, c.status)));
      return rejected(api.meta());
    };
    return { errorBanner, fail, i18n };
  };

  it("каждый известный код собирается на фронтенде: полный английский текст, серверный русский не показывается", async () => {
    const { errorBanner, fail } = await load();
    for (const c of CASES) {
      const err = await fail(c);
      expect(err.code).toBe(c.code);
      expect(err.message, c.code).toBe(c.en);
      const box = errorBanner(err, () => {});
      expect(box.querySelector("strong")!.textContent, c.code).toBe(c.title);
      expect(box.querySelector("span")!.textContent, c.code).toBe(" " + c.en);
      expect(cyrillicIn(box), c.code).toEqual([]);
    }
  });

  it("те же ответы в ru дают прежний серверный текст (регрессия: ru не меняется)", async () => {
    const { fail, i18n } = await load();
    i18n.__setLang("ru");
    for (const c of CASES) expect((await fail(c)).message, c.code).toBe(c.message);
  });

  it("data_missing: кнопка загрузки и строка оценки — по-английски", async () => {
    const { errorBanner, fail } = await load();
    const box = errorBanner(await fail(CASES[0]!), () => {});
    expect(box.querySelector("button")!.textContent).toBe("Download BTCUSDT 1h from Bybit");
    expect(box.querySelector("p")!.textContent).toBe("Download of 365 d: ≈ 10 Bybit requests (candles 6 + derivatives 4), up to 1 min.");
  });

  it("неизвестные коды и bad_request: серверное сообщение показывается как есть; synthesized-коды — по-английски", async () => {
    const { api } = await importEn(() => import("../src/api"));
    const { errorBanner } = await import("../src/ui/banner");
    const { ApiError } = await import("../src/api");
    const { clientErrorText } = await import("../src/apiErrors");
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: { code: "bad_request", message: "неизвестный символ или таймфрейм" } }, 422)));
    const e1 = await rejected(api.meta());
    expect(e1.message).toBe("неизвестный символ или таймфрейм");              // данные сервера — без перевода
    expect(errorBanner(e1, () => {}).querySelector("strong")!.textContent).toBe("Error");

    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const net = await rejected(api.meta());
    expect(net.code).toBe("network");
    const box = errorBanner(net, () => {});
    expect(box.textContent).toContain("Server unavailable the server is unreachable");
    expect(cyrillicIn(box)).toEqual([]);

    vi.stubGlobal("fetch", vi.fn(async () => { throw Object.assign(new Error("x"), { name: "AbortError" }); }));
    const ab = await rejected(api.meta());
    expect(ab.code).toBe("aborted");
    expect(ab.message).toBe("cancelled");
    expect(ab.silent).toBe(true);

    const to = new ApiError(0, "timeout", clientErrorText.timeout(120));
    const tb = errorBanner(to, () => {});
    expect(tb.textContent).toContain("The calculation took too long no response for more than 120 s");
    expect(cyrillicIn(tb)).toEqual([]);
  });

  it("баннер упавшей вкладки", async () => {
    const { crashBanner } = await importEn(() => import("../src/ui/banner"));
    const box = crashBanner("Lab", "boom", { retry() {}, reset() {} });
    expect(box.textContent).toBe("The “Lab” tab failed to open. boomThe most likely cause is corrupted saved settings of this tab. The other tabs work.RetryReset the tab's saved settings");
    expect(cyrillicIn(box)).toEqual([]);
  });
});

// ---------------------------------------------------------------- командная палитра
describe("English: командная палитра", () => {
  const prep = async () => {
    const pal = await importEn(() => import("../src/ui/palette"));
    const core = await import("../src/ui/paletteCore");
    const { t } = await import("../src/i18n");
    const titles = { lab: t("tab.lab"), portfolio: t("tab.portfolio"), wf: t("tab.wf"), best: t("tab.best"), live: t("tab.live"), stats: t("tab.stats") };
    pal.setPaletteContext(testMeta, titles);
    const fresh = document.createElement("div");
    fresh.className = "freshness";
    fresh.append(Object.assign(document.createElement("button"), { className: "btn" }));
    document.body.append(fresh);
    window.dispatchEvent(new CustomEvent("tb-live", { detail: { available: true, legs: [
      { key: "EMA cross@1h", weight: 0.5, enabled: true, signal: 1, params: { fast: 12 } },
      { key: "OI flush@4h", weight: 0.25, enabled: false, signal: null, params: { n: 10 } },
    ] } }));
    return { pal, core };
  };

  it("команды: названия, подсказки и группы без кириллицы; ключевые слова ищутся на обоих языках", async () => {
    const { pal, core } = await prep();
    const cmds = pal.buildCommands();
    const text = cmds.flatMap((c) => [c.title, c.hint ?? "", c.group]);
    expect(text.filter((s) => /[А-Яа-яЁё]/.test(s))).toEqual([]);
    const byId = Object.fromEntries(cmds.map((c) => [c.id, c]));
    expect(byId["tab:lab"]).toMatchObject({ title: "Lab", group: "Tabs" });
    expect(byId["theme:light"]).toMatchObject({ title: "Theme: Light", group: "Theme" });
    expect(byId["act:fetch"]).toMatchObject({ title: "Fetch new candles", group: "Actions" });
    expect(byId["act:github"]).toMatchObject({ title: "Open the GitHub repository", hint: "project source code, README and CI" });
    expect(byId["lab:EMA cross"]!.title).toBe("Open in Lab: EMA cross");
    expect(byId["lab:EMA cross"]!.hint).toMatch(/EMA/);                             // описание стратегии из каталога (strategies.json), а не серверное «ema»
    expect(byId["lab:EMA cross"]!.group).toBe("Strategies");
    expect(byId["act:live-legs"]!.hint).toBe("from the “Bot (live)” snapshot");
    // поиск: английский и русский запросы находят одно и то же
    expect(core.filterCommands(cmds, "backtest").map((c) => c.id)).toContain("tab:lab");
    expect(core.filterCommands(cmds, "бэктест").map((c) => c.id)).toContain("tab:lab");
    expect(core.filterCommands(cmds, "светлая").map((c) => c.id)).toContain("theme:light");
    expect(core.filterCommands(cmds, "theme light")[0]!.id).toBe("theme:light");
  });

  it("копирование текущих ног бота: строка по-английски", async () => {
    const { pal } = await prep();
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const cmd = pal.buildCommands().find((c) => c.id === "act:live-legs")!;
    await cmd.run();
    await new Promise((r) => setTimeout(r, 0));
    expect(writeText).toHaveBeenCalledWith('EMA cross@1h: weight 0.5, signal 1, {"fast":12}\nOI flush@4h: weight 0.25 (off), signal —, {"n":10}');
  });

  it("диалог палитры: плейсхолдер, подвал, счётчик вариантов (множественное число)", async () => {
    const { pal } = await prep();
    const { button } = pal.installPalette();
    document.body.append(button);
    expect(button.textContent).toContain("Search");
    expect(button.getAttribute("aria-label")).toMatch(/^Command palette \(/);
    HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
    button.click();
    const dlg = document.querySelector("dialog.palette") as HTMLDialogElement;
    expect(dlg).not.toBeNull();
    expect(dlg.querySelector("input")!.getAttribute("placeholder")).toBe("Where to go or what to do…");
    expect(dlg.querySelector(".pal-foot")!.textContent).toBe("↑↓ selectEnter runEsc close");
    expect(dlg.querySelector("[aria-live]")!.textContent).toMatch(/^\d+ options$/);
    const input = dlg.querySelector("input")!;
    input.value = "tab lab";
    input.dispatchEvent(new Event("input"));
    expect(cyrillicIn(dlg)).toEqual([]);
  });
});

// ---------------------------------------------------------------- графики
describe("English: графики", () => {
  const mount = (w = 640) => {
    const host = document.createElement("div");
    Object.defineProperty(host, "clientWidth", { configurable: true, value: w });
    document.body.append(host);
    return host;
  };

  it("тепловые карты: месяцы, заголовки, подписи ячеек и легенды — по-английски", async () => {
    const hm = await importEn(() => import("../src/charts/heatmap"));
    const monthly = hm.monthlyHeatmap([{ y: 2025, m: 1, v: 0.1 }, { y: 2025, m: 2, v: -0.05 }], [{ y: 2025, v: 0.04 }]);
    document.body.append(monthly);
    const heads = [...monthly.querySelectorAll("thead th")].map((th) => th.textContent);
    expect(heads).toEqual(["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Year"]);
    expect(monthly.querySelector("td")!.getAttribute("aria-label")).toBe("Jan 2025: +10.00%");
    monthly.querySelector("td")!.dispatchEvent(new Event("pointerenter"));
    expect(monthly.querySelector(".chart-tip")!.textContent).toContain("Return");
    expect(cyrillicIn(monthly)).toEqual([]);

    const res = { x: { name: "fast", values: [5, 10] }, y: { name: "slow", values: [20, 40] }, points: null, base: { fast: 10, slow: 40 }, grids: { total_return: [[0.1, -0.2], [0.3, 0.05]], max_drawdown: [[-0.1, -0.2], [-0.3, -0.05]] } } as unknown as import("../src/types").SweepResult;
    const sweep = hm.sweepHeatmap(res, "total_return", { fast: 10, slow: 40 }, () => {});
    document.body.append(sweep);
    expect(sweep.querySelector("td.best button")!.getAttribute("aria-label")).toContain(", best for the metric");
    expect(sweep.querySelector("td.base button")!.getAttribute("aria-label")).toContain(", current point");
    expect(sweep.querySelector("td button")!.getAttribute("aria-label")).toMatch(/^fast=5, slow=20: .+ — click to apply$/);
    const dd = hm.sweepHeatmap(res, "max_drawdown", {}, () => {});
    expect(dd.querySelector(".heat-legend")!.textContent).toMatch(/^worse: .+better: .+$/);
    expect(hm.SWEEP_METRICS.map((m) => m.label)).toEqual(["Return", "Sharpe", "Calmar", "Avg month", "Max drawdown", "Trades"]);
    expect(cyrillicIn(sweep)).toEqual([]);
    expect(cyrillicIn(dd)).toEqual([]);

    const one = { x: { name: "fast", values: [5, 10, 15] }, points: [{ total_return: 0.1, trades: 3 }, { total_return: 0.3, trades: 5 }, { total_return: -0.1, trades: 1 }], grids: {} } as unknown as import("../src/types").SweepResult;
    const prof = hm.sweepProfile(one, "total_return", 10, () => {});
    const host = mount();
    host.append(prof.el);
    expect(prof.el.querySelector("svg")!.getAttribute("aria-label")).toBe("Return vs fast");
    expect(prof.el.querySelector("g.pt")!.getAttribute("aria-label")).toMatch(/^fast=5: .+, 3 trades\. Apply$/);
    expect(prof.el.textContent).toContain("now");
    expect(prof.el.querySelector("p.small")!.textContent).toBe("Outline — current value, green dot — best for the metric (fast=10).");
    expect(cyrillicIn(prof.el)).toEqual([]);
    prof.destroy();
  });

  it("календарь дневного P/L: сетка за год и компактный список — по-английски", async () => {
    const { calendarChart } = await importEn(() => import("../src/charts/calendar"));
    const cells = [{ date: "2026-05-26", net: 3.5, trades: 2 }, { date: "2026-06-01", net: -2, trades: 1 }, { date: "2026-06-03", net: 0, trades: 0 }];
    const grid = calendarChart("2026-05-01", "2026-06-30", cells);
    document.body.append(grid);
    expect(grid.querySelector(".cal-grid")!.getAttribute("aria-label")).toBe("Daily P/L calendar: 2 trading days, 1 profitable, 1 losing");
    expect([...grid.querySelectorAll(".cal-wd span")].map((s) => s.textContent).filter(Boolean)).toEqual(["Mon", "Wed", "Fri", "Sun"]);
    expect([...grid.querySelectorAll(".cal-months span")].map((s) => s.textContent)).toContain("Jun");
    grid.querySelector(".cal-cell.pos")!.dispatchEvent(new Event("pointerenter"));
    expect(grid.querySelector(".chart-tip")!.textContent).toContain("Trades");
    expect(grid.querySelector(".cal-legend")!.textContent).toContain("−$3.50");
    expect(cyrillicIn(grid)).toEqual([]);

    const list = calendarChart("2026-05-25", "2026-05-28", [{ date: "2026-05-26", net: 3.5, trades: 1 }, { date: "2026-05-27", net: -1, trades: 3 }]);
    expect(list.querySelector(".cal-list")!.getAttribute("aria-label")).toBe("P/L by day");
    const t = [...list.querySelectorAll(".cal-row-t")].map((s) => s.textContent);
    expect(t).toEqual(["no trades", "3 trades", "1 trade", "no trades"]);
    expect(cyrillicIn(list)).toEqual([]);

    const multi = calendarChart("2025-03-01", "2026-06-30", [{ date: "2025-04-02", net: 1, trades: 1 }, { date: "2026-05-26", net: 3.5, trades: 2 }]);
    expect(multi.querySelector(".cal-grid")!.getAttribute("aria-label")).toBe("Daily P/L calendar for 2026: 2 trading days, 2 profitable, 0 losing");
    expect(cyrillicIn(multi)).toEqual([]);
  });

  it("гистограмма, столбцы, спарклайн и полосы: подписи и пустые состояния по-английски", async () => {
    const { histogramChart } = await importEn(() => import("../src/charts/histogram"));
    const { barChart } = await import("../src/charts/bars");
    const { sparkline } = await import("../src/charts/sparkline");
    const { signalStrips } = await import("../src/charts/strips");
    const { histModel } = await import("../src/charts/histModel");

    const host = mount();
    histogramChart(host, () => histModel({ edges: [-2, -1, 0, 1, 2], counts: [1, 3, 5, 2] }));
    expect(host.querySelector("svg")!.getAttribute("aria-label")).toBe("Trade return histogram");
    const labels = [...host.querySelectorAll("g.hbar")].map((g) => g.getAttribute("aria-label")!);
    expect(labels.some((l) => /: 1 trade \(\d+%\), loss$/.test(l))).toBe(true);
    expect(labels.some((l) => /: 5 trades \(\d+%\), profit$/.test(l))).toBe(true);
    expect(host.textContent).toContain("trade return, %");
    expect(cyrillicIn(host)).toEqual([]);
    const empty = mount();
    histogramChart(empty, () => null);
    expect(empty.textContent).toBe("No trades for the histogram");

    const bars = mount();
    barChart(bars, () => ({ categories: ["2024", "2025"], fmt: String, bySign: true, series: [{ name: "x", color: "--c1", values: [1, -1] }] }));
    expect(bars.querySelector("svg")!.getAttribute("aria-label")).toBe("Bar chart");
    expect(cyrillicIn(bars)).toEqual([]);
    const nobars = mount();
    barChart(nobars, () => ({ categories: [], fmt: String, series: [] }));
    expect(nobars.textContent).toBe("No data to chart");

    expect(sparkline([1, 2, 3])!.getAttribute("aria-label")).toBe("Chart");

    const strips = mount();
    signalStrips(strips);
    expect(strips.querySelector("canvas.strips")!.getAttribute("aria-label")).toBe("Signals and exposure by portfolio leg");
    expect(strips.textContent).toBe("Hover over a strip");
    expect(cyrillicIn(strips)).toEqual([]);
  });
});

// ---------------------------------------------------------------- полоса свежести
describe("Полоса свежести данных", () => {
  const status = (over: Record<string, unknown> = {}) => ({
    symbol: "BTCUSDT", tf: "1h", exists: true, now: "2026-10-08T12:00:00+00:00", step_min: 60, estimate, last: "2026-10-08T10:00:00+00:00", first: "2025-10-08T00:00:00+00:00",
    bars: 1234, behind_bars: 2, expected_last: "2026-10-08T11:00:00+00:00", level: "ok", derivs: { funding: "2026-10-08T08:00:00+00:00", oi: null, lsr: "2026-10-08T10:00:00+00:00", granularity: "1h" }, ...over,
  });
  const routes = (st: unknown, upd: unknown) => vi.stubGlobal("fetch", vi.fn(async (url: string) => (String(url).includes("/data/update") ? json(upd) : json(st))));
  const setup = async (en: boolean) => {
    // ru — тоже в свежем графе модулей: прежние тесты этого файла оставили en в кэше
    const mod = en ? await importEn(() => import("../src/ui/freshness")) : await (async () => {
      vi.resetModules();
      (await import("../src/i18n")).__setLang("ru");
      return import("../src/ui/freshness");
    })();
    const host = document.createElement("div");
    document.body.append(host);
    const bar = mod.createFreshness(host, () => ({ symbol: "BTCUSDT", tfs: ["1h"] }), () => {});
    return { host, bar };
  };

  it("en: строка статуса, время МСК (MSK), производные, кнопка и подсказки", async () => {
    routes(status(), {});
    const { host, bar } = await setup(true);
    await bar.refresh();
    expect(host.querySelector(".fresh-row span")!.textContent).toBe(
      "BTCUSDT · 1h · last candle 2026-10-08 10:00 UTC (MSK 13:00) · closed 1 h ago · 1,234 candles · since 2025-10-08 · behind by 2 candles");
    expect(host.querySelector(".fresh-row span")!.getAttribute("title")).toBe("Expected last closed candle: 2026-10-08 11:00 UTC");
    expect(host.querySelector(".fresh-sub")!.textContent).toBe("derivatives (1h): funding 10-08 08:00 · OI none · L/S 10-08 10:00 UTC");
    expect(host.querySelector("button")!.textContent).toBe("Fetch new candles");
    expect(host.querySelector("button")!.getAttribute("title")).toBe("Download only new candles and derivatives from Bybit (public API)");
    expect(cyrillicIn(host)).toEqual([]);
  });

  it("en: нет локальных данных; статус недоступен", async () => {
    routes(status({ exists: false, last: null }), {});
    const { host, bar } = await setup(true);
    await bar.refresh();
    expect(host.querySelector(".fresh-row span")!.textContent).toBe("BTCUSDT · 1h · no local data — click “Fetch new candles” to download");
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "x" }, 500)));
    await bar.refresh();
    expect(host.querySelector(".muted")!.textContent).toBe("Data status unavailable");
  });

  it("en: тост после докачки — множественное число (1 candle / 3 candles / нет новых)", async () => {
    const results: [number, string][] = [[1, "Added 1 candle, last 2026-10-08 10:00 UTC"], [3, "Added 3 candles, last 2026-10-08 10:00 UTC"], [0, "No new candles, last 2026-10-08 10:00 UTC"]];
    for (const [added, text] of results) {
      document.body.replaceChildren();
      routes(status(), { added, status: status(), before_last: null, warning: null, purged: 0 });
      const { host } = await setup(true);
      host.querySelector("button")!.click();
      await vi.waitFor(() => expect(document.querySelector(".toast span")?.textContent).toBe(text));
    }
  });

  it("ru не изменился: строка статуса, МСК, склонение «свеча/свечи/свечей»", async () => {
    routes(status(), {});
    const { host, bar } = await setup(false);
    await bar.refresh();
    const { int } = await import("../src/ui/fmt");
    expect(host.querySelector(".fresh-row span")!.textContent).toBe(
      `BTCUSDT · 1h · последняя свеча 2026-10-08 10:00 UTC (МСК 13:00) · закрыта 1 ч назад · свечей ${int(1234)} · с 2025-10-08 · отстаёт на 2 св.`);
    expect(host.querySelector(".fresh-sub")!.textContent).toBe("деривативы (1h): фандинг 10-08 08:00 · OI нет · L/S 10-08 10:00 UTC");
    expect(host.querySelector("button")!.textContent).toBe("Подгрузить новые свечи");
    for (const [added, text] of [[1, "Добавлено 1 свеча, последняя 2026-10-08 10:00 UTC"], [3, "Добавлено 3 свечи, последняя 2026-10-08 10:00 UTC"], [11, "Добавлено 11 свечей, последняя 2026-10-08 10:00 UTC"], [0, "Новых свечей нет, последняя 2026-10-08 10:00 UTC"]] as [number, string][]) {
      document.body.replaceChildren();
      routes(status(), { added, status: status(), before_last: null, warning: null, purged: 0 });
      const s = await setup(false);
      s.host.querySelector("button")!.click();
      await vi.waitFor(() => expect(document.querySelector(".toast span")?.textContent).toBe(text));
    }
  });
});

// ---------------------------------------------------------------- мелкие компоненты
describe("English: компоненты ui/", () => {
  it("таблица, тема, горячие клавиши, ссылка на GitHub, индикатор загрузки, выбор плеча", async () => {
    const { dataTable } = await importEn(() => import("../src/ui/table"));
    const tbl = dataTable<{ v: number }>({ columns: [{ key: "v", label: "v", value: (r) => r.v, render: (r) => String(r.v) }], limit: 2 });
    document.body.append(tbl.el);
    expect(tbl.el.getAttribute("aria-label")).toBe("Table");
    tbl.setRows([]);
    expect(tbl.el.textContent).toContain("No data");
    tbl.setRows([{ v: 1 }, { v: 2 }, { v: 3 }, { v: 4 }, { v: 5 }]);
    expect(tbl.el.querySelector("button.btn")!.textContent).toBe("Show 2 more (3 left)");

    const { themeSwitch } = await import("../src/ui/theme");
    const sw = themeSwitch();
    expect(sw.getAttribute("aria-label")).toBe("Color theme");
    expect([...sw.querySelectorAll("button")].map((b) => b.getAttribute("aria-label"))).toEqual(["Light theme", "Auto theme", "Dark theme"]);

    const { installShortcuts } = await import("../src/ui/shortcuts");
    const sc = installShortcuts({ lab: "Lab", portfolio: "Portfolio", wf: "Walk-forward", best: "Best strategies", live: "Bot (live)", stats: "Statistics" });
    document.body.append(sc.button);
    expect(sc.button.getAttribute("aria-label")).toBe("Keyboard shortcuts (?)");
    sc.button.click();
    const pop = document.querySelector(".popover")!;
    expect(pop.querySelector("h3")!.textContent).toBe("Keyboard shortcuts");
    expect(pop.textContent).toContain("Previous / next tab (focus on the tabs)");

    const { githubLink } = await import("../src/ui/githubLink");
    expect(githubLink().getAttribute("aria-label")).toBe("Source code on GitHub (opens in a new tab)");
    const { jobsIndicator } = await import("../src/ui/jobs");
    expect(jobsIndicator().textContent).toBe("downloading data…");
    const { leverageControl } = await import("../src/ui/levControl");
    const lev = leverageControl({ value: 5, onInput: () => {} });
    expect(lev.el.querySelector("[aria-label='Leverage, scale']")).not.toBeNull();
    expect(lev.el.querySelector("[aria-label='Leverage presets']")).not.toBeNull();

    for (const root of [tbl.el, sw, pop, lev.el]) expect(cyrillicIn(root as Element)).toEqual([]);
  });

  it("периоды вкладок: «90 d», «1 year», «2 years»; причина ограничения истории", async () => {
    const { DAY_OPTIONS, capReason } = await importEn(() => import("../src/tabs/tab"));
    expect(DAY_OPTIONS.map((o) => o.label)).toEqual(["90 d", "180 d", "1 year", "2 years", "3 years"]);
    expect(capReason({ history_days: { "1m": 90 } } as never, "1m", 365)).toBe("only up to 90 d of 1m history is available");
  });
});
