// Контракт «пороги вердиктов в UI == пороги сервера»: константы читаются прямо из src/tradebot/core/optimize.py и сверяются с подсказками
// и текстами фронтенда (ui/verdicts.ts, tabs/lbCard.ts, tabs/optimize.ts). Меняете порог на сервере — тест подскажет, какие тексты поправить.
import { describe, expect, it } from "vitest";
import { VERDICT_ORDER, VERDICTS } from "../src/ui/verdicts";

// vitest подменяет .css пустышкой, а типов node нет — читаем исходники через process.getBuiltinModule (cwd = web/)
const proc = (globalThis as unknown as { process: { cwd(): string; getBuiltinModule(n: string): unknown } }).process;
const fs = proc.getBuiltinModule("node:fs") as { readFileSync(p: string, enc: string): string };
const read = (rel: string): string => fs.readFileSync(`${proc.cwd()}/${rel}`, "utf8").replace(/\r\n/g, "\n");

const py = read("../src/tradebot/core/optimize.py");
const verdictFn = /\ndef verdict\([\s\S]*?(?=\ndef )/.exec(py)?.[0] ?? "";
const num = (re: RegExp, src = py): number => {
  const m = re.exec(src);
  expect(m, String(re)).not.toBeNull();
  return Number(m![1]);
};
/** 0.5 -> «0,5», 0.4 -> «40%» (как в подсказках интерфейса). */
const ru = (x: number): string => String(x).replace(".", ",");

describe("пороги вердиктов: UI == core/optimize.py", () => {
  it("функция verdict найдена, константы читаются", () => {
    expect(verdictFn.length).toBeGreaterThan(200);
    expect(num(/^MIN_OOS_DAYS\s*=\s*(\d+)/m)).toBeGreaterThan(0);
  });

  it("набор вердиктов сервера == VERDICT_ORDER", () => {
    const returned = [...new Set([...verdictFn.matchAll(/return "(\w+)"/g)].map((m) => m[1]!))];
    expect(returned.sort()).toEqual([...VERDICT_ORDER].sort());
    expect(Object.keys(VERDICTS).sort()).toEqual([...VERDICT_ORDER].sort());
  });

  it("Sharpe OOS и доля зелёных месяцев в подсказках == условию robust", () => {
    const sharpe = num(/get\("sharpe"\) or 0\) > ([0-9.]+)/, verdictFn);
    const green = num(/get\("green_months"\) or 0\) >= ([0-9.]+)/, verdictFn);
    expect(VERDICTS.robust.hint).toContain(`Sharpe OOS > ${ru(sharpe)}`);
    expect(VERDICTS.overfit.hint).toContain(`Sharpe OOS ≤ ${ru(sharpe)}`);
    expect(VERDICTS.robust.hint).toContain(`зелёных месяцев ≥ ${Math.round(green * 100)}%`);
  });

  it("минимальное окно OOS (MIN_OOS_DAYS) в текстах и условиях интерфейса", () => {
    const days = num(/^MIN_OOS_DAYS\s*=\s*(\d+)/m);
    const card = read("src/tabs/lbCard.ts");
    const opt = read("src/tabs/optimize.ts");
    expect(num(/oosDays < (\d+)/, card)).toBe(days);
    expect(card).toContain(`меньше ${days} дн.`);
    expect(num(/oos_days < (\d+)/, opt)).toBe(days);
    expect(opt).toContain(`меньше ${days} дн.`);
  });
});
