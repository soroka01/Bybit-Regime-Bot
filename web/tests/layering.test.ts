/// <reference types="vite/client" />
// Слои фронтенда: charts/ и ui/ — нижние, они не импортируют из tabs/ (иначе общие помощники застревают во вкладках).
import { describe, expect, it } from "vitest";

const layers: Record<string, Record<string, string>> = {
  "src/charts": import.meta.glob("../src/charts/**/*.ts", { query: "?raw", import: "default", eager: true }),
  "src/ui": import.meta.glob("../src/ui/**/*.ts", { query: "?raw", import: "default", eager: true }),
};

const importsOf = (code: string): string[] => [...code.matchAll(/(?:import|export)\s[^;]*?from\s+"([^"]+)"/g)].map((m) => m[1]!);

describe("слои модулей", () => {
  for (const [layer, files] of Object.entries(layers)) {
    it(`${layer} не импортирует из tabs/`, () => {
      expect(Object.keys(files).length).toBeGreaterThan(3);
      const bad = Object.entries(files).flatMap(([f, code]) => importsOf(code).filter((s) => /(^|\/)tabs\//.test(s)).map((s) => `${f} -> ${s}`));
      expect(bad).toEqual([]);
    });
  }
});
