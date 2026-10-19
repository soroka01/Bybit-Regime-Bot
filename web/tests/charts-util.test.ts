// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { contrast, inkOn, luminance, mix, parseColor, withAlpha } from "../src/charts/colors";
import { niceScale } from "../src/charts/scale";
import { badge, deltaChip, emptyState, glyph, metricTile } from "../src/ui/kit";

describe("charts/colors", () => {
  it("разбирает hex и rgb() в старом и новом синтаксисе", () => {
    expect(parseColor("#3987e5")).toEqual([57, 135, 229, 1]);
    expect(parseColor("#fff")).toEqual([255, 255, 255, 1]);
    expect(parseColor("rgb(255 255 255 / 6%)")).toEqual([255, 255, 255, 0.06]);
    expect(parseColor("rgba(10, 20, 30, .5)")).toEqual([10, 20, 30, 0.5]);
    expect(parseColor("nonsense")).toBeNull();
  });
  it("withAlpha / mix", () => {
    expect(withAlpha("#ff0000", 0.5)).toBe("rgba(255,0,0,0.5)");
    expect(mix("#000000", "#ffffff", 0.5)).toBe("rgb(128,128,128)");
    expect(mix("#000000", "#ffffff", 5)).toBe("rgb(255,255,255)");
  });
  it("контраст WCAG и выбор читаемого текста", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 0);
    expect(luminance("#ffffff")).toBeCloseTo(1, 3);
    expect(inkOn("#101010", "#ffffff", "#000000")).toBe("#ffffff");
    expect(inkOn("#f0f0f0", "#ffffff", "#000000")).toBe("#000000");
  });
});

describe("charts/scale niceScale", () => {
  it("границы кратны шагу, ноль на делении", () => {
    const s = niceScale(-0.35, 0.11, 5);
    expect(s.ticks).toContain(0);
    expect(s.lo).toBeLessThanOrEqual(-0.35);
    expect(s.hi).toBeGreaterThanOrEqual(0.11);
    for (const t of s.ticks) expect(Math.abs(t / s.step - Math.round(t / s.step))).toBeLessThan(1e-9);
  });
  it("вырожденный и некорректный диапазон не ломаются", () => {
    expect(niceScale(5, 5).ticks.length).toBeGreaterThan(1);
    expect(niceScale(NaN, 1).ticks.length).toBeGreaterThan(1);
  });
});

describe("ui/kit", () => {
  it("metricTile: тон окрашивает значение и добавляет чип со стрелкой", () => {
    const t = metricTile({ label: "Доход", value: "+5%", tone: "pos", sub: "B&H" });
    expect(t.querySelector(".metric-value")!.className).toContain("pos");
    expect(t.querySelector(".delta.pos .g")!.textContent).toBe("▲");
    expect(metricTile({ label: "x", value: "1" }).querySelector(".delta")).toBeNull();
    expect(metricTile({ label: "x", value: "1", tone: "neg", delta: false }).querySelector(".delta")).toBeNull();
  });
  it("glyph / deltaChip / badge", () => {
    expect(glyph("pos")).toBe("▲");
    expect(glyph("neg")).toBe("▼");
    expect(glyph("")).toBe("");
    expect(deltaChip("neg", "−3%").textContent).toBe("▼−3%");
    const b = badge("ok", "ok", { glyph: "✓" });
    expect(b.className).toContain("has-glyph");
    expect(b.textContent).toBe("✓ok");
  });
  it("emptyState: иконка, заголовок и подсказка", () => {
    const e = emptyState("Пусто", "Нажмите кнопку", null, { big: true });
    expect(e.className).toContain("big");
    expect(e.querySelector("svg")).not.toBeNull();
    expect(e.textContent).toContain("Нажмите кнопку");
  });
});
