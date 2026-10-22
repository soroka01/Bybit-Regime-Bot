// Контракт «пороги вердиктов в UI == пороги сервера»: константы читаются прямо из src/tradebot/core/optimize.py и сверяются с константами
// интерфейса (ui/verdicts.ts) и с ИТОГОВЫМИ текстами обоих языков (словарь leaderboard.*), а не с исходным кодом фронтенда.
// Меняете порог на сервере — тест подскажет, какие константы и тексты поправить.
import { describe, expect, it } from "vitest";
import { t } from "../src/i18n";
import { GREEN_MONTHS_MIN_PCT, MIN_OOS_DAYS, SHARPE_MIN, VERDICT_ORDER, VERDICTS } from "../src/ui/verdicts";
import { importEn } from "./enHelpers";

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
/** 0.5 -> «0,5» (десятичная запятая ru). */
const ruNum = (x: number): string => String(x).replace(".", ",");

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

  it("константы интерфейса == условиям сервера", () => {
    expect(SHARPE_MIN).toBe(num(/get\("sharpe"\) or 0\) > ([0-9.]+)/, verdictFn));
    expect(GREEN_MONTHS_MIN_PCT).toBe(Math.round(num(/get\("green_months"\) or 0\) >= ([0-9.]+)/, verdictFn) * 100));
    expect(MIN_OOS_DAYS).toBe(num(/^MIN_OOS_DAYS\s*=\s*(\d+)/m));
  });

  it("ru: Sharpe OOS и доля зелёных месяцев в подсказках == условию robust", () => {
    expect(VERDICTS.robust.hint).toContain(`Sharpe OOS > ${ruNum(SHARPE_MIN)}`);
    expect(VERDICTS.overfit.hint).toContain(`Sharpe OOS ≤ ${ruNum(SHARPE_MIN)}`);
    expect(VERDICTS.robust.hint).toContain(`зелёных месяцев ≥ ${GREEN_MONTHS_MIN_PCT}%`);
  });

  it("en: те же пороги в английских подсказках (десятичная точка)", async () => {
    const en = await importEn(() => import("../src/ui/verdicts"));
    expect(en.VERDICTS.robust.hint).toContain(`Sharpe OOS > ${SHARPE_MIN}`);
    expect(en.VERDICTS.overfit.hint).toContain(`Sharpe OOS ≤ ${SHARPE_MIN}`);
    expect(en.VERDICTS.robust.hint).toContain(`green months ≥ ${GREEN_MONTHS_MIN_PCT}%`);
    expect(en.VERDICTS.robust.hint).not.toMatch(/[А-Яа-яЁё]/);
  });

  it("минимальное окно OOS (MIN_OOS_DAYS) в текстах обоих языков и в условиях интерфейса", async () => {
    const days = num(/^MIN_OOS_DAYS\s*=\s*(\d+)/m);
    expect(t("leaderboard.card.too_short_note", { days: MIN_OOS_DAYS })).toContain(`меньше ${days} дн.`);
    await importEn(() => import("../src/ui/verdicts"));
    const i18n = await import("../src/i18n");
    expect(i18n.getLang()).toBe("en");
    expect(i18n.t("leaderboard.card.too_short_note", { days: MIN_OOS_DAYS })).toContain(`under ${days} days`);
    // вкладка «Подбор» (tabs/optimize.ts) держит своё условие в коде: число обязано совпасть (текст — общий, через словарь; если условие вынесено в константу, проверка пропускается)
    const opt = /oos_days < (\d+)/.exec(read("src/tabs/optimize.ts"));
    if (opt) expect(Number(opt[1])).toBe(days);
  });
});
