// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import fixture from "./fixtures/rank.json";
import { isRank, rankKey, rankValue, RANKS, splitRanked } from "../src/tabs/lbRank";
import { canTransition, countMissing, createLbStore, initialModel, runDisabled, runLabel, statusText, type Entry } from "../src/tabs/lbState";
import { itemSig, compactInfo, type CardCtx } from "../src/tabs/lbCard";
import { patchKeyed } from "../src/ui/keyed";
import { tierOf, VERDICT_ORDER, verdictInfo } from "../src/ui/verdicts";
import { verdictBadge } from "../src/ui/modal";
import { fitDays, LB_DAYS } from "../src/tabs/tab";
import type { LbItem, Meta } from "../src/types";

const mkItem = (name: string, verdict: string, oos: Record<string, number | null>): LbItem =>
  ({ strategy: name, desc: "", derivative: false, funding_included: true, result: { verdict, oos, is: {}, full: {}, params: {}, score: null, robust_score: null } } as unknown as LbItem);

describe("ярусы вердиктов", () => {
  it("порядок robust > overfit > few > loss", () => {
    expect(VERDICT_ORDER).toEqual(["robust", "overfit", "few", "loss"]);
    expect(["loss", "few", "robust", "overfit"].sort((a, b) => tierOf(a) - tierOf(b))).toEqual(["robust", "overfit", "few", "loss"]);
  });
  it("неизвестный вердикт: ярус в конце и безопасный бейдж (не бросает)", () => {
    expect(tierOf("wat")).toBe(4);
    expect(tierOf(undefined)).toBe(4);
    expect(verdictInfo("toString").tone).toBe("off");     // не берём свойства прототипа
    expect(() => verdictBadge("wat" as never)).not.toThrow();
    expect(verdictBadge("robust").className).toContain("ok");
  });
});

describe("rankKey / rankValue", () => {
  it("ключи метрик", () => {
    expect(Object.fromEntries(RANKS.map((r) => [r.value, r.key]))).toEqual({ calmar: "calmar", sharpe: "sharpe", return: "total_return", dd: "max_drawdown" });
  });
  it("устаревший/неизвестный ранг не бросает и даёт Calmar", () => {
    expect(rankKey("zzz")).toBe("calmar");
    expect(rankKey(undefined)).toBe("calmar");
    expect(isRank("zzz")).toBe(false);
    expect(rankValue(mkItem("a", "robust", { calmar: 2 }), "zzz")).toBe(2);
  });
  it("rankValue: нет OOS/не число/нет сделок для dd -> null", () => {
    expect(rankValue(null, "calmar")).toBeNull();
    expect(rankValue({ result: null } as unknown as LbItem, "calmar")).toBeNull();
    expect(rankValue(mkItem("a", "robust", { calmar: null }), "calmar")).toBeNull();
    expect(rankValue(mkItem("a", "robust", { calmar: Infinity }), "calmar")).toBeNull();
    expect(rankValue(mkItem("a", "robust", { max_drawdown: -0.1, trades: 0 }), "dd")).toBeNull();
    expect(rankValue(mkItem("a", "robust", { max_drawdown: -0.1, trades: 4 }), "dd")).toBe(-0.1);
  });
});

