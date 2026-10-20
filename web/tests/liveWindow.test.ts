// Окно результата на вкладке live (liveWindow.ts): полное / частичное / отсутствующее покрытие снимка, «Всё» и «7 д» из /api/stats,
// откат на снимок с честной подписью, чип «сегодня». Числа из отчёта владельца: первая точка 160,63 $, сейчас 166,05 $ -> +5,42 $ / +3,37 %.
import { describe, expect, it } from "vitest";
import { plToday, type Pt } from "../src/tabs/liveCalc";
import { COVER_TOL_MS, fmtSpan, resolveWindow, snapshotCoverage, statsNeeded, todayLabel, type Feeds } from "../src/tabs/liveWindow";
import type { StatsResponse } from "../src/types";
import { richStats } from "./fixtures/statsRich";

const MIN = 60_000, H = 3_600_000, DAY = 86_400_000;
const NOW = Date.parse("2026-10-17T12:00:00Z");

/** Снимок как у /api/live: последние `n` точек с шагом 5 мин, заканчиваются «сейчас». */
const snapshot = (n: number, last = 166.05, first = 162.6): Pt[] =>
  Array.from({ length: n }, (_, i) => ({ t: NOW - (n - 1 - i) * 5 * MIN, v: first + ((last - first) * i) / Math.max(1, n - 1) }));

/** Ответ /api/stats?range=…: ≤600 точек от eq_start_ts до NOW. */
function statsFor(range: "all" | "7d", o: { start: number; first: number; last: number; before?: boolean; over?: Partial<StatsResponse> }): StatsResponse {
  const n = 600;
  const t = Array.from({ length: n }, (_, i) => Math.round((o.start + ((NOW - o.start) * i) / (n - 1)) / 1000));
  const v = t.map((_, i) => o.first + ((o.last - o.first) * i) / (n - 1));
  const base = richStats();
  return {
    ...base, range, computed_at: NOW / 1000,
    equity: { ...base.equity!, eq_start: o.first, eq_end: o.last, eq_start_ts: Math.round(o.start / 1000), eq_end_ts: NOW / 1000, starts_before_data: !!o.before, delta: +(o.last - o.first).toFixed(4), wallet_return_pct: +((o.last / o.first - 1) * 100).toFixed(4) },
    series: { ...base.series!, equity: { t, v, lo: v, hi: v } },
    ...o.over,
  };
}
const START_ALL = Date.parse("2026-10-06T00:12:00Z");
const ALL = () => statsFor("all", { start: START_ALL, first: 160.63, last: 166.05 });

describe("покрытие снимка", () => {
  it("24 ч: 288 точек (24 ч при шаге 5 мин) — покрыто; 100 точек (≈ 8 ч) — нет", () => {
    expect(snapshotCoverage("24h", snapshot(289), NOW).full).toBe(true);
    const c = snapshotCoverage("24h", snapshot(100), NOW);
    expect(c.full).toBe(false);
    expect(c.spanMs).toBe(99 * 5 * MIN);
  });
  it("7 д: 2000 точек = ≈ 6,9 суток — НЕ покрыто; 2020 точек — покрыто; допуск 15 мин", () => {
    const c = snapshotCoverage("7d", snapshot(2000), NOW);
    expect(c.full).toBe(false);
    expect(c.spanMs / DAY).toBeCloseTo(6.94, 2);
    expect(snapshotCoverage("7d", snapshot(2020), NOW).full).toBe(true);
    const edge = [{ t: NOW - 7 * DAY + COVER_TOL_MS, v: 1 }, { t: NOW, v: 2 }];
    expect(snapshotCoverage("7d", edge, NOW).full).toBe(true);
    expect(snapshotCoverage("7d", [{ t: edge[0]!.t + 1, v: 1 }, edge[1]!], NOW).full).toBe(false);
  });
  it("«Всё» снимок не гарантирует никогда; пустой ряд — охват 0", () => {
    expect(snapshotCoverage("all", snapshot(3000), NOW).full).toBe(false);
    expect(snapshotCoverage("7d", [], NOW)).toEqual({ full: false, spanMs: 0, firstMs: null });
  });
  it("нужен ли /api/stats: Всё — всегда, 7 д — если снимок короче недели, 24 ч — никогда", () => {
    expect(statsNeeded("all", snapshot(10), NOW)).toBe("all");
    expect(statsNeeded("7d", snapshot(2000), NOW)).toBe("7d");
    expect(statsNeeded("7d", snapshot(2100), NOW)).toBeNull();
    expect(statsNeeded("24h", snapshot(10), NOW)).toBeNull();
  });
  it("fmtSpan: минуты, часы, дни с запятой", () => {
    expect([fmtSpan(0), fmtSpan(5 * MIN), fmtSpan(7 * H), fmtSpan(6.94 * DAY), fmtSpan(11.2 * DAY)]).toEqual(["0 мин", "5 мин", "7 ч", "6,9 дн.", "11,2 дн."]);
  });
});

