// @vitest-environment jsdom
// «Статистика»: подключение — маршрут после «Бот (live)», клавиша 6, команда в палитре, справка по клавишам.
import { beforeEach, describe, expect, it } from "vitest";
import { currentRoute, ROUTES } from "../src/state";
import { installShortcuts } from "../src/ui/shortcuts";
import { buildCommands, setPaletteContext } from "../src/ui/palette";

const TITLES = { lab: "Лаборатория", portfolio: "Портфель", wf: "Walk-forward", best: "Лучшие стратегии", live: "Бот (live)", stats: "Статистика" };

beforeEach(() => { localStorage.clear(); location.hash = ""; document.body.replaceChildren(); });

describe("вкладка «Статистика»: подключение", () => {
  it("маршрут stats стоит сразу после live (шестая вкладка)", () => {
    expect(ROUTES.indexOf("stats")).toBe(ROUTES.indexOf("live") + 1);
    expect(ROUTES[5]).toBe("stats");
    location.hash = "#/stats";
    expect(currentRoute()).toBe("stats");
  });
  it("клавиша 6 открывает «Статистику»; справка по клавишам её перечисляет", () => {
    const sc = installShortcuts(TITLES);
    document.body.append(sc.button);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "6", bubbles: true }));
    expect(location.hash).toBe("#/stats");
    sc.button.click();
    const rows = [...document.querySelectorAll(".popover .shortcuts li")].map((li) => li.textContent);
    expect(rows.some((t) => t!.includes("Статистика") && t!.includes("6"))).toBe(true);
  });
  it("палитра Ctrl+K: команда «Статистика» находится по русским и английским словам", () => {
    setPaletteContext(null, TITLES);
    const cmd = buildCommands().find((c) => c.id === "tab:stats")!;
    expect(cmd.title).toBe("Статистика");
    expect(cmd.keys).toEqual(["6"]);
    expect(cmd.keywords).toContain("stats");
    expect(cmd.keywords).toContain("статистика");
    cmd.run();
    expect(location.hash).toBe("#/stats");
  });
});
