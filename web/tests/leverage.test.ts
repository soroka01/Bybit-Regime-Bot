// Плечо 1×…100×: нелинейная шкала слайдера, пресеты, подсказка о ликвидации и сам элемент управления (jsdom).
import { describe, expect, it, vi } from "vitest";
import {
  BOT_MAX_LEVERAGE, botLimitNote, clampLev, LEV_POS_MAX, LEV_PRESETS, LEV_STOPS, levFromPos, liqHintText, liqMovePct,
  MAX_LEVERAGE, posFromLev, riskLevel,
} from "../src/leverage";
import { leverageControl } from "../src/ui/levControl";

describe("шкала слайдера", () => {
  it("шаги: 0.5 до 5×, 1 до 10×, 5 до 25×, 25 до 100×", () => {
    expect([...LEV_STOPS]).toEqual([1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 6, 7, 8, 9, 10, 15, 20, 25, 50, 75, 100]);
    expect(LEV_POS_MAX).toBe(LEV_STOPS.length - 1);
    expect(LEV_STOPS[0]).toBe(1);
    expect(LEV_STOPS[LEV_POS_MAX]).toBe(MAX_LEVERAGE);
  });

  it("позиция <-> значение: круговое преобразование без потерь", () => {
    for (let p = 0; p <= LEV_POS_MAX; p++) expect(posFromLev(levFromPos(p))).toBe(p);
    for (const v of LEV_STOPS) expect(levFromPos(posFromLev(v))).toBe(v);
  });

  it("значения вне шкалы уходят на ближайшую отметку; позиции зажимаются", () => {
    expect(levFromPos(posFromLev(7.3))).toBe(7);
    expect(levFromPos(posFromLev(12))).toBe(10);
    expect(levFromPos(posFromLev(40))).toBe(50);
    expect(levFromPos(-5)).toBe(1);
    expect(levFromPos(999)).toBe(100);
    expect(levFromPos(NaN)).toBe(1);
  });

  it("пресеты [1,2,3,5,10,20,50,100] лежат ровно на отметках слайдера", () => {
    expect([...LEV_PRESETS]).toEqual([1, 2, 3, 5, 10, 20, 50, 100]);
    for (const p of LEV_PRESETS) expect(LEV_STOPS).toContain(p);
  });

  it("clampLev: границы 1…100, NaN -> 1, два знака", () => {
    expect(clampLev(0.2)).toBe(1);
    expect(clampLev(150)).toBe(100);
    expect(clampLev(NaN)).toBe(1);
    expect(clampLev(7.3333)).toBe(7.33);
  });
});

describe("подсказка о риске", () => {
  it("X = (1/плечо − MMR) · 100", () => {
    expect(liqMovePct(1)).toBeCloseTo(99.5, 9);
    expect(liqMovePct(3)).toBeCloseTo(32.833, 2);
    expect(liqMovePct(10)).toBeCloseTo(9.5, 9);
    expect(liqMovePct(50)).toBeCloseTo(1.5, 9);
    expect(liqMovePct(100)).toBeCloseTo(0.5, 9);
  });

  it("порог цвета: янтарный с 10×, красный с 25×", () => {
    expect(riskLevel(1)).toBe("ok");
    expect(riskLevel(9.99)).toBe("ok");
    expect(riskLevel(10)).toBe("warn");
    expect(riskLevel(24.99)).toBe("warn");
    expect(riskLevel(25)).toBe("danger");
    expect(riskLevel(100)).toBe("danger");
  });

  it("текст подсказки", () => {
    expect(liqHintText(100)).toMatch(/^ликвидация ≈ при движении против позиции на 0[.,]5 %$/);
    expect(liqHintText(3)).toMatch(/на 32[.,]8 %$/);
    expect(liqHintText(1)).toMatch(/на 99[.,]5 %$/);
  });

  it("«бот ограничен 3×» только выше потолка бота", () => {
    expect(BOT_MAX_LEVERAGE).toBe(3);
    expect(botLimitNote(3)).toBeNull();
    expect(botLimitNote(1)).toBeNull();
    expect(botLimitNote(3.5)).toBe("бот ограничен 3×");
    expect(botLimitNote(100)).toBe("бот ограничен 3×");
  });
});

