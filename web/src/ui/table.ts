// Сортируемая таблица с ограничением числа отображаемых строк.
import { h } from "./dom";

interface Column<T> {
  key: string;
  label: string;
  /** Значение для сортировки. */
  value(row: T): number | string | null;
  /** Текст ячейки (по умолчанию String(value)). */
  render?(row: T): string;
  /** Готовый узел ячейки (бейдж, значок); приоритетнее render. */
  node?(row: T): Node | null;
  /** CSS-класс ячейки (например, pos/neg). */
  cls?(row: T): string;
  align?: "left" | "right";
  title?: string;
}

interface TableOpts<T> {
  columns: Column<T>[];
  sort?: { key: string; dir: 1 | -1 };
  limit?: number;
  rowKey?(row: T): string;
  /** CSS-класс строки (например, tr-win / tr-loss). */
  rowCls?(row: T): string;
  onRowClick?(row: T): void;
  isSelected?(row: T): boolean;
  empty?: string;
  caption?: string;
  /** Раскрывающаяся строка под строкой данных (пример — сделка и её fills): показывается, если open(row); состояние хранит вызывающий, затем refresh(). */
  expand?: { open(row: T): boolean; render(row: T): Node };
}

const FOCUSABLE = "a[href], button, input, select, textarea, [tabindex]";

/** Что было в фокусе внутри tbody: строка (по ключу data-key или позиции), индекс элемента внутри строки (0 — сама строка), раскрытая ли часть. */
interface FocusMark { rowKey: string | null; pos: number; inner: number; expansion: boolean }

