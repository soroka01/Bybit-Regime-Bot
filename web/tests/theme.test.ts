// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, currentMode, resolve, storedMode, themeSwitch, THEME_COLORS } from "../src/ui/theme";

const mql = (dark: boolean) => vi.fn().mockImplementation(() => ({ matches: dark, addEventListener() {}, removeEventListener() {} }));

beforeEach(() => {
  localStorage.clear();
  document.head.innerHTML = '<meta name="color-scheme" content="dark"><meta name="theme-color" content="#000">';
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.themeMode;
  vi.stubGlobal("matchMedia", mql(true));
});
afterEach(() => { vi.unstubAllGlobals(); });

describe("тема", () => {
  it("по умолчанию — тёмная, если выбор не сохранён", () => {
    expect(storedMode()).toBe("dark");
    expect(resolve("dark")).toBe("dark");
  });

  it("applyTheme ставит атрибуты, meta color-scheme и theme-color, сохраняет выбор (включая auto)", () => {
    applyTheme("light", { persist: true, animate: false });
    const d = document.documentElement;
    expect(d.dataset.theme).toBe("light");
    expect(d.dataset.themeMode).toBe("light");
    expect(document.querySelector('meta[name="color-scheme"]')!.getAttribute("content")).toBe("light");
    expect(document.querySelector('meta[name="theme-color"]')!.getAttribute("content")).toBe(THEME_COLORS.light);
    expect(localStorage.getItem("tb.theme")).toBe("light");
    applyTheme("auto", { persist: true, animate: false });
    expect(localStorage.getItem("tb.theme")).toBe("auto");
    expect(d.dataset.themeMode).toBe("auto");
    expect(d.dataset.theme).toBe("dark");          // ОС тёмная
  });

  it("auto следует за ОС", () => {
    vi.stubGlobal("matchMedia", mql(false));
    expect(resolve("auto")).toBe("light");
  });

  it("повреждённое значение в localStorage даёт тёмную тему", () => {
    localStorage.setItem("tb.theme", "purple");
    expect(storedMode()).toBe("dark");
  });

  it("событие tb-theme отправляется при смене темы (графики перерисуются)", () => {
    const fn = vi.fn();
    window.addEventListener("tb-theme", fn);
    applyTheme("light", { animate: false });
    window.removeEventListener("tb-theme", fn);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("переключатель: три кнопки с aria-pressed, клик меняет тему и сохраняет", () => {
    applyTheme("dark", { animate: false });
    const el = themeSwitch();
    const btns = [...el.querySelectorAll("button")];
    expect(btns).toHaveLength(3);
    expect(btns.map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "false", "true"]);   // светлая, авто, тёмная
    btns[0]!.click();
    expect(currentMode()).toBe("light");
    expect(btns.map((b) => b.getAttribute("aria-pressed"))).toEqual(["true", "false", "false"]);
    expect(localStorage.getItem("tb.theme")).toBe("light");
  });
});
