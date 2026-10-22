/// <reference types="vite/client" />
// Слои фронтенда: charts/ и ui/ — нижние, они не импортируют из tabs/ (иначе общие помощники застревают во вкладках).
import { describe, expect, it } from "vitest";

const layers: Record<string, Record<string, string>> = {
  "src/charts": import.meta.glob("../src/charts/**/*.ts", { query: "?raw", import: "default", eager: true }),
  "src/ui": import.meta.glob("../src/ui/**/*.ts", { query: "?raw", import: "default", eager: true }),
};
// i18n — самый нижний слой: его импортируют все, он не импортирует ни ui/, ни charts/, ни tabs/
const lowest: Record<string, Record<string, string>> = {
  "src/i18n": import.meta.glob("../src/i18n/**/*.ts", { query: "?raw", import: "default", eager: true }),
};

const importsOf = (code: string): string[] => [...code.matchAll(/(?:import|export)\s[^;]*?from\s+"([^"]+)"/g)].map((m) => m[1]!);

describe("слои модулей", () => {
  for (const [layer, files] of Object.entries(lowest)) {
    it(`${layer} не импортирует из ui/, charts/ и tabs/`, () => {
      expect(Object.keys(files).length).toBeGreaterThan(5);
      const bad = Object.entries(files).flatMap(([f, code]) => importsOf(code).filter((s) => /(^|\/)(tabs|ui|charts)\//.test(s)).map((s) => `${f} -> ${s}`));
      expect(bad).toEqual([]);
    });
  }
  for (const [layer, files] of Object.entries(layers)) {
    it(`${layer} не импортирует из tabs/`, () => {
      expect(Object.keys(files).length).toBeGreaterThan(3);
      const bad = Object.entries(files).flatMap(([f, code]) => importsOf(code).filter((s) => /(^|\/)tabs\//.test(s)).map((s) => `${f} -> ${s}`));
      expect(bad).toEqual([]);
    });
  }
});
