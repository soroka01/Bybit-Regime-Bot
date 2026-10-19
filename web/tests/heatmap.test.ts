// @vitest-environment jsdom
// Тепловая карта развёртки: ячейка остаётся ячейкой таблицы (без role=button на td), кликабельная кнопка — внутри; счётчик событий live.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { sweepHeatmap } from "../src/charts/heatmap";
import type { SweepResult } from "../src/types";
import { eventsCountText, EVENTS_LIMIT } from "../src/tabs/liveEvents";

const res = (): SweepResult => ({
  x: { name: "fast", values: [5, 10] }, y: { name: "slow", values: [20, 40] }, points: null, base: { fast: 10, slow: 40 },
  grids: { total_return: [[0.1, -0.2], [0.3, 0.05]] },
});

beforeEach(() => { document.body.replaceChildren(); });

describe("sweepHeatmap: семантика ячеек", () => {
  it("td без role/tabindex, внутри — button с aria-label; клик и Enter/пробел (нативно) применяют значения", () => {
    const onPick = vi.fn();
    const el = sweepHeatmap(res(), "total_return", { fast: 10, slow: 40 }, onPick);
    document.body.append(el);
    const tds = [...el.querySelectorAll<HTMLElement>("tbody td")];
    expect(tds).toHaveLength(4);
    for (const td of tds) {
      expect(td.getAttribute("role")).toBeNull();
      expect(td.hasAttribute("tabindex")).toBe(false);
      const b = td.querySelector("button.pick")!;
      expect(b.getAttribute("type")).toBe("button");
      expect(b.getAttribute("aria-label")).toMatch(/fast=\d+, slow=\d+: .+ — нажмите, чтобы применить/);
      expect(td.textContent).toBe(b.textContent);        // видимый текст ячейки сохранён
    }
    (tds[1]!.querySelector("button") as HTMLButtonElement).click();
    expect(onPick).toHaveBeenCalledWith(10, 20);
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(el.querySelectorAll("[role=button]").length).toBe(0);
  });

  it("лучшая и текущая ячейки помечены классами на td и упомянуты в aria-label кнопки", () => {
    const el = sweepHeatmap(res(), "total_return", { fast: 10, slow: 40 }, () => {});
    const best = el.querySelector<HTMLElement>("td.best")!;
    expect(best.querySelector("button")!.getAttribute("aria-label")).toContain("лучшая по метрике");
    const base = el.querySelector<HTMLElement>("td.base")!;
    expect(base.querySelector("button")!.getAttribute("aria-label")).toContain("текущая точка");
  });

  it("подсказка показывается по фокусу кнопки", () => {
    const el = sweepHeatmap(res(), "total_return", {}, () => {});
    document.body.append(el);
    const b = el.querySelector("td button") as HTMLButtonElement;
    const tip = () => el.querySelector(".chart-tip")!;
    expect(tip().classList.contains("on")).toBe(false);
    b.dispatchEvent(new Event("focus"));
    expect(tip().classList.contains("on")).toBe(true);
    expect(tip().textContent).toContain("fast=5 · slow=20");
    b.dispatchEvent(new Event("blur"));
    expect(tip().classList.contains("on")).toBe(false);
  });
});

describe("счётчик событий live", () => {
  const ev = (n: number, level = "info") => Array.from({ length: n }, (_, i) => ({ ts: `2026-10-08T12:${String(i % 60).padStart(2, "0")}:00Z`, level, text: `e${i}` }));
  it("до лимита — как раньше; сверх лимита честно говорит «последние 60 из N»", () => {
    expect(EVENTS_LIMIT).toBe(60);
    expect(eventsCountText([], "all")).toBe("");
    expect(eventsCountText(ev(5), "all")).toBe("5");
    expect(eventsCountText(ev(60), "all")).toBe("60");
    expect(eventsCountText(ev(100), "all")).toBe("последние 60 из 100");
    const mixed = [...ev(70, "warn"), ...ev(30, "info")];
    expect(eventsCountText(mixed, "alerts")).toBe("последние 60 из 70 (всего 100)");
    expect(eventsCountText([...ev(3, "warn"), ...ev(7, "info")], "alerts")).toBe("3 из 10");
  });
});
