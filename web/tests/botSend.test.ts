// «В бота»: исходы sendToBotWithToast (тосты), блокировка кнопки, одинаковый текст у «Копировать» и «В бота» во вкладках,
// а также карточка «Цель и сигналы» (активные ноги в таблице, неактивные — в свёрнутом списке).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sendButton, sendToBotWithToast } from "../src/ui/clipboard";
import { signalsCard } from "../src/tabs/liveView";
import type { LiveSnapshot, Meta } from "../src/types";

vi.mock("../src/tabs/resultview", () => ({
  createResultView: () => ({
    el: document.createElement("div"), slot: document.createElement("div"),
    setBusy() {}, setError() {}, setData() {}, destroy() {},
  }),
}));
vi.mock("../src/tabs/sweepPanel", () => ({
  createSweepPanel: () => ({ el: document.createElement("div"), inputsChanged() {}, clear() {}, fillAxes() {} }),
}));

const { createLab } = await import("../src/tabs/lab");
const { createLeaderboard } = await import("../src/tabs/leaderboard");
const { createPortfolio } = await import("../src/tabs/portfolio");

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const toasts = () => [...document.querySelectorAll(".toasts .toast")].map((t) => ({ kind: t.classList.contains("error") ? "error" : t.classList.contains("ok") ? "ok" : "info", text: t.textContent }));

type Call = { url: string; body: Record<string, unknown> };
/** fetch-заглушка: /api/bot/proposals отвечает `proposal`, остальное — `other` (по умолчанию пустой объект). */
function stub(proposal: () => Response, other: (url: string) => Response = () => json(200, {})): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, body: init.body ? JSON.parse(init.body as string) : {} });
    return url.includes("/api/bot/proposals") ? proposal() : other(url);
  }));
  return calls;
}
const backtest = () => json(200, { metrics: { total_return: 0.1, max_drawdown: -0.1, sharpe: 1.2, trades: 10 } });
const proposals = (calls: Call[]) => calls.filter((c) => c.url.includes("/api/bot/proposals"));
const okReply = (o: Record<string, unknown> = {}) => () => json(200, { ok: true, added: 1, errors: [], bot_alive: true, ...o });

beforeEach(() => {
  localStorage.clear(); document.body.replaceChildren();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("sendToBotWithToast: исходы", () => {
  it("ok и бот жив — зелёный тост; текст уходит в POST /api/bot/proposals как есть", async () => {
    const calls = stub(okReply());
    await sendToBotWithToast("Leg(...)");
    expect(proposals(calls)).toHaveLength(1);
    expect(proposals(calls)[0]!.body).toEqual({ text: "Leg(...)" });
    expect(toasts()).toEqual([{ kind: "ok", text: "Отправлено в Telegram — откройте уведомление и выберите вес" }]);
  });

  it("ok, но бот не отвечает — информационный тост", async () => {
    stub(okReply({ bot_alive: false }));
    await sendToBotWithToast("x");
    expect(toasts()).toEqual([{ kind: "info", text: "Предложение сохранено, но бот сейчас не отвечает — оно придёт в Telegram после запуска бота (хранится 24 ч)" }]);
  });

  it("частичный отказ: тост успеха и отдельный тост «Не принято»", async () => {
    stub(okReply({ errors: ["нога 2: нет такой стратегии", "нога 3: вес"] }));
    await sendToBotWithToast("x");
    const t = toasts();
    expect(t[0]!.kind).toBe("ok");
    expect(t[1]).toEqual({ kind: "error", text: "Не принято: нога 2: нет такой стратегии; нога 3: вес" });
  });

  it("ApiError (422 / 409 / 429): сообщение сервера в красном тосте", async () => {
    for (const [status, code, message] of [[422, "bad_request", "Не удалось разобрать текст"], [409, "bot_never_started", "Бот ещё не запускался"], [429, "too_many", "Слишком много неподтверждённых предложений"]] as const) {
      document.body.replaceChildren();
      stub(() => json(status, { detail: { code, message } }));
      await sendToBotWithToast("x");
      expect(toasts()).toEqual([{ kind: "error", text: message }]);
    }
  });

  it("сеть недоступна — красный тост; отменённый запрос тоста не даёт", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fail"); }));
    await sendToBotWithToast("x");
    expect(toasts()).toEqual([{ kind: "error", text: "сервер недоступен" }]);
    document.body.replaceChildren();
    vi.stubGlobal("fetch", vi.fn(async () => { throw Object.assign(new Error("a"), { name: "AbortError" }); }));
    await sendToBotWithToast("x");
    expect(toasts()).toEqual([]);
  });
});

describe("sendButton", () => {
  it("кнопка с иконкой бота и подсказкой; на время запроса заблокирована, двойной клик — один запрос", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const calls: Call[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ url, body: JSON.parse(init.body as string) });
      await gate;
      return json(200, { ok: true, added: 1, errors: [], bot_alive: true });
    }));
    let n = 0;
    const b = sendButton("В бота", () => `текст ${++n}`);
    document.body.append(b);
    expect(b.className).toBe("btn");
    expect(b.title).toContain("карточка придёт в Telegram");
    expect(b.querySelector("svg")).not.toBeNull();
    b.click(); b.click();
    await flush();
    expect(b.disabled).toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.body).toEqual({ text: "текст 1" });
    release(); await flush();
    expect(b.disabled).toBe(false);
    b.click(); await flush();
    expect(calls).toHaveLength(2);
  });
});

