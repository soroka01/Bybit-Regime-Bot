// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { githubLink, REPO_URL } from "../src/ui/githubLink";
import { buildCommands, setPaletteContext } from "../src/ui/palette";

describe("ссылка на репозиторий", () => {
  it("внешняя ссылка: новая вкладка, noopener и noreferrer, понятное имя, значок", () => {
    const a = githubLink();
    expect(REPO_URL).toMatch(/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+$/);
    expect(a.tagName).toBe("A");
    expect(a.getAttribute("href")).toBe(REPO_URL);
    expect(a.target).toBe("_blank");
    expect(a.rel.split(" ")).toEqual(expect.arrayContaining(["noopener", "noreferrer"]));
    expect(a.getAttribute("aria-label")).toContain("GitHub");
    expect(a.querySelector("svg path")).not.toBeNull();
    expect(a.querySelector(".gh-label")?.textContent).toBe("GitHub");
  });

  it("команда палитры открывает тот же адрес без opener", () => {
    setPaletteContext(null, {});
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    buildCommands().find((c) => c.id === "act:github")!.run();
    expect(open).toHaveBeenCalledWith(REPO_URL, "_blank", "noopener,noreferrer");
    open.mockRestore();
  });
});
