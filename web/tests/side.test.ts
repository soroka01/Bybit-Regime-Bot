// @vitest-environment jsdom
// Цветовая семантика: сторона (лонг/шорт, покупка/продажа) — только слова и нейтральная рамка; --pos/--neg — только прибыль/убыток.
// Правила показа среды: рабочую среду (mainnet) не рекламируем, предупреждаем только о testnet / paper. Плашка статуса бота в шапке.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveSnapshot } from "../src/types";
import { envBadge, envFlag } from "../src/ui/env";
import { orderBadge, SIDE_GLYPH, SIDE_WORD, sideBadge } from "../src/ui/side";

// Чтение исходников: vitest подменяет импорты .css пустышкой, а типов node в проекте нет — берём fs через process.getBuiltinModule (cwd = web/).
interface Fs { readdirSync(p: string): string[]; readFileSync(p: string, enc: string): string }
function readTree(dir: string, ext: string, prefix = ""): Record<string, string> {
  const proc = (globalThis as unknown as { process: { cwd(): string; getBuiltinModule(n: string): unknown } }).process;
  const fs = proc.getBuiltinModule("node:fs") as Fs;
  const base = `${proc.cwd()}/${dir}`;
  return Object.fromEntries(fs.readdirSync(base).filter((f) => f.endsWith(ext)).map((f) => [prefix + f, fs.readFileSync(`${base}/${f}`, "utf8")]));
}

const clean = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();

describe("бейдж стороны", () => {
  it("слова ЛОНГ/ШОРТ и контурные △/▽ (не закрашенные ▲/▼ результата)", () => {
    expect(SIDE_WORD).toEqual({ long: "ЛОНГ", short: "ШОРТ" });
    expect(SIDE_GLYPH).toEqual({ long: "△", short: "▽" });
    const l = sideBadge("long"), s = sideBadge("short");
    expect(l.textContent).toBe("△ЛОНГ");
    expect(s.textContent).toBe("▽ШОРТ");
    expect(l.dataset.side).toBe("long");
    expect(`${l.textContent}${s.textContent}`).not.toMatch(/[▲▼]/);
    expect(l.querySelector("[aria-hidden=true]")!.textContent).toBe("△");
  });
  it("классы: side-badge + сторона, никогда pos/neg; ордер — отдельный вид «Покупка/Продажа»", () => {
    for (const el of [sideBadge("long"), sideBadge("short"), orderBadge(true), orderBadge(false)]) {
      expect(el.classList.contains("side-badge")).toBe(true);
      expect(el.classList.contains("pos") || el.classList.contains("neg")).toBe(false);
    }
    expect(orderBadge(true).textContent).toBe("△Покупка");
    expect(orderBadge(false).textContent).toBe("▽Продажа");
    expect(orderBadge(false).classList.contains("short")).toBe(false);            // продажа ≠ шорт
    expect(orderBadge(true).classList.contains("long")).toBe(false);
  });
});

describe("CSS: --pos/--neg/--long/--short не красят сторону", () => {
  const css = readTree("src/styles", ".css");

  it("общие утилиты .long/.short в base.css больше не задают цвет", () => {
    expect(css["base.css"]).not.toMatch(/\.long\b|\.short\b/);
    expect(css["base.css"]).toMatch(/\.pos \{ color: var\(--pos\)/);
  });
  it("бейдж стороны, позиции и сигналов — на нейтральных токенах", () => {
    const rule = (sel: string) => new RegExp(`${sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{[^}]*\\}`).exec(css["tab-live.css"])?.[0] ?? "";
    for (const sel of [".side-badge", ".lv-side", ".lv-leg-sig.long, .lv-leg-sig.short"]) {
      const r = rule(sel);
      expect(r, sel).not.toBe("");
      expect(r, sel).not.toMatch(/--(pos|neg|long|short)\b/);
    }
    expect(css["tab-live.css"]).not.toMatch(/\.side-badge\.(long|short)\b/);
    expect(css["tab-live.css"]).not.toMatch(/\.lv-leg-c\.(pos|neg)/);                 // вклад в экспозицию — не прибыль
  });
  it("--long/--short остались только у маркеров графиков (charts.css) и в токенах", () => {
    const users = Object.entries(css).filter(([f, t]) => f !== "tokens.css" && /var\(--(long|short)\)/.test(t)).map(([f]) => f);
    expect(users).toEqual(["charts.css"]);
  });
  it("доли сторон в «Лонг против шорта» нейтральны (не --long/--short)", () => {
    const split = /\.st-split i\.long[^}]*\}|\.st-split i\.short[^}]*\}/g;
    for (const m of css["tab-stats.css"].match(split) ?? []) expect(m).not.toMatch(/--(pos|neg|long|short)\b/);
  });
});

