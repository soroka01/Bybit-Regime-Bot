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

describe("dataTable: клавиатурный фокус переживает пересборку", () => {
  interface R { id: string; v: number }
  const mk = (extra: Record<string, unknown> = {}) => {
    const clicks: string[] = [];
    const t = dataTable<R>({
      columns: [
        { key: "id", label: "id", value: (r) => r.id, render: (r) => r.id },
        { key: "v", label: "v", value: (r) => r.v, render: (r) => String(r.v) },
        { key: "act", label: "act", value: () => null, node: (r) => { const b = document.createElement("button"); b.type = "button"; b.textContent = "x" + r.id; return b; } },
      ],
      sort: { key: "v", dir: -1 }, rowKey: (r) => r.id, onRowClick: (r) => clicks.push(r.id), limit: 3, ...extra,
    });
    document.body.replaceChildren(t.el);
    return { t, clicks };
  };
  const rows = (): R[] => [{ id: "a", v: 1 }, { id: "b", v: 5 }, { id: "c", v: 3 }, { id: "d", v: 9 }, { id: "e", v: 7 }];
  const tr = (t: { el: HTMLElement }, key: string) => [...t.el.querySelectorAll<HTMLElement>("tbody tr")].find((r) => r.dataset.key === key)!;
  const active = () => document.activeElement as HTMLElement;

  it("сортировка по Enter/клику: фокус остаётся на кнопке того же столбца в новой шапке", () => {
    const { t } = mk();
    t.setRows(rows());
    const btn = () => [...t.el.querySelectorAll<HTMLButtonElement>("th button")].find((b) => b.dataset.col === "v")!;
    const old = btn();
    old.focus();
    expect(active()).toBe(old);
    old.click();
    expect(old.isConnected).toBe(false);          // шапка пересобрана
    expect(active()).toBe(btn());
    expect(btn().closest("th")!.getAttribute("aria-sort")).toBe("ascending");
  });

  it("клик мышью по заголовку без фокуса в шапке фокус не крадёт", () => {
    const { t } = mk();
    t.setRows(rows());
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();
    t.el.querySelectorAll<HTMLButtonElement>("th button")[0]!.click();
    expect(active()).toBe(input);
  });

  it("строка в фокусе: refresh()/setRows() с изменившимися значениями возвращают фокус на ту же строку по ключу", () => {
    const { t } = mk({ limit: 10 });
    t.setRows(rows());
    tr(t, "c").focus();
    expect(active().dataset.key).toBe("c");
    t.setRows(rows().map((r) => (r.id === "c" ? { ...r, v: 100 } : r)));       // c уехала на первое место
    expect(active().dataset.key).toBe("c");
    expect(active().isConnected).toBe(true);
    expect(t.el.querySelector("tbody tr")!.getAttribute("data-key")).toBe("c");
    t.refresh();
    expect(active().dataset.key).toBe("c");
  });

  it("фокус на кнопке внутри ячейки возвращается на кнопку той же строки", () => {
    const { t } = mk({ limit: 10 });
    t.setRows(rows());
    const btn = tr(t, "b").querySelector("button")!;
    btn.focus();
    t.setRows(rows().map((r) => ({ ...r, v: r.v + 1 })));
    expect(active().tagName).toBe("BUTTON");
    expect(active().textContent).toBe("xb");
    expect(active().isConnected).toBe(true);
  });

  it("строка исчезла из набора — фокус уходит на саму таблицу, а не на body", () => {
    const { t } = mk({ limit: 10 });
    t.setRows(rows());
    tr(t, "b").focus();
    t.setRows(rows().filter((r) => r.id !== "b"));
    expect(active()).toBe(t.el);
  });

  it("без rowKey фокус остаётся на строке той же позиции", () => {
    const { t } = mk({ limit: 10, rowKey: undefined });
    t.setRows(rows());
    const second = t.el.querySelectorAll<HTMLElement>("tbody tr")[1]!;
    second.focus();
    t.setRows(rows().map((r) => ({ ...r, v: r.v * 2 })));
    expect(active()).toBe(t.el.querySelectorAll<HTMLElement>("tbody tr")[1]);
  });

  it("фокус вне таблицы не трогается", () => {
    const { t } = mk();
    t.setRows(rows());
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();
    t.refresh();
    expect(active()).toBe(input);
  });

  it("«Показать ещё»: пока кнопка есть — фокус на ней; когда все строки показаны и кнопка скрыта — на первой добавленной строке", () => {
    const { t } = mk({ limit: 3 });
    t.setRows(rows());                                           // 5 строк, лимит 3
    const more = t.el.querySelector<HTMLButtonElement>("button.btn")!;
    more.focus();
    more.click();
    expect(more.hidden).toBe(true);
    expect(active().dataset.key).toBeDefined();
    expect(t.el.querySelectorAll("tbody tr")).toHaveLength(5);
    expect(active()).toBe(t.el.querySelectorAll<HTMLElement>("tbody tr")[3]);
  });
});
