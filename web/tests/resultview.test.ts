// @vitest-environment jsdom
// Вид результата (jsdom, графики подменены): герой с дельтами к B&H, «Все метрики», фильтр сделок, выгрузка CSV, короткий период, состояния.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BacktestResult, TradeRow } from "../src/types";

const downloads: { name: string; text: string }[] = [];
vi.mock("../src/ui/download", () => ({ downloadText: (name: string, text: string) => { downloads.push({ name, text }); } }));
vi.mock("../src/charts/line", () => ({ lineChart: () => ({ setData() {}, setLog() {}, setMarkers() {}, destroy() {} }) }));
vi.mock("../src/charts/bars", () => ({ barChart: () => ({ redraw() {}, destroy() {} }) }));
vi.mock("../src/charts/heatmap", () => ({ monthlyHeatmap: () => document.createElement("div") }));

const { createResultView } = await import("../src/tabs/resultview");

const trade = (side: "long" | "short", ret: number, i: number): TradeRow =>
  ({ entry: `2026-01-0${i + 1} 00:00:00`, exit: `2026-01-0${i + 1} 12:00:00`, side, ret, bars: 5, entry_price: 100, exit_price: 101 });

const result = (over: Partial<BacktestResult> = {}, days = 365): BacktestResult => ({
  metrics: {
    total_return: 0.42, buy_hold_return: -0.02, cagr: 0.4, max_drawdown: -0.12, sharpe: 1.9, calmar: 3.3, trades: 4, win_rate: 0.5,
    avg_month: 0.03, median_month: 0.02, months_15plus: 0.1, green_months: 0.6, liquidations: 0, worst_month: -0.08, profit_factor: 1.5, avg_trade: 0.01, sortino: 2.4, exposure: 0.8, bh_max_drawdown: -0.4,
  },
  data_range: { start: "2025-01-01T00:00:00Z", end: new Date(Date.UTC(2025, 0, 1) + days * 86400000).toISOString(), bars: 100, step_min: 60 },
  t: [1, 2], equity: [1, 1.1], bh: [1, 1], drawdown: [0, -0.1], price: [100, 101],
  markers: { entries: [], exits: [], truncated: false }, monthly: [], yearly: [],
  trades: [trade("long", 0.05, 0), trade("short", -0.02, 1), trade("long", -0.01, 2), trade("short", 0.03, 3)], trades_total: 4,
  ...over,
} as BacktestResult);

const chip = (el: HTMLElement, text: string) => [...el.querySelectorAll(".rv-filters button")].find((b) => b.textContent?.startsWith(text)) as HTMLButtonElement;
const rows = (el: HTMLElement) => el.querySelectorAll(".rv-trades tbody tr");

beforeEach(() => { downloads.length = 0; document.body.replaceChildren(); });
afterEach(() => vi.restoreAllMocks());

describe("resultview: герой и метрики", () => {
  it("четыре главные плитки с дельтами к B&H; остальные метрики в «Все метрики»", () => {
    const v = createResultView();
    v.setData(result());
    const tiles = v.el.querySelectorAll(".rv-tile");
    expect(tiles).toHaveLength(4);
    expect(tiles[0]!.textContent).toContain("Доходность");
    expect(tiles[0]!.querySelector(".delta")!.textContent).toContain("+44 п.п. к B&H");
    expect(tiles[1]!.querySelector(".delta")!.textContent).toContain("мельче B&H");
    expect(tiles[3]!.textContent).toContain("винрейт 50%");
    expect(v.el.querySelector(".rv-all summary")!.textContent).toContain("Все метрики");
    expect(v.el.querySelectorAll(".rv-minis .mini").length).toBeGreaterThanOrEqual(12);
    expect(v.el.classList.contains("is-initial")).toBe(false);
  });

  it("короткий период (< 180 дн.) — узкая плашка-предупреждение, годовые метрики с «~»; длинный период — без плашки", () => {
    const v = createResultView();
    v.setData(result({}, 90));
    expect(v.el.querySelector(".rv-notices .notice.warn")!.textContent).toContain("Короткий период");
    expect(v.el.querySelector(".rv-minis")!.textContent).toContain("~");
    v.setData(result({}, 365));
    expect(v.el.querySelector(".rv-notices .notice")).toBeNull();
  });

  it("спот — информационная плашка с реальной комиссией", () => {
    const v = createResultView();
    v.setData(result({ market: "spot", effective: { leverage: 1, long_only: true, fee: 0.001 } as BacktestResult["effective"] }));
    expect(v.el.querySelector(".rv-notices")!.textContent).toContain("Спот");
    expect(v.el.querySelector(".rv-notices")!.textContent).toContain("0,100 %");
  });
});

