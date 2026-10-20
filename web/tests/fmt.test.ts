import { describe, expect, it } from "vitest";
import { ago, agoMs, date, datetime, fixSign, fmtUtc, int, num, parseTime, pct, pctU, sgn, snum, spct, spctU, susd, toneOf, usd, utcShort } from "../src/ui/fmt";
import { alignSeries, stepValueAt, stride } from "../src/charts/align";
import { clockUtc } from "../src/ui/fmtTime";

const MINUS = "−";

describe("числа", () => {
  it("null/undefined/NaN/Infinity -> тире", () => {
    for (const f of [num, pct, spct, int, usd]) {
      expect(f(null)).toBe("—");
      expect(f(undefined)).toBe("—");
      expect(f(NaN)).toBe("—");
      expect(f(Infinity)).toBe("—");
      expect(f(-Infinity)).toBe("—");
    }
  });

  it("отрицательный ноль никогда не печатается", () => {
    expect(num(-0)).toBe("0,00");
    expect(num(-0.001)).toBe("0,00");
    expect(pct(-0.0004)).toBe("0,0%");
    expect(pct(-0)).toBe("0,0%");
    expect(int(-0.4)).toBe("0");
    expect(int(-0)).toBe("0");
    expect(usd(-0.001)).toBe("0,00 $");
    expect(spct(-0.00001)).toBe("0,0%");
    expect(spct(0.00001)).toBe("0,0%");
    for (const s of [num(-0.001), pct(-0.0004), int(-0.2), usd(-0.004), spct(-0.0001, 0)]) expect(s).not.toMatch(/[-−]0/);
  });

  it("настоящие отрицательные числа получают типографский минус", () => {
    expect(num(-1.5)).toBe(MINUS + "1,50");
    expect(pct(-0.123)).toBe(MINUS + "12,3%");
    expect(int(-7.6)).toBe(MINUS + "8");
    expect(spct(-0.05, 0)).toBe(MINUS + "5%");
  });

  it("положительные: spct добавляет плюс только если после округления не ноль", () => {
    expect(spct(0.1234, 1)).toBe("+12,3%");
    expect(spct(0)).toBe("0,0%");
  });

  it("fixSign", () => {
    expect(fixSign("-0,0")).toBe("0,0");
    expect(fixSign("−0")).toBe("0");
    expect(fixSign("-1,2")).toBe(MINUS + "1,2");
    expect(fixSign("5")).toBe("5");
  });

  it("sgn", () => {
    expect(sgn(null)).toBe("");
    expect(sgn(0)).toBe("");
    expect(sgn(1)).toBe("pos");
    expect(sgn(-1)).toBe("neg");
    expect(sgn(NaN)).toBe("");
  });
});

describe("время (UTC)", () => {
  it("наивная строка считается UTC и не зависит от пояса браузера", () => {
    expect(parseTime("2025-03-01 12:30:00")?.toISOString()).toBe("2025-03-01T12:30:00.000Z");
    expect(parseTime("2025-03-01T12:30:00+00:00")?.toISOString()).toBe("2025-03-01T12:30:00.000Z");
    expect(parseTime("2025-03-01T15:30:00+03:00")?.toISOString()).toBe("2025-03-01T12:30:00.000Z");
    expect(parseTime("2025-03-01")?.toISOString()).toBe("2025-03-01T00:00:00.000Z");
    expect(parseTime(1740832200)?.toISOString()).toBe("2025-03-01T12:30:00.000Z");
  });
  it("невалидное -> null/тире", () => {
    expect(parseTime("мусор")).toBeNull();
    expect(parseTime("")).toBeNull();
    expect(parseTime(null)).toBeNull();
    expect(fmtUtc("мусор")).toBe("—");
    expect(date(undefined)).toBe("—");
    expect(utcShort(null)).toBe("—");
  });
  it("явный суффикс UTC", () => {
    expect(fmtUtc("2025-03-01T12:30:00+00:00")).toMatch(/^01\.03\.25 12:30 UTC$/);
    expect(datetime("2025-03-01 12:30:00")).toBe(fmtUtc("2025-03-01 12:30:00"));
    expect(date("2025-03-01T23:59:00+00:00")).toBe("01.03.2025");
    expect(utcShort("2025-03-01T12:30:45+00:00")).toBe("2025-03-01 12:30");
    expect(utcShort("2025-03-01T15:30:00+03:00")).toBe("2025-03-01 12:30");
  });
  it("ago", () => {
    expect(ago(null)).toBe("—");
    expect(ago("мусор")).toBe("—");
    expect(ago(new Date(Date.now() - 30_000).toISOString())).toMatch(/с назад$/);
    expect(ago(new Date(Date.now() - 3 * 3600_000).toISOString())).toMatch(/ч назад$/);
  });
});

