// Контраст текстовых токенов (WCAG 2.x) считается прямо по styles/tokens.css: --fg, --fg-muted и --fg-subtle ≥ 4,5:1 на всех поверхностях обеих тем.
import { describe, expect, it } from "vitest";
import { contrast } from "../src/charts/colors";

// vitest подменяет импорты .css пустышкой, а типов node нет — читаем исходник через process.getBuiltinModule (cwd = web/)
const proc = (globalThis as unknown as { process: { cwd(): string; getBuiltinModule(n: string): unknown } }).process;
const css = (proc.getBuiltinModule("node:fs") as { readFileSync(p: string, enc: string): string }).readFileSync(`${proc.cwd()}/src/styles/tokens.css`, "utf8").replace(/\r\n/g, "\n");

/** Токены блока темы: от заголовка селектора до закрывающей скобки в начале строки. */
function block(selector: string): Record<string, string> {
  const start = css.indexOf(selector + " {");
  expect(start, selector).toBeGreaterThanOrEqual(0);
  const body = css.slice(start, css.indexOf("\n}", start));
  return Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1]!, m[2]!.trim().replace(/\s*\/\*.*$/, "")]));
}

const THEMES = { dark: block(':root, :root[data-theme="dark"]'), light: block(':root[data-theme="light"]') };
const SURFACES = ["--bg", "--surface-1", "--surface-2", "--surface-3", "--surface-inset"];
const TEXT = ["--fg", "--fg-muted", "--fg-subtle"];

describe("контраст текстовых токенов", () => {
  for (const [theme, t] of Object.entries(THEMES)) {
    for (const fg of TEXT) {
      for (const bg of SURFACES) {
        it(`${theme}: ${fg} на ${bg} ≥ 4,5:1`, () => {
          expect(t[fg], fg).toMatch(/^#[0-9a-f]{6}$/i);
          expect(t[bg], bg).toMatch(/^#[0-9a-f]{6}$/i);
          expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
        });
      }
    }
  }

  it("иерархия сохранена: --fg-subtle не контрастнее --fg-muted ни на одной поверхности", () => {
    for (const t of Object.values(THEMES)) for (const bg of SURFACES) expect(contrast(t["--fg-subtle"]!, t[bg]!)).toBeLessThanOrEqual(contrast(t["--fg-muted"]!, t[bg]!) + 1e-9);
  });
});
