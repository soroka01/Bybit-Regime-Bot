import { describe, expect, it } from "vitest";
import { filterCommands, isSubsequence, norm, pushRecent, scoreCommand, type Command } from "../src/ui/paletteCore";

const c = (id: string, title: string, group = "Вкладки", keywords = ""): Command => ({ id, title, group, keywords, run() {} });
const cmds = [
  c("tab:lab", "Лаборатория", "Вкладки", "lab"),
  c("tab:best", "Лучшие стратегии", "Вкладки", "leaderboard рейтинг"),
  c("tab:live", "Бот (live)", "Вкладки", "bot"),
  c("theme:dark", "Тёмная тема", "Тема", "dark"),
  c("act:fetch", "Подгрузить новые свечи", "Действия", "update данные"),
];

describe("командная палитра: поиск", () => {
  it("нормализация: регистр и ё", () => {
    expect(norm("  Тёмная   ТЕМА ")).toBe("темная тема");
  });
  it("подстрока и начало слова", () => {
    expect(filterCommands(cmds, "лаб").map((x) => x.id)[0]).toBe("tab:lab");
    expect(filterCommands(cmds, "тема").map((x) => x.id)).toContain("theme:dark");
    expect(filterCommands(cmds, "темная").map((x) => x.id)).toEqual(["theme:dark"]);
  });
  it("ключевые слова и раздел", () => {
    expect(filterCommands(cmds, "leaderboard").map((x) => x.id)).toEqual(["tab:best"]);
    expect(filterCommands(cmds, "действия").map((x) => x.id)).toEqual(["act:fetch"]);
  });
  it("все слова запроса обязательны", () => {
    expect(filterCommands(cmds, "новые свечи").map((x) => x.id)).toEqual(["act:fetch"]);
    expect(filterCommands(cmds, "новые лаборатория")).toEqual([]);
  });
  it("нестрогое совпадение по порядку символов (от 3 букв)", () => {
    expect(isSubsequence("лбр", "лаборатория")).toBe(true);
    expect(filterCommands(cmds, "лбр").map((x) => x.id)).toEqual(["tab:lab"]);
    expect(scoreCommand(cmds[0]!, "яя")).toBe(0);
  });
  it("лучшее совпадение выше", () => {
    const r = filterCommands([c("a", "Открыть настройки"), c("b", "Настройки"), c("d", "Прочее", "Вкладки", "настройки")], "настройки").map((x) => x.id);
    expect(r[0]).toBe("b");
    expect(r[r.length - 1]).toBe("d");
  });
  it("пустой запрос: недавние первыми, остальное в исходном порядке", () => {
    expect(filterCommands(cmds, "", ["act:fetch", "tab:live"]).map((x) => x.id)).toEqual(["act:fetch", "tab:live", "tab:lab", "tab:best", "theme:dark"]);
    expect(filterCommands(cmds, "").map((x) => x.id)).toEqual(cmds.map((x) => x.id));
  });
  it("история: без повторов, не длиннее max", () => {
    expect(pushRecent(["a", "b", "c"], "b")).toEqual(["b", "a", "c"]);
    expect(pushRecent(["a", "b"], "z", 2)).toEqual(["z", "a"]);
  });
});