describe("элемент управления плечом", () => {
  const make = (value = 1, botNote = true) => {
    const onInput = vi.fn();
    const c = leverageControl({ value, botNote, onInput });
    const range = c.el.querySelector("input[type=range]") as HTMLInputElement;
    const box = c.el.querySelector("input[type=number]") as HTMLInputElement;
    const chips = [...c.el.querySelectorAll(".lev-presets button")] as HTMLButtonElement[];
    const hint = c.el.querySelector(".lev-hint:not(.bot)") as HTMLElement;
    const bot = c.el.querySelector(".lev-hint.bot") as HTMLElement;
    return { c, onInput, range, box, chips, hint, bot };
  };

  it("пресеты: клик ставит значение, слайдер, число и подсказку", () => {
    const { onInput, range, box, chips, hint } = make();
    expect(chips.map((b) => b.textContent)).toEqual(["1×", "2×", "3×", "5×", "10×", "20×", "50×", "100×"]);
    chips.find((b) => b.textContent === "50×")!.click();
    expect(onInput).toHaveBeenLastCalledWith(50);
    expect(box.value).toBe("50");
    expect(levFromPos(+range.value)).toBe(50);
    expect(hint.className).toContain("danger");
    expect(hint.textContent).toMatch(/на 1[.,]5 %/);
    expect(chips.find((b) => b.textContent === "50×")!.getAttribute("aria-pressed")).toBe("true");
  });

  it("слайдер двигает значение по шкале; число вне шкалы сохраняется точным", () => {
    const { onInput, range, box, hint } = make(1);
    range.value = String(LEV_POS_MAX);
    range.dispatchEvent(new Event("input"));
    expect(onInput).toHaveBeenLastCalledWith(100);
    box.value = "12";
    box.dispatchEvent(new Event("input"));
    expect(onInput).toHaveBeenLastCalledWith(12);
    expect(box.value).toBe("12");                       // поле при вводе не перезаписывается
    expect(hint.className).toContain("warn");
    box.value = "500";
    box.dispatchEvent(new Event("change"));
    expect(onInput).toHaveBeenLastCalledWith(100);
    expect(box.value).toBe("100");
  });

  it("подсказка: зелёная/янтарная/красная; «бот ограничен 3×» при плече выше 3", () => {
    const { c, hint, bot } = make(3);
    expect(hint.className).toContain("ok");
    expect(bot.hidden).toBe(true);
    c.set(5);
    expect(bot.hidden).toBe(false);
    expect(bot.textContent).toBe("бот ограничен 3×");
    c.set(10);
    expect(hint.className).toContain("warn");
    c.set(25);
    expect(hint.className).toContain("danger");
  });

  it("botNote: false — заметки о боте нет", () => {
    const { c, bot } = make(1, false);
    c.set(50);
    expect(bot.hidden).toBe(true);
  });

  it("setDisabled (спот): всё заблокировано, вместо подсказки — пояснение; обратно значение возвращается", () => {
    const { c, onInput, range, box, chips, hint } = make(20);
    c.setDisabled(true);
    c.set(1);
    expect(range.disabled && box.disabled && chips.every((b) => b.disabled)).toBe(true);
    expect(hint.textContent).toBe("спот: плечо 1×, ликвидации нет");
    chips[3]!.click();                                    // заблокированный пресет не срабатывает
    expect(onInput).not.toHaveBeenCalled();
    c.setDisabled(false);
    c.set(20);
    expect(range.disabled || box.disabled).toBe(false);
    expect(hint.textContent).toMatch(/на 4[.,]5 %/);
  });
});
