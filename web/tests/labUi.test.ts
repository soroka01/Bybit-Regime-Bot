// Компоненты фазы 2b (jsdom): поиск стратегии (combobox), строка параметра (изменено / по умолчанию), секции панели с однострочной сводкой.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { combobox } from "../src/ui/combobox";
import { slider } from "../src/ui/controls";
import { panelSection } from "../src/ui/panelSection";
import { filterStrategies } from "../src/ui/strategySearch";

const items = [
  { value: "EMA cross", label: "EMA cross", desc: "Лонг когда быстрая EMA выше медленной" },
  { value: "Funding fade", label: "Funding fade", desc: "Против перекоса фандинга", tag: "деривативы" },
  { value: "OI flush", label: "OI flush", desc: "Вход после обвала открытого интереса", tag: "деривативы" },
];
const key = (el: HTMLElement, k: string) => el.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
const type = (input: HTMLInputElement, v: string) => { input.value = v; input.dispatchEvent(new Event("input", { bubbles: true })); };

beforeEach(() => { document.body.replaceChildren(); localStorage.clear(); });

describe("combobox: выбор стратегии с поиском", () => {
  const make = () => {
    const onChange = vi.fn();
    const c = combobox({
      label: "Стратегия", items, value: "EMA cross", onChange,
      filter: (list, q) => filterStrategies(list.map((i) => ({ name: i.label, desc: i.desc ?? "", derivative: !!i.tag, i })), q).map((x) => x.i),
    });
    document.body.append(c.el);
    const input = c.el.querySelector("input")!;
    const list = c.el.querySelector("[role=listbox]") as HTMLElement;
    return { c, input, list, onChange };
  };

  it("ARIA: поле — combobox, список закрыт; фокус открывает список со всеми вариантами и бейджем «деривативы»", () => {
    const { input, list } = make();
    expect(input.getAttribute("role")).toBe("combobox");
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect(list.hidden).toBe(true);
    expect(input.value).toBe("EMA cross");
    input.dispatchEvent(new Event("focus"));
    expect(input.getAttribute("aria-expanded")).toBe("true");
    expect(list.hidden).toBe(false);
    expect(list.querySelectorAll("[role=option]")).toHaveLength(3);
    expect(list.textContent).toContain("деривативы");
    expect(list.querySelector("[aria-selected=true]")!.textContent).toContain("EMA cross");
  });

  it("набор текста фильтрует; ↓ и Enter выбирают; onChange вызывается один раз", () => {
    const { input, list, onChange } = make();
    input.dispatchEvent(new Event("focus"));
    type(input, "f");
    expect([...list.querySelectorAll("[role=option]")].map((o) => o.querySelector(".co-name")!.textContent)).toEqual(["Funding fade", "OI flush"]);
    key(input, "ArrowDown");
    expect(input.getAttribute("aria-activedescendant")).toBe(list.querySelectorAll("[role=option]")[1]!.id);
    key(input, "Enter");
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("OI flush");
    expect(input.value).toBe("OI flush");
    expect(list.hidden).toBe(true);
    expect(input.getAttribute("aria-expanded")).toBe("false");
  });

  it("ничего не найдено — сообщение, Enter ничего не выбирает; Esc закрывает и возвращает название", () => {
    const { input, list, onChange } = make();
    input.dispatchEvent(new Event("focus"));
    type(input, "zzz");
    expect(list.textContent).toContain("Ничего не найдено");
    key(input, "Enter");
    expect(onChange).not.toHaveBeenCalled();
    key(input, "Escape");
    expect(list.hidden).toBe(true);
    expect(input.value).toBe("EMA cross");
  });

  it("↑ с первой записи переходит на последнюю; выбор текущей записи не вызывает onChange; set() меняет текст", () => {
    const { c, input, list, onChange } = make();
    input.dispatchEvent(new Event("focus"));
    key(input, "ArrowUp");
    expect(list.querySelectorAll("[role=option]")[2]!.classList.contains("is-active")).toBe(true);
    key(input, "Home");
    key(input, "Enter");                              // Home -> «EMA cross» (текущая)
    expect(onChange).not.toHaveBeenCalled();
    c.set("Funding fade");
    expect(input.value).toBe("Funding fade");
  });

  it("клик мышью по варианту выбирает его", () => {
    const { input, list, onChange } = make();
    input.dispatchEvent(new Event("focus"));
    list.querySelectorAll("[role=option]")[1]!.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    expect(onChange).toHaveBeenCalledWith("Funding fade");
  });
});