// ---------- вкладки: «Копировать для бота» и «В бота» дают один и тот же текст
const param = { min: 2, max: 50, step: 1, default: 12, kind: "int", grid: [5, 12, 20] };
const meta = {
  strategies: [{ name: "EMA cross", desc: "ema", derivative: false, params: { fast: param } }],
  timeframes: ["1h"], symbols: ["BTCUSDT"], available: { BTCUSDT: ["1h"] }, klines_available: { BTCUSDT: ["1h"] },
  leaderboard_tfs: ["1h"], portfolio_tfs: ["1h", "4h"], base_tf: "1h", timeframe_minutes: { "1h": 60 }, history_days: { "1h": 1095 },
  fetch_estimate: {}, walkforward_levs: [1],
  defaults: { fee: 0.00055, slippage: 0.0002, days: 365, leverage: 1 }, overlay_defaults: { htf: 50, stop: 3 },
  legs: [
    { strategy: "EMA cross", tf: "1h", params: { fast: 12 }, weight: 0.5, long_only: false },
    { strategy: "EMA cross", tf: "4h", params: { fast: 20 }, weight: 0.25, long_only: true },
  ],
} as unknown as Meta;
const btn = (el: HTMLElement, text: string) => [...el.querySelectorAll("button")].find((b) => b.textContent?.includes(text)) as HTMLButtonElement;

/** Перехватывает буфер обмена: возвращает массив скопированных текстов. */
function captureClipboard(): string[] {
  const copied: string[] = [];
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn(async (t: string) => { copied.push(t); }) } });
  return copied;
}

describe("копирование и отправка дают одинаковый текст", () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it("Лаборатория", async () => {
    const calls = stub(okReply(), backtest);
    const copied = captureClipboard();
    const tab = createLab(meta);
    tab.show();
    await vi.advanceTimersByTimeAsync(10); await flush();
    btn(tab.el, "Копировать для бота").click(); await flush();
    btn(tab.el, "В бота").click(); await flush();
    expect(copied).toHaveLength(1);
    expect(copied[0]).toContain("Leg(");
    expect(copied[0]).toContain("доход +10%");
    expect(proposals(calls)).toHaveLength(1);
    expect(proposals(calls)[0]!.body.text).toBe(copied[0]);
  });

  it("Портфель: обе кнопки в panel-bar, текст совпадает", async () => {
    const calls = stub(okReply(), backtest);
    const copied = captureClipboard();
    const tab = createPortfolio(meta);
    tab.show();
    await vi.advanceTimersByTimeAsync(10); await flush();
    const bar = tab.el.querySelector(".panel-bar") as HTMLElement;
    expect(bar.contains(btn(tab.el, "Копировать для бота"))).toBe(true);
    expect(bar.contains(btn(tab.el, "В бота"))).toBe(true);
    btn(tab.el, "Копировать для бота").click(); await flush();
    btn(tab.el, "В бота").click(); await flush();
    expect(copied[0]).toContain("Портфель: BTCUSDT");
    expect(proposals(calls)[0]!.body.text).toBe(copied[0]);
  });

  it("Рейтинг: кнопка «В бота» на карточке", async () => {
    const plan = { symbol: "BTCUSDT", tf: "1h", klines: true, derivs: false, strategies: [{ name: "A", desc: "a", derivative: false, ready: true, reason: "" }] };
    const item = {
      strategy: "A", desc: "a", derivative: false, funding_included: true, data_last: "2026-01-01T00:00:00+00:00", computed_at: "2026-01-01T00:00:00+00:00",
      result: { verdict: "robust", params: { n: 1 }, oos: { calmar: 1, total_return: 0.1, max_drawdown: -0.1, trades: 9 }, is: { total_return: 0.2 }, score: 1, robust_score: 1, full: {} },
    };
    const calls = stub(okReply(), () => json(200, { items: [item], missing: [], plan, days_effective: 730, data_last: "2026-01-01T00:00:00+00:00", fingerprint: "x" }));
    const copied = captureClipboard();
    location.hash = "#/best";
    const tab = createLeaderboard(meta);
    tab.show();
    await flush();
    const card = tab.el.querySelector(".lb-card:not(.compact)") as HTMLElement;
    expect(card).not.toBeNull();
    btn(card, "Копировать для бота").click(); await flush();
    btn(card, "В бота").click(); await flush();
    expect(copied[0]).toContain("Стратегия: A");
    expect(proposals(calls)).toHaveLength(1);
    expect(proposals(calls)[0]!.body.text).toBe(copied[0]);
  });
});

