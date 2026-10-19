// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { spark } from "../src/tabs/lbCard";
import type { LbItem } from "../src/types";

const item = (curve: { t: number[]; equity: (number | null)[]; bh: (number | null)[] } | undefined) =>
  ({ strategy: "A", desc: "", derivative: false, funding_included: false, result: { curve } }) as unknown as LbItem;

describe("spark: кривая с null (NaN на сервере)", () => {
  it("null-точки пропускаются, в points нет NaN", () => {
    const svg = spark(item({ t: [1, 2, 3, 4], equity: [1, null, 1.2, 1.1], bh: [1, 1.01, null, 1.05] }))!;
    const pts = [...svg.querySelectorAll("polyline")].map((p) => p.getAttribute("points")!);
    expect(pts.length).toBe(2);
    for (const p of pts) expect(p).not.toMatch(/NaN|undefined|Infinity/);
    expect(pts[1]!.split(" ").length).toBe(3);               // equity: 3 валидные точки из 4
  });
  it("полностью пустая кривая и отсутствие кривой -> null без исключений", () => {
    expect(spark(item({ t: [1, 2, 3], equity: [null, null, null], bh: [null, null, null] }))).toBeNull();
    expect(spark(item(undefined))).toBeNull();
    expect(spark({ strategy: "A", result: null } as unknown as LbItem)).toBeNull();
  });
});