describe("slider: параметр с умолчанием", () => {
  const make = (value = 12) => {
    const onInput = vi.fn();
    const s = slider({ label: "fast", min: 3, max: 75, step: 1, value, def: 12, onInput });
    document.body.append(s.el);
    return { s, onInput, box: s.el.querySelector("input[type=number]") as HTMLInputElement, reset: s.el.querySelector(".def-btn") as HTMLButtonElement };
  };

  it("по умолчанию: не помечен, кнопка «по умолч.» неактивна, видны min · по умолч. · max", () => {
    const { s, reset } = make();
    expect(s.el.classList.contains("is-changed")).toBe(false);
    expect(reset.disabled).toBe(true);
    expect(s.el.querySelector(".field-range")!.textContent).toBe("3по умолч. 1275");
  });

  it("изменение помечает строку, «по умолч.» возвращает значение и вызывает onInput", () => {
    const { s, onInput, box, reset } = make();
    box.value = "20";
    box.dispatchEvent(new Event("input"));
    expect(onInput).toHaveBeenLastCalledWith(20);
    expect(s.el.classList.contains("is-changed")).toBe(true);
    expect((s.el.querySelector(".chg-dot") as HTMLElement).hidden).toBe(false);
    expect(reset.disabled).toBe(false);
    reset.click();
    expect(onInput).toHaveBeenLastCalledWith(12);
    expect(box.value).toBe("12");
    expect(s.el.classList.contains("is-changed")).toBe(false);
    expect(reset.disabled).toBe(true);
  });

  it("set() снаружи (клик по ячейке развёртки) тоже обновляет пометку; стартовое значение вне умолчания помечено сразу", () => {
    const { s } = make();
    s.set(30);
    expect(s.el.classList.contains("is-changed")).toBe(true);
    s.set(12);
    expect(s.el.classList.contains("is-changed")).toBe(false);
    expect(make(40).s.el.classList.contains("is-changed")).toBe(true);
  });

  it("слайдер без умолчания (комиссия) остаётся прежней разметки: без пометок и кнопки", () => {
    const s = slider({ label: "Комиссия", min: 0, max: 0.3, step: 0.005, value: 0.055, unit: "%", onInput: () => {} });
    expect(s.el.querySelector(".def-btn")).toBeNull();
    expect(s.el.classList.contains("param")).toBe(false);
    expect(s.el.querySelector('input[type="number"][aria-label="Комиссия"]')).not.toBeNull();
  });
});

describe("panelSection: сводка и запоминание состояния", () => {
  it("сводка обновляется; состояние сохраняется в localStorage и восстанавливается", () => {
    const a = panelSection({ key: "t.a", icon: "chart", title: "Рынок", summary: "BTCUSDT · 1h", body: [document.createElement("div")], open: false });
    document.body.append(a.el);
    expect(a.el.open).toBe(false);
    expect(a.el.querySelector(".ps-sum")!.textContent).toBe("BTCUSDT · 1h");
    a.setSummary("ETHUSDT · 4h");
    expect(a.el.querySelector(".ps-sum")!.textContent).toBe("ETHUSDT · 4h");
    a.el.open = true;
    a.el.dispatchEvent(new Event("toggle"));
    expect(localStorage.getItem("tb.disc.t.a")).toBe("1");
    const b = panelSection({ key: "t.a", icon: "chart", title: "Рынок", summary: "", body: [], open: false });
    expect(b.el.open).toBe(true);
  });
});