describe("splitRanked: порядок равен серверному rank_items (fixture из Python)", () => {
  type FxItem = { name: string; verdict: string; oos: Record<string, number | null> };
  const items = fixture.items as FxItem[];
  const byName = new Map(items.map((i) => [i.name, mkItem(i.name, i.verdict, i.oos)]));
  const expected = fixture.expected as Record<string, string[]>;

  for (const crit of ["calmar", "sharpe", "return", "dd"] as const) {
    it(`критерий ${crit}`, () => {
      const order = items.map((i) => i.name);
      const { ranked, rest } = splitRanked(order, (n) => byName.get(n) ?? null, crit);
      // Python кладёт элементы без значения в конец яруса, TS выносит их в «остальные» (компактные карточки):
      // относительный порядок элементов со значением обязан совпадать.
      const hasValue = new Set(order.filter((n) => rankValue(byName.get(n)!, crit) != null));
      expect(ranked.map((r) => r.name)).toEqual(expected[crit]!.filter((n) => hasValue.has(n)));
      expect(rest.sort()).toEqual(order.filter((n) => !hasValue.has(n)).sort());
    });
  }

  it("ярусы идут строго robust, overfit, few, loss", () => {
    const { ranked } = splitRanked(items.map((i) => i.name), (n) => byName.get(n)!, "calmar");
    const tiers = ranked.map((r) => r.tier);
    expect([...tiers].sort((a, b) => a - b)).toEqual(tiers);
  });

  it("внутри яруса — по убыванию метрики; для dd — ближе к нулю лучше", () => {
    const a = mkItem("A", "robust", { max_drawdown: -0.3, trades: 5 });
    const b = mkItem("B", "robust", { max_drawdown: -0.05, trades: 5 });
    const c = mkItem("C", "loss", { max_drawdown: -0.01, trades: 5 });
    const { ranked } = splitRanked(["A", "B", "C"], (n) => ({ A: a, B: b, C: c })[n]!, "dd");
    expect(ranked.map((r) => r.name)).toEqual(["B", "A", "C"]);
  });

  it("стабильность: равные значения сохраняют исходный порядок", () => {
    const mk = (n: string) => mkItem(n, "robust", { calmar: 1 });
    const { ranked } = splitRanked(["x", "y", "z"], (n) => mk(n), "calmar");
    expect(ranked.map((r) => r.name)).toEqual(["x", "y", "z"]);
  });

  it("неизвестный вердикт не ломает сортировку", () => {
    const { ranked } = splitRanked(["a", "b"], (n) => mkItem(n, n === "a" ? "weird" : "robust", { calmar: 1 }), "calmar");
    expect(ranked.map((r) => r.name)).toEqual(["b", "a"]);
  });
});

describe("автомат состояний", () => {
  it("допустимые и недопустимые переходы", () => {
    expect(canTransition("idle", "loading")).toBe(true);
    expect(canTransition("idle", "running")).toBe(false);
    expect(canTransition("loading", "running")).toBe(false);
    expect(canTransition("cached", "running")).toBe(true);
    expect(canTransition("running", "cached")).toBe(true);
    expect(canTransition("running", "loading")).toBe(true);
    expect(canTransition("error", "running")).toBe(true);
  });

  it("set(): один вызов onChange на патч; недопустимый переход отвергается без изменений", () => {
    const onChange = vi.fn();
    const s = createLbStore(onChange);
    expect(s.set({ phase: "loading" })).toBe(true);
    expect(onChange).toHaveBeenCalledTimes(1);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(s.set({ phase: "running", total: 5 })).toBe(false);
    expect(s.model.phase).toBe("loading");
    expect(s.model.total).toBe(0);
    expect(onChange).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });

  it("исключение в отрисовке не ломает состояние (A1: нет зависания в running)", () => {
    const onRenderError = vi.fn();
    const s = createLbStore(() => { throw new Error("render"); }, onRenderError);
    s.set({ phase: "loading" });
    s.set({ phase: "cached" });
    expect(s.set({ phase: "running", total: 3 })).toBe(true);
    expect(s.set({ phase: "cached" })).toBe(true);
    expect(s.model.phase).toBe("cached");
    expect(onRenderError).toHaveBeenCalledTimes(4);
  });

  it("resetFor очищает данные под новые настройки", () => {
    const s = createLbStore(() => {});
    s.set({ phase: "loading" }); s.set({ phase: "cached", order: ["a"], readyCount: 3, missingCount: 1, note: "x" });
    s.model.entries.set("a", { k: "none" });
    s.resetFor();
    expect(s.model.phase).toBe("loading");
    expect(s.model.order).toEqual([]);
    expect(s.model.entries.size).toBe(0);
    expect(s.model.readyCount).toBe(0);
    expect(s.model.note).toBe("");
    expect(s.model.plan).toBeNull();
  });

  it("statusText / runLabel / runDisabled", () => {
    const m = initialModel();
    expect(statusText(m)).toBe("");
    expect(runLabel(m)).toBe("Запустить");
    Object.assign(m, { phase: "cached", readyCount: 10, missingCount: 0, dataLast: "2025-03-01T12:30:00+00:00" });
    expect(statusText(m)).toBe("Показано из кэша · данные от 2025-03-01 12:30 UTC");
    expect(runDisabled(m)).toBe(true);
    Object.assign(m, { missingCount: 4 });
    expect(statusText(m)).toBe("Из кэша: 6 из 10. Осталось досчитать: 4.");
    expect(runLabel(m)).toBe("Досчитать недостающие (4)");
    Object.assign(m, { missingCount: 10 });
    expect(statusText(m)).toMatch(/нажмите «Запустить»/);
    Object.assign(m, { phase: "running", done: 2, total: 10 });
    expect(statusText(m)).toBe("Считаю 3 из 10…");
    expect(runDisabled(m)).toBe(true);
    Object.assign(m, { retry: { k: 2, n: 4, name: "EMA", waitedS: 20 } });
    expect(statusText(m)).toContain("ожидаю сервер, попытка 2/4");
    Object.assign(m, { phase: "error", error: "ой" });
    expect(statusText(m)).toBe("ой");
    expect(statusText({ ...m, phase: "loading" })).toBe("Загрузка кэша…");
  });

  it("countMissing считает pending/none/error/busy", () => {
    const e = new Map<string, Entry>([["a", { k: "pending" }], ["b", { k: "none" }], ["c", { k: "error", error: "x" }], ["d", { k: "busy" }], ["e", { k: "skip", skip: "" }], ["f", { k: "item", item: {} as LbItem }]]);
    expect(countMissing(e)).toBe(4);
  });
});