describe("окно результата: 24 ч и 7 д из снимка", () => {
  it("24 ч, снимок покрывает — «за 24 ч», база = кошелёк сутки назад", () => {
    const pts = snapshot(2000);
    const w = resolveWindow("24h", pts, 166.05, NOW, {});
    expect(w.state).toBe("ok");
    expect(w.label).toBe("за 24 ч");
    expect(w.partial).toBe(false);
    expect(w.source).toBe("snapshot");
    expect(w.pl!.since).toBeLessThanOrEqual(NOW - DAY + 5 * MIN);
  });
  it("24 ч, а данных 8 часов — подпись сокращается до «за доступные 8 ч»", () => {
    const w = resolveWindow("24h", snapshot(97), 166.05, NOW, {});
    expect(w.label).toBe("за доступные 8 ч");
    expect(w.partial).toBe(true);
  });
  it("7 д, снимок покрывает неделю — «за 7 дн.», без запроса /api/stats", () => {
    const w = resolveWindow("7d", snapshot(2100), 166.05, NOW, {});
    expect(w.label).toBe("за 7 дн.");
    expect(w.partial).toBe(false);
    expect(w.source).toBe("snapshot");
  });
  it("нет точек: окно пустое, не падает", () => {
    const w = resolveWindow("24h", [], undefined, NOW, {});
    expect(w.pl).toBeNull();
    expect(w.series).toEqual([]);
  });
});

describe("«Всё»: /api/stats", () => {
  it("статистика есть: чип = delta и wallet_return_pct, база — eq_start, подпись «за всё время · с 06.10.26»", () => {
    const w = resolveWindow("all", snapshot(2000), 166.05, NOW, { all: { kind: "ok", res: ALL() } });
    expect(w.state).toBe("ok");
    expect(w.source).toBe("stats");
    expect(w.label).toBe("за всё время · с 06.10.26");
    expect(w.partial).toBe(false);
    expect(w.pl!.base).toBeCloseTo(160.63, 2);
    expect(w.pl!.cur).toBeCloseTo(166.05, 2);
    expect(w.pl!.abs).toBeCloseTo(5.42, 2);
    expect(w.pl!.pct! * 100).toBeCloseTo(3.374, 2);              // 166,05 / 160,63 − 1
    expect(w.spanMs / DAY).toBeCloseTo(11.49, 1);
    expect(w.series).toHaveLength(600);
    expect(w.series[0]!.t).toBe(Math.round(START_ALL / 1000) * 1000);
    expect(w.title).toContain("за всё время");
  });
  it("фикстура владельца: снимок (2000 точек, 162,60 -> 166,05) даёт НЕПРАВИЛЬНЫЕ +3,44 $ / +2,12 % — вот почему «Всё» берётся из статистики", () => {
    const snap = resolveWindow("all", snapshot(2000), 166.05, NOW, { all: { kind: "error" } });            // без статистики: честно «доступная история», а не всё время
    expect(snap.pl!.abs).toBeCloseTo(3.45, 1);
    expect(snap.pl!.pct! * 100).toBeCloseTo(2.12, 1);
    expect(snap.label).toBe("за доступную историю (≈ 6,9 дн.)");
    const all = resolveWindow("all", snapshot(2000), 166.05, NOW, { all: { kind: "ok", res: ALL() } });
    expect(all.pl!.abs).toBeCloseTo(5.42, 2);
    expect(all.pl!.pct! * 100).toBeCloseTo(3.37, 2);
    expect(all.label).not.toBe(snap.label);
  });
  it("загрузка (записи нет / loading): ничего не показываем под видом полного периода", () => {
    for (const feeds of [{}, { all: { kind: "loading" } }] as Feeds[]) {
      const w = resolveWindow("all", snapshot(2000), 166.05, NOW, feeds);
      expect(w.state).toBe("loading");
      expect(w.pl).toBeNull();
      expect(w.label).toBe("за всё время");
      expect(w.series).toEqual([]);
    }
  });
  it("ошибка запроса: снимок с честной подписью, не «за всё время»", () => {
    const w = resolveWindow("all", snapshot(2000), 166.05, NOW, { all: { kind: "error" } });
    expect(w.state).toBe("ok");
    expect(w.label).toBe("за доступную историю (≈ 6,9 дн.)");
    expect(w.partial).toBe(true);
    expect(w.source).toBe("snapshot");
    expect(w.label).not.toContain("всё время");
  });
  it("available:false / ответ без equity / чужой диапазон: тот же откат на снимок", () => {
    const bad: StatsResponse[] = [
      { ...ALL(), available: false, reason: "no_db" },
      { ...ALL(), equity: undefined },
      { ...ALL(), series: undefined },
      { ...ALL(), range: "30d" },                                                   // чужой диапазон из общего кэша
      { ...ALL(), equity: { ...ALL().equity!, eq_start: null } },
    ];
    for (const res of bad) {
      const w = resolveWindow("all", snapshot(2000), 166.05, NOW, { all: { kind: "ok", res } });
      expect(w.label).toBe("за доступную историю (≈ 6,9 дн.)");
    }
    const e = resolveWindow("all", snapshot(2000), 166.05, NOW, { all: { kind: "error", message: "boom" } });
    expect(e.label).toBe("за доступную историю (≈ 6,9 дн.)");
  });
  it("ответ без delta / wallet_return_pct — считается из eq_start и eq_end", () => {
    const res = ALL();
    const w = resolveWindow("all", snapshot(2000), 166.05, NOW, { all: { kind: "ok", res: { ...res, equity: { ...res.equity!, delta: null, wallet_return_pct: null } } } });
    expect(w.source).toBe("stats");
    expect(w.pl!.abs).toBeCloseTo(5.42, 2);
    expect(w.pl!.pct! * 100).toBeCloseTo(3.374, 2);
  });
  it("снимок без equity: чип по последней точке ряда, вкладка не падает", () => {
    const w = resolveWindow("all", snapshot(2000), undefined, NOW, { all: { kind: "error" } });
    expect(w.pl!.cur).toBeCloseTo(166.05, 2);
  });
});

