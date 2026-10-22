// @vitest-environment jsdom
// Ядро локализации: целостность словарей (ключи ru = en, без пересечений, английский без кириллицы), t() с подстановкой и множественными формами,
// определение языка и переключение, форматирование чисел и дат по языку. Язык тестов по умолчанию — ru (tests/setup.ts).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { __setLang, detectLang, getLang, hasKey, LANG_KEY, switchLang, t, tDyn, tList } from "../src/i18n";
import { EN, RU, SPACE_KEYS } from "../src/i18n/messages";
import { agoMs, date, fmtUtc, int, num, pct, spct, susd, usd } from "../src/ui/fmt";
import { ddmm, ddmmyy, fmtDuration, mdLocal, stamp, ymdLocal } from "../src/ui/fmtTime";
import { killReasonLabel, reasonLabel, whyLabel } from "../src/i18n/storedText";
import { metricDefinition, strategyDesc } from "../src/i18n/serverText";

const CYR = /[А-Яа-яЁё]/;
const texts = (m: unknown): string[] => (typeof m === "string" ? [m] : Array.isArray(m) ? (m as string[]) : Object.values(m as Record<string, string>));

describe("словари", () => {
  it("ru и en содержат одни и те же ключи", () => {
    expect(Object.keys(RU).sort()).toEqual(Object.keys(EN).sort());
  });

  it("ключи не пересекаются между пространствами и начинаются с префикса своего файла", () => {
    const all = Object.values(SPACE_KEYS).flat();
    expect(new Set(all).size).toBe(all.length);
    const prefixes: Record<string, string[]> = { common: ["app.", "lang.", "tab.", "time.", "date.", "common."] };
    for (const [ns, keys] of Object.entries(SPACE_KEYS)) {
      const ok = prefixes[ns] ?? [`${ns}.`];
      expect(keys.filter((k) => !ok.some((p) => k.startsWith(p))), `ключи вне префикса в «${ns}»`).toEqual([]);
    }
  });

  it("английский текст не содержит кириллицы (кроме названия языка «Русский»)", () => {
    const bad = Object.entries(EN).filter(([k, m]) => k !== "lang.ru" && texts(m).some((s) => CYR.test(s))).map(([k]) => k);
    expect(bad).toEqual([]);
  });

  it("русский текст не пуст, формы множественного числа полные (one/few/many/other)", () => {
    for (const [k, m] of Object.entries(RU)) {
      expect(texts(m).every((s) => s.length > 0), k).toBe(true);
      if (typeof m === "object" && !Array.isArray(m)) expect(Object.keys(m).sort(), `${k}: формы ru`).toEqual(["few", "many", "one", "other"]);
    }
    for (const [k, m] of Object.entries(EN)) {
      if (typeof m === "object" && !Array.isArray(m)) expect(Object.keys(m).sort(), `${k}: формы en`).toEqual(["one", "other"]);
    }
  });

  it("плейсхолдеры {имя} в ru и en совпадают", () => {
    const ph = (m: unknown) => [...new Set(texts(m).join(" ").match(/\{\w+\}/g) ?? [])].sort();
    for (const k of Object.keys(EN)) expect(ph(RU[k]), k).toEqual(ph(EN[k]));
  });
});

describe("t()", () => {
  afterEach(() => __setLang("ru"));

  it("подстановка и язык", () => {
    expect(t("tab.key_title", { title: "Лаборатория", n: 1 })).toBe("Лаборатория (клавиша 1)");
    __setLang("en");
    expect(t("tab.key_title", { title: "Lab", n: 1 })).toBe("Lab (key 1)");
    expect(t("app.loading")).toBe("Loading…");
  });

  it("нет ключа: возвращается ключ, tDyn — undefined", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(t("no.such.key" as never)).toBe("no.such.key");
    expect(tDyn("no.such.key")).toBeUndefined();
    expect(hasKey("tab.lab")).toBe(true);
    expect(hasKey("no.such.key")).toBe(false);
    warn.mockRestore();
  });

  it("списки: месяцы и дни недели по языку", () => {
    expect(tList("date.months_short")).toHaveLength(12);
    expect(tList("date.weekdays_short")[0]).toBe("Пн");
    __setLang("en");
    expect(tList("date.weekdays_short")[0]).toBe("Mon");
  });
});

describe("определение языка", () => {
  beforeEach(() => {
    localStorage.clear();
    document.head.innerHTML = "";
    history.replaceState(null, "", "/");
  });

  it("по умолчанию en", () => expect(detectLang()).toBe("en"));
  it("meta от сервера", () => {
    document.head.innerHTML = '<meta name="tb-default-lang" content="ru">';
    expect(detectLang()).toBe("ru");
  });
  it("localStorage сильнее meta, ?lang= сильнее всего", () => {
    document.head.innerHTML = '<meta name="tb-default-lang" content="ru">';
    localStorage.setItem(LANG_KEY, "en");
    expect(detectLang()).toBe("en");
    history.replaceState(null, "", "/?lang=ru");
    expect(detectLang()).toBe("ru");
  });
  it("мусор игнорируется", () => {
    localStorage.setItem(LANG_KEY, "de");
    document.head.innerHTML = '<meta name="tb-default-lang" content="xx">';
    history.replaceState(null, "", "/?lang=fr");
    expect(detectLang()).toBe("en");
  });

  it("switchLang сохраняет выбор и перезагружает страницу; повторный выбор текущего — ничего", () => {
    const reload = vi.fn();
    switchLang("ru", reload);   // текущий язык в тестах — ru: это «тот же» язык
    expect(reload).not.toHaveBeenCalled();
    __setLang("en");
    switchLang("ru", reload);
    expect(localStorage.getItem(LANG_KEY)).toBe("ru");
    expect(reload).toHaveBeenCalledTimes(1);
    __setLang("ru");
  });
});