// ---------- «Бот (live)»: карточка «Цель и сигналы»
type SnapLeg = NonNullable<LiveSnapshot["legs"]>[number];
const leg = (o: Partial<SnapLeg>): SnapLeg => ({ key: "k", strategy: "EMA cross", tf: "4h", params: { fast: 10 }, long_only: false, weight: 0.5, enabled: true, signal: 1, updated: null, ...o });
const NOW = Date.parse("2026-10-08T12:00:00Z");
const card = (legs: SnapLeg[], target: number | undefined = 0.5) => signalsCard({ available: true, legs, target_exposure: target }, null, NOW);

describe("signalsCard: активные и неактивные ноги", () => {
  const legs = [
    leg({ key: "a", strategy: "A", weight: 0.6, signal: 1 }),
    leg({ key: "b", strategy: "B", tf: "1h", weight: 0.4, signal: -1 }),
    leg({ key: "c", strategy: "C", weight: 0, signal: 1, params: { n: 5 } }),
    leg({ key: "d", strategy: "D", weight: 0.3, enabled: false }),
  ];

  it("в основной таблице только активные; Σ в подвале — по ним", () => {
    const el = card(legs);
    const rows = [...el.querySelectorAll("ul.lv-legs > li.lv-leg:not(.head)")].map((r) => r.querySelector(".lv-leg-name b")!.textContent);
    expect(rows).toEqual(["A", "B"]);
    const foot = el.querySelector(".lv-foot")!.textContent!;
    expect(foot).toContain("Σ весов 100%");          // 60 + 40, а не 130 с выключенной
    expect(foot).toContain("Σ вкладов +0,20");        // 0.6 − 0.4
    expect(foot).toContain("цель +0,50×");
  });

  it("неактивные — в свёрнутом details.lv-idle со счётчиком, бейджами и параметрами", () => {
    const d = card(legs).querySelector("details.lv-idle") as HTMLDetailsElement;
    expect(d).not.toBeNull();
    expect(d.open).toBe(false);
    expect(d.querySelector("summary")!.textContent).toBe("Неактивные ноги (2)");
    const items = [...d.querySelectorAll("ul li")];
    expect(items.map((li) => li.querySelector("b")!.textContent)).toEqual(["C", "D"]);
    expect(items[0]!.querySelector(".chip-tf")!.textContent).toBe("4h");
    const badge = (i: number) => items[i]!.querySelector(".badge")!;
    expect(badge(0).textContent).toBe("не торгуется");
    expect(badge(0).getAttribute("title")).toContain("Вес 0");
    expect(badge(1).textContent).toBe("выключена");
    expect(badge(1).getAttribute("title")).toBe("Нога выключена");
    expect(items[0]!.querySelector(".mono")!.textContent).toBe("n=5");
  });

  it("раскрытое состояние переживает перерисовку: его хранит вызывающий (иначе блок сворачивался бы на каждом автообновлении)", () => {
    const st = { open: false, onToggle(o: boolean) { st.open = o; } };
    const draw = () => signalsCard({ available: true, legs, target_exposure: 0.5 }, null, NOW, st);
    const first = draw().querySelector("details.lv-idle") as HTMLDetailsElement;
    first.open = true;
    first.dispatchEvent(new Event("toggle"));
    expect(st.open).toBe(true);
    expect((draw().querySelector("details.lv-idle") as HTMLDetailsElement).open).toBe(true);     // новый DOM, но раскрыт
    const again = draw().querySelector("details.lv-idle") as HTMLDetailsElement;
    again.open = false;
    again.dispatchEvent(new Event("toggle"));
    expect((draw().querySelector("details.lv-idle") as HTMLDetailsElement).open).toBe(false);
  });

  it("нет неактивных — блока details нет", () => {
    expect(card([leg({ strategy: "A" })]).querySelector("details.lv-idle")).toBeNull();
  });

  it("нет активных — вместо таблицы сообщение, подвал с Σ не рисуется; неактивные остаются", () => {
    const el = card([leg({ weight: 0 }), leg({ enabled: false })], 0);
    expect(el.querySelector("ul.lv-legs")).toBeNull();
    expect(el.querySelector(".lv-foot")).toBeNull();
    expect(el.textContent).toContain("Нет работающих стратегий — целевая экспозиция 0, бот вне рынка.");
    expect(el.querySelector("summary")!.textContent).toBe("Неактивные ноги (2)");
  });

  it("нет данных о ногах вообще — прежняя заглушка", () => {
    const el = card([]);
    expect(el.textContent).toContain("Нет данных о ногах.");
    expect(el.querySelector("details.lv-idle")).toBeNull();
  });

  it("подсказка о добавлении стратегии под карточкой", () => {
    const hint = card([leg({})]).querySelector(".lv-hint")!;
    expect(hint.classList.contains("muted")).toBe(true);
    expect(hint.textContent).toContain("Telegram → 🧩 Стратегии → ➕ Добавить");
    expect(hint.textContent).toContain("«В бота» в Лаборатории, Рейтинге и Портфеле");
  });
});
