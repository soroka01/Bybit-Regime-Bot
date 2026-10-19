import { describe, expect, it } from "vitest";
import { applyVerdictFilter, countVerdicts, groupByVerdict, isVFilter, isWeak } from "../src/tabs/lbFilter";
import type { LbItem } from "../src/types";

const mk = (name: string, verdict: string) => ({ name, it: { strategy: name, result: { verdict } } as unknown as LbItem });
const list = [mk("a", "robust"), mk("b", "robust"), mk("c", "overfit"), mk("d", "few"), mk("e", "loss"), mk("f", "loss"), mk("g", "mystery")];

describe("фильтры рейтинга по вердикту", () => {
  it("счётчики", () => {
    expect(countVerdicts(list)).toEqual({ robust: 2, overfit: 1, few: 1, loss: 2, other: 1 });
    expect(countVerdicts([])).toEqual({ robust: 0, overfit: 0, few: 0, loss: 0, other: 0 });
  });
  it("явный фильтр оставляет один вердикт и сохраняет порядок", () => {
    expect(applyVerdictFilter(list, "loss", false).map((x) => x.name)).toEqual(["e", "f"]);
    expect(applyVerdictFilter(list, "robust", true).map((x) => x.name)).toEqual(["a", "b"]);
  });
  it("«скрыть слабые» убирает few и loss, неизвестные остаются", () => {
    expect(applyVerdictFilter(list, "", true).map((x) => x.name)).toEqual(["a", "b", "c", "g"]);
    expect(applyVerdictFilter(list, "", false).length).toBe(7);
  });
  it("явный фильтр по слабому вердикту главнее «скрыть слабые»", () => {
    expect(applyVerdictFilter(list, "few", true).map((x) => x.name)).toEqual(["d"]);
  });
  it("группировка по подряд идущим вердиктам", () => {
    expect(groupByVerdict(list).map((g) => [g.verdict, g.items.length])).toEqual([["robust", 2], ["overfit", 1], ["few", 1], ["loss", 2], ["mystery", 1]]);
  });
  it("проверки значений", () => {
    expect(isVFilter("")).toBe(true);
    expect(isVFilter("loss")).toBe(true);
    expect(isVFilter("x")).toBe(false);
    expect(isWeak("few")).toBe(true);
    expect(isWeak("robust")).toBe(false);
  });
});