export function dataTable<T>(o: TableOpts<T>) {
  let rows: T[] = [];
  let sort = o.sort ?? { key: o.columns[0]!.key, dir: 1 as 1 | -1 };
  let sorted: T[] | null = null;      // отсортированная копия: пересобирается только при смене строк/сортировки, не на «Показать ещё»
  let shown = o.limit ?? 500;
  let emptyText = o.empty ?? "Нет данных";
  const limit = shown;
  const thead = h("thead");
  const tbody = h("tbody");
  const more = h("button", { class: "btn ghost", type: "button" });
  const wrap = h("div", { class: "table-wrap", tabindex: 0, role: "region", "aria-label": o.caption ?? "Таблица" },
    h("table", { class: "data" }, o.caption ? h("caption", { class: "sr-only", text: o.caption }) : null, thead, tbody), more);

  const head = () => {
    thead.replaceChildren(h("tr", null, ...o.columns.map((c) => {
      const active = sort.key === c.key;
      return h("th", {
        class: c.align === "left" ? "left" : "", title: c.title ?? "", scope: "col",
        "aria-sort": active ? (sort.dir === 1 ? "ascending" : "descending") : "none",
      }, h("button", {
        type: "button", class: "th-btn", "data-col": c.key, onClick: () => {
          const hadFocus = thead.contains(document.activeElement);
          sort = { key: c.key, dir: active ? (sort.dir === 1 ? -1 : 1) : -1 };
          if (c.key === o.columns[0]!.key && !active) sort.dir = 1;
          shown = limit;
          sorted = null;
          head();
          body();
          // шапка пересобрана — клавиатурный фокус не должен падать на body (иначе Tab начинается с начала страницы)
          if (hadFocus) Array.from(thead.querySelectorAll<HTMLElement>("button.th-btn")).find((b) => b.dataset.col === c.key)?.focus({ preventScroll: true });
        },
      }, c.label, h("span", { class: "arrow", "aria-hidden": "true", text: active ? (sort.dir === 1 ? "▲" : "▼") : "" })));
    })));
  };

  const dataRows = (): HTMLElement[] => Array.from(tbody.children).filter((r) => !r.classList.contains("row-expansion")) as HTMLElement[];

  /** Запоминает позицию фокуса внутри tbody (null — фокус вне тела таблицы). */
  const markFocus = (): FocusMark | null => {
    const ae = document.activeElement as HTMLElement | null;
    if (!ae || !tbody.contains(ae)) return null;
    const row = ae.closest("tr");
    if (!row || row.parentElement !== tbody) return null;
    const expansion = row.classList.contains("row-expansion");
    const tr = (expansion ? row.previousElementSibling : row) as HTMLElement | null;
    if (!tr) return null;
    const list = [row, ...Array.from(row.querySelectorAll<HTMLElement>(FOCUSABLE))];
    return { rowKey: tr.dataset.key ?? null, pos: dataRows().indexOf(tr), inner: Math.max(0, list.indexOf(ae)), expansion };
  };
  /** Возвращает фокус на ту же строку (по ключу, иначе по позиции) и тот же элемент в ней; строка исчезла — на саму таблицу. */
  const restoreFocus = (m: FocusMark) => {
    const rows = dataRows();
    const tr = m.rowKey != null ? rows.find((r) => r.dataset.key === m.rowKey) : rows[m.pos];
    const next = tr && m.expansion ? tr.nextElementSibling : null;
    const scope = (next && next.classList.contains("row-expansion") ? next : tr) as HTMLElement | undefined;
    const list = scope ? [scope, ...Array.from(scope.querySelectorAll<HTMLElement>(FOCUSABLE))] : [];
    const target = list[Math.min(m.inner, list.length - 1)];
    (target && target.tabIndex >= 0 ? target : wrap).focus({ preventScroll: true });
  };

  const body = () => {
    const mark = markFocus();
    const col = o.columns.find((c) => c.key === sort.key) ?? o.columns[0]!;
    const all = sorted ??= [...rows].sort((a, b) => {
      const x = col.value(a), y = col.value(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;          // пустые всегда внизу
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
    const vis = all.slice(0, shown);
    if (!vis.length) {
      tbody.replaceChildren(h("tr", null, h("td", { colspan: o.columns.length, class: "empty", text: emptyText })));
    } else {
      tbody.replaceChildren(...vis.map((r) => {
        const tr = h("tr", {
          class: [o.onRowClick ? "clickable" : "", o.rowCls?.(r) ?? ""].join(" ").trim(),
          tabindex: o.onRowClick ? 0 : undefined,
          "aria-selected": o.isSelected ? String(o.isSelected(r)) : undefined,
          "data-key": o.rowKey?.(r),
        }, ...o.columns.map((c) => h("td", {
          class: [c.align === "left" ? "left" : "", c.cls?.(r) ?? ""].join(" ").trim(),
          text: c.node ? undefined : c.render ? c.render(r) : String(c.value(r) ?? "—"),
        }, c.node?.(r) ?? null)));
        if (o.onRowClick) {
          tr.addEventListener("click", () => o.onRowClick!(r));
          tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); o.onRowClick!(r); } });
        }
        if (!o.expand?.open(r)) return tr;
        return [tr, h("tr", { class: "row-expansion" }, h("td", { colspan: o.columns.length, class: "left" }, o.expand.render(r)))];
      }).flat());
    }
    const left = Math.max(0, all.length - shown);
    more.hidden = left === 0;
    more.textContent = `Показать ещё ${Math.min(limit, left)} (осталось ${left})`;
    more.onclick = () => {
      const had = document.activeElement === more;
      const first = shown;
      shown += limit;
      body();
      // кнопка могла исчезнуть (всё показано) — фокус на первую добавленную строку, а не на body
      if (had && more.hidden) {
        const added = dataRows()[first];
        (added && added.tabIndex >= 0 ? added : wrap).focus({ preventScroll: true });
      }
    };
    if (mark) restoreFocus(mark);
  };

  head();
  return {
    el: wrap,
    setRows(r: T[]) { rows = r; sorted = null; shown = limit; body(); },
    setEmpty(text: string) { emptyText = text; },
    refresh() { sorted = null; body(); },       // строки могли измениться на месте — пересортируем
  };
}