describe("форматирование по языку", () => {
  const T = Date.UTC(2026, 9, 22, 17, 5) / 1000;   // 2026-10-22 17:05 UTC
  afterEach(() => __setLang("ru"));

  it("ru: как раньше", () => {
    expect(num(1234.5)).toBe("1 234,50");
    expect(usd(-12.5)).toBe("−12,50 $");
    expect(susd(12.5)).toBe("+12,50 $");
    expect(pct(0.123)).toBe("12,3%");
    expect(date(T)).toBe("22.10.2026");
    expect(fmtUtc(T)).toBe("22.10.26 17:05 UTC");
    expect(ddmm(T)).toBe("22.10");
    expect(ddmmyy(T)).toBe("22.10.26");
    expect(stamp(T)).toBe("22.10.26 17:05");
    expect(ymdLocal("2026-10-22")).toBe("22.10.2026");
    expect(mdLocal("2026-10-22")).toBe("22.10");
    expect(fmtDuration(5 * 3600 + 20 * 60)).toBe("5 ч 20 мин");
    expect(fmtDuration(3 * 86400 + 4 * 3600)).toBe("3 д 4 ч");
    expect(agoMs(0, 120_000)).toBe("2 мин назад");
  });

  it("en: десятичная точка, $ спереди, ISO-даты, английские единицы", () => {
    __setLang("en");
    expect(num(1234.5)).toBe("1,234.50");
    expect(int(1234567)).toBe("1,234,567");
    expect(usd(12.5)).toBe("$12.50");
    expect(usd(-12.5)).toBe("−$12.50");
    expect(susd(12.5)).toBe("+$12.50");
    expect(spct(0.0123)).toBe("+1.2%");
    expect(date(T)).toBe("2026-10-22");
    expect(fmtUtc(T)).toBe("2026-10-22 17:05 UTC");
    expect(ddmm(T)).toBe("10-22");
    expect(ddmmyy(T)).toBe("2026-10-22");
    expect(stamp(T)).toBe("2026-10-22 17:05");
    expect(ymdLocal("2026-10-22")).toBe("2026-10-22");
    expect(mdLocal("2026-10-22")).toBe("10-22");
    expect(fmtDuration(45)).toBe("45 s");
    expect(fmtDuration(5 * 3600 + 20 * 60)).toBe("5 h 20 min");
    expect(fmtDuration(3 * 86400 + 4 * 3600)).toBe("3 d 4 h");
    expect(agoMs(0, 120_000)).toBe("2 min ago");
    expect(agoMs(0, 100 * 3600_000)).toBe("4 d ago");
  });

  it("отрицательный ноль и «—» не зависят от языка", () => {
    __setLang("en");
    expect(num(-0.001)).toBe("0.00");
    expect(usd(undefined)).toBe("—");
    expect(getLang()).toBe("en");
  });
});

describe("переключение языка: ?lang= в адресе не должен перебивать выбор", () => {
  afterEach(() => { __setLang("ru"); history.replaceState(null, "", "/"); localStorage.clear(); });

  it("выбор сохранён: ?lang= убирается из адреса перед перезагрузкой", () => {
    __setLang("ru");
    history.replaceState(null, "", "/?lang=ru&x=1#/lab");
    const reload = vi.fn();
    switchLang("en", reload);
    expect(localStorage.getItem(LANG_KEY)).toBe("en");
    expect(window.location.search).toBe("?x=1");
    expect(window.location.hash).toBe("#/lab");
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

describe("тексты, сохранённые ботом по-русски (storedText)", () => {
  afterEach(() => __setLang("ru"));

  it("ru: строка возвращается без изменений", () => {
    for (const s of ["бар", "бар, EMA cross · 4h: вне рынка → лонг", "старт/перезапуск, с опозданием 12 мин", "что-то новое"]) expect(reasonLabel(s)).toBe(s);
    expect(killReasonLabel("просадка от пика 12.3% >= 25%")).toBe("просадка от пика 12.3% >= 25%");
    expect(whyLabel("закрытие вручную")).toBe("закрытие вручную");
  });

  it("en: известные значения переводятся, неизвестные остаются как есть", () => {
    __setLang("en");
    expect(reasonLabel("бар")).toBe("bar");
    expect(reasonLabel("закрытие вручную")).toBe("manual close");
    expect(reasonLabel("стоп (реконструкция)")).toBe("stop (reconstructed)");
    expect(reasonLabel("бар, EMA cross · 4h: вне рынка → лонг")).toBe("bar, EMA cross · 4h: flat → long");
    expect(reasonLabel("бар, SMA cross · 4h: нет данных → шорт; Regime switch (CHOP) · 6h: лонг → вне рынка"))
      .toBe("bar, SMA cross · 4h: no data → short; Regime switch (CHOP) · 6h: long → flat");
    expect(reasonLabel("старт/перезапуск, с опозданием 12 мин")).toBe("start/restart, 12 min late");
    expect(reasonLabel("что-то новое")).toBe("что-то новое");
    expect(reasonLabel("")).toBe("");
    expect(reasonLabel(null)).toBe("");
    expect(killReasonLabel("просадка от пика 12.3% >= 25%")).toBe("drawdown from peak 12.3% >= 25%");
    expect(killReasonLabel("дневной убыток 4.1% >= 3%")).toBe("daily loss 4.1% >= 3%");
    expect(killReasonLabel("неизвестно")).toBe("неизвестно");
  });
});

describe("серверные тексты по ключу", () => {
  it("нет перевода — запасной серверный текст", () => {
    expect(strategyDesc("No Such Strategy", "серверный текст")).toBe("серверный текст");
    expect(metricDefinition("no_such_metric")).toBe("");
  });
});