describe("карточки и патчинг списка", () => {
  const ctx: CardCtx = { symbol: "BTCUSDT", tf: "1h", days: 730, longOnly: false, leverage: 1, market: "perp", feePct: 0.055, slipPct: 0.01, onOpenLab() {}, onCopy() {}, onSend() {} };
  it("compactInfo: все виды записей", () => {
    expect(compactInfo({ k: "busy" }).cls).toBe("failed");
    expect(compactInfo({ k: "pending" }).text).toBe("считаю…");
    expect(compactInfo(undefined).text).toBe("не рассчитано");
    expect(compactInfo({ k: "skip", skip: "нет OI" }).text).toBe("нет данных — нет OI");
  });
  it("itemSig меняется при смене настроек/ранга и не меняется иначе", () => {
    const it = mkItem("a", "robust", { calmar: 1, total_return: 0.1 });
    expect(itemSig(1, it, ctx)).toBe(itemSig(1, it, { ...ctx }));
    expect(itemSig(1, it, ctx)).not.toBe(itemSig(2, it, ctx));
    expect(itemSig(1, it, ctx)).not.toBe(itemSig(1, it, { ...ctx, days: 365 }));
  });

  it("patchKeyed переиспользует узлы с тем же ключом/подписью, пересоздаёт изменённые и переставляет", () => {
    const host = document.createElement("div");
    const build = (t: string) => () => Object.assign(document.createElement("p"), { textContent: t });
    patchKeyed(host, [{ key: "a", sig: "1", build: build("A") }, { key: "b", sig: "1", build: build("B") }, { key: "c", sig: "1", build: build("C") }]);
    const [a, b, c] = Array.from(host.children);
    patchKeyed(host, [{ key: "c", sig: "1", build: build("C!") }, { key: "a", sig: "2", build: build("A2") }, { key: "b", sig: "1", build: build("B!") }]);
    const now = Array.from(host.children);
    expect(now.map((n) => n.textContent)).toEqual(["C", "A2", "B"]);
    expect(now[0]).toBe(c);        // тот же узел
    expect(now[2]).toBe(b);
    expect(now[1]).not.toBe(a);    // подпись изменилась — новый узел
    patchKeyed(host, [{ key: "b", sig: "1", build: build("B!") }]);
    expect(host.children.length).toBe(1);
    expect(host.children[0]).toBe(b);
  });
});

describe("периоды и fitDays", () => {
  const meta = (cap: Record<string, number>) => ({ history_days: cap }) as unknown as Meta;
  it("рейтинг не получает значение вне своих вариантов (раньше для 1m получалось 90 и select был пустым)", () => {
    expect(fitDays(meta({ "1m": 90 }), "1m", 730, LB_DAYS)).toBe(180);
    expect(LB_DAYS).toContain(fitDays(meta({ "1m": 90 }), "1m", 730, LB_DAYS));
    expect(fitDays(meta({ "1h": 1095 }), "1h", 730, LB_DAYS)).toBe(730);
    expect(fitDays(meta({ "15m": 400 }), "15m", 1095, LB_DAYS)).toBe(365);
    expect(fitDays(meta({ "15m": 400 }), "15m", 500, LB_DAYS)).toBe(365);
  });
  it("общий список: подгоняет под предел", () => {
    expect(fitDays(meta({ "1m": 90 }), "1m", 730)).toBe(90);
    expect(fitDays(meta({ "1h": 1095 }), "1h", 365)).toBe(365);
  });
});