describe("resultview: сделки", () => {
  it("фильтры со счётчиками показывают нужные сделки", () => {
    const v = createResultView();
    v.setData(result());
    expect(rows(v.el)).toHaveLength(4);
    expect(chip(v.el, "Все").textContent).toContain("4");
    chip(v.el, "Лонг").click();
    expect(rows(v.el)).toHaveLength(2);
    chip(v.el, "Прибыльные").click();
    expect(rows(v.el)).toHaveLength(2);
    expect(chip(v.el, "Прибыльные").getAttribute("aria-pressed")).toBe("true");
    expect(chip(v.el, "Лонг").getAttribute("aria-pressed")).toBe("false");
    chip(v.el, "Убыточные").click();
    expect([...rows(v.el)].every((r) => r.classList.contains("tr-loss"))).toBe(true);
  });

  it("CSV выгружает именно отфильтрованные сделки, имя файла берётся из контекста", () => {
    const v = createResultView({ fileHint: () => ["BTCUSDT", "EMA cross", "1h"] });
    v.setData(result());
    chip(v.el, "Шорт").click();
    (v.el.querySelector(".rv-trades-tools .btn") as HTMLButtonElement).click();
    expect(downloads).toHaveLength(1);
    expect(downloads[0]!.name).toBe("trades_BTCUSDT_EMA-cross_1h_short.csv");
    const lines = downloads[0]!.text.trim().split("\r\n");
    expect(lines[0]).toBe("entry,exit,side,return,bars,entry_price,exit_price");
    expect(lines).toHaveLength(3);
    expect(lines.slice(1).every((l) => l.includes(",short,"))).toBe(true);
  });

  it("нет сделок: пустое состояние, кнопка CSV неактивна", () => {
    const v = createResultView();
    v.setData(result({ trades: [], trades_total: 0 }));
    expect(v.el.querySelector(".rv-trades td.empty")!.textContent).toContain("сделок нет");
    expect((v.el.querySelector(".rv-trades-tools .btn") as HTMLButtonElement).disabled).toBe(true);
  });

  it("строка с прибылью/убытком получает класс окраски, результат — со знаком-глифом", () => {
    const v = createResultView();
    v.setData(result());
    const first = [...rows(v.el)].find((r) => r.classList.contains("tr-win"))!;
    expect(first.textContent).toMatch(/▲ \+/);
  });
});

describe("resultview: состояния", () => {
  it("busy: подпись и aria-busy; ошибка скрывает скелетон и показывает баннер", () => {
    const v = createResultView();
    v.setBusy(true, "Сервер занят, жду… попытка 2/3");
    expect(v.el.getAttribute("aria-busy")).toBe("true");
    expect(v.el.querySelector(".busy-pill")!.textContent).toContain("попытка 2/3");
    v.setBusy(false);
    expect(v.el.getAttribute("aria-busy")).toBe("false");
    v.setError({ message: "boom", code: "network", status: 0, extra: {}, name: "ApiError", silent: false } as never, () => {});
    expect(v.el.classList.contains("has-error")).toBe(true);
    expect(v.el.querySelector(".banner.error")).not.toBeNull();
  });
});

describe("resultview: живая область только на короткой строке статуса (F8)", () => {
  it("корень результата не aria-live; единственная живая область — маленькая sr-only строка", () => {
    const v = createResultView();
    v.setData(result());
    expect(v.el.hasAttribute("aria-live")).toBe(false);
    const live = [...v.el.querySelectorAll("[aria-live]")];
    expect(live).toHaveLength(1);
    expect(live[0]!.getAttribute("role")).toBe("status");
    expect(live[0]!.classList.contains("sr-only")).toBe(true);
    expect(live[0]!.querySelector("*")).toBeNull();                 // только текст, без вложенной разметки результата
  });

  it("busy → «Считаю…», готово → одна фраза с итогом (коротко), снятие busy очищает строку", () => {
    const v = createResultView();
    const st = () => v.el.querySelector<HTMLElement>("[role=status][aria-live=polite]")!;
    v.setBusy(true);
    expect(st().textContent).toBe("Считаю…");
    v.setBusy(true, "Сервер занят, жду… попытка 2/3");
    expect(st().textContent).toContain("попытка 2/3");
    v.setBusy(false);
    expect(st().textContent).toBe("");
    v.setData(result());
    expect(st().textContent).toMatch(/^Результат обновлён: доходность \+42%, макс\. просадка −12,0%, сделок 4$/);
    expect(st().textContent!.length).toBeLessThan(120);
  });

  it("строка статуса не скрывается в начальном состоянии (display:none не озвучивается)", () => {
    // vitest подменяет импорты .css пустышкой, а типов node нет — читаем исходник через process.getBuiltinModule (cwd = web/)
    const proc = (globalThis as unknown as { process: { cwd(): string; getBuiltinModule(n: string): unknown } }).process;
    const fs = proc.getBuiltinModule("node:fs") as { readFileSync(p: string, enc: string): string };
    const css = fs.readFileSync(`${proc.cwd()}/src/styles/components.css`, "utf8");
    expect(css).toMatch(/\.result\.is-initial > [^{]*:not\(\.rv-status\)[^{]*\{ display: none/);
  });
});