describe("проценты в процентных единицах и знаковые форматтеры", () => {
  it("pctU не умножает на 100; pct(x) == pctU(x * 100)", () => {
    expect(pctU(12.34, 1)).toBe("12,3%");
    expect(pctU(-0.001, 1)).toBe("0,0%");
    expect(pctU(null)).toBe("—");
    expect(pct(0.1234, 2)).toBe(pctU(12.34, 2));
  });
  it("spctU / susd / snum: «+» только у положительных с ненулевой строкой", () => {
    expect(spctU(1.5, 1)).toBe("+1,5%");
    expect(spctU(0.0001, 1)).toBe("0,0%");
    expect(spctU(-1.5, 1)).toBe("−1,5%");
    expect(susd(12)).toBe("+12,00 $");
    expect(susd(0.001)).toBe("0,00 $");
    expect(susd(-4)).toBe("−4,00 $");
    expect(susd(null)).toBe("—");
    expect(snum(2.5, 1)).toBe("+2,5");
    expect(snum(undefined)).toBe("—");
  });
  it("toneOf: допуск eps, не-числа нейтральны, sgn == toneOf без допуска", () => {
    expect(toneOf(0.004, 0.005)).toBe("");
    expect(toneOf(-0.006, 0.005)).toBe("neg");
    expect(toneOf(1)).toBe("pos");
    expect(toneOf(NaN)).toBe("");
    expect(toneOf(null)).toBe("");
    for (const v of [1, -1, 0, null, undefined, NaN]) expect(sgn(v as number)).toBe(toneOf(v as number));
  });
  it("agoMs: границы единиц; будущее — 0 с; ago(строка) считает так же", () => {
    const now = 1_000_000_000_000;
    expect(agoMs(now - 5000, now)).toBe("5 с назад");
    expect(agoMs(now + 5000, now)).toBe("0 с назад");
    expect(agoMs(now - 89_000, now)).toBe("89 с назад");
    expect(agoMs(now - 5 * 60_000, now)).toBe("5 мин назад");
    expect(agoMs(now - 3 * 3600_000, now)).toBe("3 ч назад");
    expect(agoMs(now - 3 * 86400_000, now)).toBe("3 дн назад");
    expect(agoMs(NaN, now)).toBe("—");
    expect(ago(new Date(Date.now() - 5 * 60_000).toISOString())).toBe("5 мин назад");
  });
});

describe("fmtTime", () => {
  it("clockUtc: время суток в UTC с суффиксом, не зависит от пояса браузера", () => {
    expect(clockUtc(Date.parse("2026-10-08T12:03:05Z"))).toBe("12:03:05 UTC");
    expect(clockUtc(Date.parse("2026-10-08T23:59:59+03:00"))).toBe("20:59:59 UTC");
  });
});

describe("charts/align", () => {
  it("stepValueAt: step-вперёд, до начала — первая, пропуски обходятся, пусто — null", () => {
    const t = [100, 200, 300, 400], v = [10, null, null, 40];
    const at = (x: number) => stepValueAt(t.length, (i) => t[i]!, (i) => v[i], x);
    expect(at(50)).toBe(10);
    expect(at(100)).toBe(10);
    expect(at(350)).toBe(10);        // 300 и 200 — пропуски, берём последнее известное
    expect(at(999)).toBe(40);
    expect(stepValueAt(0, () => 0, () => 0, 1)).toBeNull();
    expect(stepValueAt(2, (i) => i, () => null, 1)).toBeNull();
    expect(stepValueAt(3, (i) => i, (i) => (i === 2 ? 7 : NaN), 0)).toBe(7);   // до начала ряда и первая точка — пропуск: ищем вперёд
  });
  it("alignSeries: step-вперёд, до старта — null", () => {
    const r = alignSeries([{ t: [1, 3], v: [10, 30] }, { t: [2], v: [200] }]);
    expect(r.x).toEqual([1, 2, 3]);
    expect(r.ys[0]).toEqual([10, 10, 30]);
    expect(r.ys[1]).toEqual([null, 200, 200]);
  });
  it("alignSeries: пустые ряды", () => {
    expect(alignSeries([])).toEqual({ x: [], ys: [] });
    expect(alignSeries([{ t: [], v: [] }]).ys[0]).toEqual([]);
  });
  it("stride: короткие без изменений, длинные прореживаются, последний элемент сохраняется", () => {
    const a = Array.from({ length: 10 }, (_, i) => i);
    expect(stride(a, 100)).toBe(a);
    const b = Array.from({ length: 5000 }, (_, i) => i);
    const s = stride(b, 1500);
    expect(s.length).toBeLessThanOrEqual(1501);
    expect(s[0]).toBe(0);
    expect(s[s.length - 1]).toBe(4999);
  });
});