describe("«7 д» при неполном снимке", () => {
  const SEVEN = (before = false) => statsFor("7d", { start: NOW - 7 * DAY, first: 161.9, last: 166.05, before });
  it("статистика есть: честные 7 суток из /api/stats (график — её точки)", () => {
    const w = resolveWindow("7d", snapshot(2000), 166.05, NOW, { "7d": { kind: "ok", res: SEVEN() } });
    expect(w.source).toBe("stats");
    expect(w.label).toBe("за 7 дн.");
    expect(w.partial).toBe(false);
    expect(w.pl!.base).toBeCloseTo(161.9, 2);
    expect(w.pl!.abs).toBeCloseTo(4.15, 2);
    expect(w.series[0]!.t).toBe((NOW - 7 * DAY));
  });
  it("статистика есть, но бот работает меньше недели (starts_before_data) — «за доступные N дн. · с …»", () => {
    const res = statsFor("7d", { start: NOW - 4 * DAY, first: 100, last: 110, before: true });
    const w = resolveWindow("7d", snapshot(2000), 110, NOW, { "7d": { kind: "ok", res } });
    expect(w.label).toBe("за доступные 4,0 дн. · с 13.10.26");
    expect(w.partial).toBe(true);
  });
  it("ошибка: «за доступные 6,9 дн.»; пока грузится — loading", () => {
    const e = resolveWindow("7d", snapshot(2000), 166.05, NOW, { "7d": { kind: "error" } });
    expect(e.label).toBe("за доступные 6,9 дн.");
    expect(e.partial).toBe(true);
    expect(resolveWindow("7d", snapshot(2000), 166.05, NOW, {}).state).toBe("loading");
  });
});

describe("чип «сегодня»", () => {
  it("снимок начинается раньше начала суток UTC: база — первая точка суток, подпись «с 00:00 UTC»", () => {
    const pts = [{ t: Date.parse("2026-10-16T22:00:00Z"), v: 100 }, { t: Date.parse("2026-10-17T00:05:00Z"), v: 101 }, { t: NOW, v: 103 }];
    const t = plToday(pts, 103, NOW)!;
    expect(t.base).toBe(101);
    expect(t.abs).toBe(2);
    expect(todayLabel(t, NOW)).toBe("сегодня (с 00:00 UTC)");
  });
  it("первая точка суток заметно позже полуночи (бот запущен днём / снимок короче суток): время указано в подписи", () => {
    const pts = [{ t: Date.parse("2026-10-17T09:30:00Z"), v: 100 }, { t: NOW, v: 103 }];
    const t = plToday(pts, 103, NOW)!;
    expect(t.base).toBe(100);
    expect(todayLabel(t, NOW)).toBe("сегодня (с 09:30 UTC)");
  });
  it("снимок целиком вчерашний -> сегодня данных нет (null), а не вчерашняя база", () => {
    expect(plToday([{ t: NOW - 20 * H, v: 100 }, { t: NOW - 13 * H, v: 101 }], 102, NOW)).toBeNull();
  });
});