describe("исходники: сторона не красится цветами P/L", () => {
  const files = ["tabs/liveView.ts", "tabs/statsView.ts", "tabs/statsTrades.ts", "ui/botStatus.ts", "ui/side.ts"];
  const raw = { ...readTree("src/tabs", ".ts", "tabs/"), ...readTree("src/ui", ".ts", "ui/") };
  it("нет «LONG/SHORT» латиницей в тексте интерфейса", () => {
    for (const f of files) {
      const src = raw[f];
      expect(src, f).toBeTruthy();
      expect(src, f).not.toMatch(/"LONG"|"SHORT"|\? "LONG|\? "SHORT/);
    }
  });
});

describe("среда: правила показа", () => {
  it("mainnet / live без paper — ничего (не рекламируем); пустая или неизвестная среда — тоже", () => {
    for (const env of ["mainnet", "MAINNET", "live", "", null, undefined, "prod", "?"]) expect(envFlag(env, false), String(env)).toBeNull();
    expect(envFlag("mainnet")).toBeNull();
    expect(envBadge("mainnet", false)).toBeNull();
  });
  it("testnet — заметное предупреждение; paper — предупреждение и поверх любой среды", () => {
    expect(envFlag("testnet")!.text).toBe("TESTNET · не реальные деньги");
    expect(envFlag(" TestNet ")!.text).toContain("TESTNET");
    expect(envFlag("mainnet", true)!.text).toBe("PAPER · без ордеров");
    expect(envFlag("testnet", true)!.text).toBe("PAPER · без ордеров");
    const b = envBadge("testnet", false)!;
    expect(b.classList.contains("badge") && b.classList.contains("warn")).toBe(true);
    expect(b.getAttribute("title")).toContain("тестовой сети");
  });
});

describe("плашка статуса бота: среда и позиция", () => {
  const NOW = Date.parse("2026-10-08T12:00:00Z");
  let pill: HTMLElement;
  const snap = (over: Partial<LiveSnapshot> = {}): LiveSnapshot => ({
    available: true, env: "mainnet", enabled: true, killed: false, equity: 1000, peak_equity: 1000, heartbeat: new Date(NOW - 20_000).toISOString(),
    position: null, ...over,
  });
  async function paint(s: LiveSnapshot) {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(s), { status: 200, headers: { "Content-Type": "application/json" } })));
    const { botStatusPill } = await import("../src/ui/botStatus");
    pill = botStatusPill();
    document.body.append(pill);
    for (let i = 0; i < 6; i++) await vi.advanceTimersByTimeAsync(0);
    return pill;
  }
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); document.body.replaceChildren(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("mainnet: слова MAINNET/TESTNET/PAPER нет ни в тексте, ни в подписи", async () => {
    const p = await paint(snap());
    expect(p.hidden).toBe(false);
    expect(clean(p.textContent)).toBe("Торговля вкл");
    expect(p.querySelector(".bp-env")).toBeNull();
    expect(p.getAttribute("aria-label")).not.toMatch(/MAINNET|TESTNET|PAPER/);
  });
  it("testnet и paper: заметная плашка среды и пометка в подписи", async () => {
    const t = await paint(snap({ env: "testnet" }));
    expect(clean(t.querySelector(".bp-env")!.textContent)).toBe("TESTNET");
    expect(t.getAttribute("aria-label")).toContain("ВНИМАНИЕ: TESTNET");
    document.body.replaceChildren();
    const pp = await paint(snap({ env: "mainnet", paper: true }));
    expect(clean(pp.querySelector(".bp-env")!.textContent)).toBe("PAPER");
  });
  it("позиция: сторона словом без цвета P/L, результат в $ и % счёта отдельным чипом по знаку", async () => {
    const p = await paint(snap({ position: { side: "Buy", qty: 0.003, entry: 100000, upnl: -4.5, mark: 98500 } }));
    const side = p.querySelector(".bp-pos")!;
    expect(clean(side.textContent)).toBe("△ ЛОНГ 0,0030");
    expect(side.className).toBe("bp-pos");
    const pnl = p.querySelector(".bp-pnl")!;
    expect(pnl.classList.contains("neg")).toBe(true);                           // лонг в минусе
    expect(clean(pnl.textContent)).toBe("▼ −4,50 $");
    const pc = p.querySelector(".bp-pct")!;
    expect(pc.classList.contains("neg")).toBe(true);
    expect(clean(pc.textContent)).toBe("−0,45%");
    expect(p.getAttribute("aria-label")).toContain("−0,45% счёта");
    document.body.replaceChildren();
    const s = await paint(snap({ position: { side: "Sell", qty: 0.003, entry: 100000, upnl: 6, mark: 98000 } }));
    expect(clean(s.querySelector(".bp-pos")!.textContent)).toBe("▽ ШОРТ 0,0030");
    expect(s.querySelector(".bp-pnl")!.classList.contains("pos")).toBe(true);     // шорт в плюсе
  });
});
