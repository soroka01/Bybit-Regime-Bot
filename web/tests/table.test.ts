// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { dataTable } from "../src/ui/table";

describe("dataTable: рендер только страницы, сортировка не пересобирается", () => {
  const rows = Array.from({ length: 1000 }, (_, i) => ({ id: i, v: (i * 7919) % 1000 }));
  const make = () => {
    let sortCalls = 0;
    const t = dataTable<{ id: number; v: number }>({
      columns: [
        { key: "id", label: "id", value: (r) => r.id, render: (r) => String(r.id) },
        { key: "v", label: "v", value: (r) => { sortCalls++; return r.v; }, render: (r) => String(r.v) },
      ],
      sort: { key: "v", dir: -1 }, limit: 100,
    });
    t.setRows(rows);
    return { t, calls: () => sortCalls };
  };

  it("в DOM только видимая страница; «Показать ещё» добавляет страницу без пересортировки", () => {
    const { t, calls } = make();
    const trs = () => t.el.querySelectorAll("tbody tr").length;
    expect(trs()).toBe(100);
    const afterSort = calls();
    expect(afterSort).toBeGreaterThan(0);
    t.el.querySelector<HTMLButtonElement>("button.btn")!.click();
    expect(trs()).toBe(200);
    expect(calls()).toBe(afterSort);                    // value() для сортировки больше не вызывался
  });

  it("смена сортировки и новые строки пересортировывают; порядок корректен", () => {
    const { t, calls } = make();
    const first = () => t.el.querySelector("tbody tr td:nth-child(2)")!.textContent;
    expect(first()).toBe("999");                       // dir -1 по v
    const c0 = calls();
    t.el.querySelectorAll<HTMLButtonElement>("th button")[1]!.click();   // тот же столбец — меняем направление
    expect(first()).toBe("0");
    expect(calls()).toBeGreaterThan(c0);
    t.setRows(rows.slice(0, 5));
    expect(t.el.querySelectorAll("tbody tr").length).toBe(5);
  });
});
