// @vitest-environment jsdom
// Графики: токены (cssVar → getComputedStyle) читаются на перерисовку, а не на столбец/ряд; mousemove по полосам перерисовывает только слой наведения.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { barChart } from "../src/charts/bars";
import { histogramChart } from "../src/charts/histogram";
import { signalStrips } from "../src/charts/strips";
import { histModel } from "../src/charts/histModel";

beforeEach(() => {
  document.body.replaceChildren();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const mount = (w = 640) => {
  const host = document.createElement("div");
  Object.defineProperty(host, "clientWidth", { configurable: true, value: w });
  document.body.append(host);
  return host;
};
const styleCalls = () => vi.spyOn(window, "getComputedStyle");

describe("barChart: токены читаются один раз на перерисовку", () => {
  const draw = (n: number, series = 1, bySign = true) => {
    const host = mount();
    const spy = styleCalls();
    const cats = Array.from({ length: n }, (_, i) => `c${i}`);
    barChart(host, () => ({
      categories: cats, fmt: (v) => String(v), bySign,
      series: Array.from({ length: series }, (_, k) => ({ name: `s${k}`, color: "--c1", values: cats.map((_, i) => (i % 2 ? -i : i)) })),
    }));
    const calls = spy.mock.calls.length;
    spy.mockRestore();
    expect(host.querySelectorAll("path.bar").length).toBe(n * series);
    return calls;
  };
  it("число обращений к getComputedStyle не растёт с числом столбцов", () => {
    expect(draw(60)).toBe(draw(3));
    expect(draw(60, 1, false)).toBe(draw(3, 1, false));
  });
  it("цвет столбца по знаку и цвет серии применяются как раньше", () => {
    document.documentElement.style.setProperty("--pos", "rgb(1, 2, 3)");
    document.documentElement.style.setProperty("--neg", "rgb(9, 8, 7)");
    const host = mount();
    barChart(host, () => ({ categories: ["a", "b"], fmt: String, bySign: true, series: [{ name: "x", color: "--c1", values: [1, -1] }] }));
    const fills = [...host.querySelectorAll("path.bar")].map((p) => p.getAttribute("fill"));
    expect(fills).toEqual(["rgb(1, 2, 3)", "rgb(9, 8, 7)"]);
    document.documentElement.style.removeProperty("--pos");
    document.documentElement.style.removeProperty("--neg");
  });
});

describe("histogramChart: токены корзин читаются один раз", () => {
  it("число обращений к getComputedStyle не зависит от числа корзин", () => {
    const run = (bins: number) => {
      const host = mount();
      const edges = Array.from({ length: bins + 1 }, (_, i) => i - Math.floor(bins / 2));
      const counts = Array.from({ length: bins }, (_, i) => i + 1);
      const spy = styleCalls();
      histogramChart(host, () => histModel({ edges, counts }));
      const n = spy.mock.calls.length;
      spy.mockRestore();
      expect(host.querySelectorAll("g.hbar").length).toBe(bins);
      return n;
    };
    expect(run(30)).toBe(run(4));
  });
});

describe("signalStrips: mousemove перерисовывает только слой наведения", () => {
  interface Ctx { calls: string[]; [k: string]: unknown }
  const ctxs = new WeakMap<HTMLCanvasElement, Ctx>();
  const fakeCtx = (): Ctx => {
    const calls: string[] = [];
    return new Proxy({ calls } as Ctx, {
      get: (t, p: string) => (p in t ? t[p] : p === "measureText" ? () => ({ width: 10 }) : (..._a: unknown[]) => { calls.push(p); }),
      set: (t, p: string, v) => { t[p] = v; return true; },
    });
  };
  beforeEach(() => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (this: HTMLCanvasElement) {
      if (!ctxs.has(this)) ctxs.set(this, fakeCtx());
      return ctxs.get(this) as unknown as CanvasRenderingContext2D;
    } as never);
  });

  function setup(rowsN = 3) {
    const host = mount(600);
    const s = signalStrips(host);
    const t = Array.from({ length: 50 }, (_, i) => 1_700_000_000 + i * 86400);
    const rows = Array.from({ length: rowsN }, (_, k) => ({ label: `Нога ${k}`, values: t.map((_, i) => ((i + k) % 3) - 1), max: 1 }));
    s.set(t, rows);
    const base = host.querySelector("canvas.strips") as HTMLCanvasElement;
    const over = host.querySelector("canvas.strips-overlay") as HTMLCanvasElement;
    base.getBoundingClientRect = () => ({ left: 0, top: 0, width: 600, height: 100, right: 600, bottom: 100, x: 0, y: 0, toJSON() {} });
    return { host, base, over, s, rows };
  }
  const move = (c: HTMLCanvasElement, x: number, y: number) => c.dispatchEvent(new MouseEvent("mousemove", { clientX: x, clientY: y, bubbles: true }));

  it("два слоя: основной и прозрачный наложенный; наложенный не перехватывает мышь (pointer-events в CSS) и скрыт от AT", () => {
    const { host, over } = setup();
    expect(host.querySelectorAll("canvas").length).toBe(2);
    expect(over.getAttribute("aria-hidden")).toBe("true");
    expect(over.getAttribute("role")).toBeNull();
  });

  it("mousemove не трогает основной холст, а на наложенном рисует одну вертикаль", () => {
    const { base, over, host } = setup();
    const b = ctxs.get(base)!, o = ctxs.get(over)!;
    const baseBefore = b.calls.length;
    o.calls.length = 0;
    move(base, 300, 10);
    expect(b.calls.length).toBe(baseBefore);                         // полосы не перерисованы
    expect(o.calls).toContain("clearRect");
    expect(o.calls.filter((c) => c === "fillRect")).toHaveLength(1);
    expect(host.textContent).toContain("Нога 0");                    // подпись под полосой обновилась
    expect(host.querySelector(".chart-tip")!.classList.contains("on")).toBe(true);
  });

  it("токены не читаются на mousemove (getComputedStyle не вызывается)", () => {
    const { base } = setup();
    const spy = styleCalls();
    for (let i = 0; i < 20; i++) move(base, 200 + i * 10, 10);
    expect(spy.mock.calls.length).toBe(0);
  });

  it("токены не читаются в цикле по ногам: число обращений при отрисовке не растёт с числом ног", () => {
    const count = (rowsN: number) => {
      const host = mount(600);
      const s = signalStrips(host);
      const spy = styleCalls();
      const t = [1, 2, 3];
      s.set(t, Array.from({ length: rowsN }, (_, k) => ({ label: k === 0 ? "Итого" : `L${k}`, values: [1, -1, 0], max: 1 })));
      const n = spy.mock.calls.length;
      spy.mockRestore();
      return n;
    };
    expect(count(12)).toBe(count(2));
  });

  it("выход курсора очищает слой наведения и подсказку", () => {
    const { base, over, host } = setup();
    move(base, 300, 10);
    const o = ctxs.get(over)!;
    o.calls.length = 0;
    base.dispatchEvent(new MouseEvent("mouseleave"));
    expect(o.calls).toContain("clearRect");
    expect(o.calls).not.toContain("fillRect");
    expect(host.querySelector(".chart-tip")!.classList.contains("on")).toBe(false);
    expect(host.textContent).toContain("Наведите курсор");
  });
});
