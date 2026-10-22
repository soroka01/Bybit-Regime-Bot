// Форматирование чисел и дат по языку интерфейса (i18n): ru — «1 234,50», «дд.мм.гг», «50 $»; en — «1,234.50», «YYYY-MM-DD», «$50».
// Табличные цифры задаются в CSS.
//
// ВРЕМЯ. Сервер отдаёт моменты времени в UTC (ISO-строки с «+00:00» либо «наивные» строки «YYYY-MM-DD HH:MM:SS» — они тоже UTC).
// Везде в таблицах, рейтинге и полосе свежести показываем UTC с явным суффиксом « UTC» (fmtUtc / utcShort).
// Полоса свежести дополнительно показывает подсказку «МСК». date() и datetime() — UTC-вариант (datetime = fmtUtc).
import { getLang, localeTag, t, tList } from "../i18n";
import type { Num } from "../types";

const MINUS = "−";
const cache = new Map<string, Intl.NumberFormat>();
function fmtN(d: number): Intl.NumberFormat {
  const key = `${localeTag()}:${d}`;
  let f = cache.get(key);
  if (!f) cache.set(key, (f = new Intl.NumberFormat(localeTag(), { minimumFractionDigits: d, maximumFractionDigits: d })));
  return f;
}

export const isNum = (v: Num | undefined): v is number => v != null && Number.isFinite(v);

/** Ненулевая цифра в отформатированной строке: после округления «0,00» ноль — знак «+» не нужен. */
const nonZero = (s: string): boolean => /[1-9]/.test(s);
/** «+» у положительных значений с ненулевой строкой (ноль после округления — без знака). */
const plus = (v: Num | undefined, s: string): string => (isNum(v) && v > 0 && nonZero(s) ? "+" + s : s);

/**
 * Приводит знак к типографскому минусу и убирает «отрицательный ноль»: если после округления в строке нет ни одной
 * ненулевой цифры («-0», «-0,0»), знак отбрасывается. Intl (в зависимости от ICU) может отдать как «-», так и «−».
 */
export function fixSign(s: string): string {
  const neg = s.startsWith("-") || s.startsWith(MINUS);
  if (!neg) return s;
  const body = s.slice(1);
  return /[1-9]/.test(body) ? MINUS + body : body;
}

export function num(v: Num | undefined, d = 2): string {
  return isNum(v) ? fixSign(fmtN(d).format(v)) : "—";
}
export function pct(v: Num | undefined, d = 1): string {
  return isNum(v) ? fixSign(fmtN(d).format(v * 100)) + "%" : "—";
}
/** Процент со знаком «+» для положительных значений (ноль после округления — без знака). */
export function spct(v: Num | undefined, d = 1): string {
  return plus(v, pct(v, d));
}
/** Значение, УЖЕ выраженное в процентах (1,5 = 1,5 %; так отдаёт /api/stats): «12,3%». В отличие от pct не умножает на 100. */
export function pctU(v: Num | undefined, d = 1): string {
  return isNum(v) ? num(v, d) + "%" : "—";
}
/** pctU со знаком «+». */
export const spctU = (v: Num | undefined, d = 1): string => plus(v, pctU(v, d));
export function int(v: Num | undefined): string {
  return isNum(v) ? fixSign(fmtN(0).format(Math.round(v))) : "—";
}
/** Знак доллара по языку: ru — «12,50 $», en — «$12.50» (минус перед знаком валюты: «−$12.50»). */
const withUsd = (s: string): string => (getLang() === "ru" ? `${s} $` : s.startsWith(MINUS) ? `${MINUS}$${s.slice(1)}` : `$${s}`);
export function usd(v: Num | undefined, d = 2): string {
  return isNum(v) ? withUsd(fixSign(fmtN(d).format(v))) : "—";
}
/** Сумма в долларах со знаком «+» у положительных. */
export const susd = (v: Num | undefined, d = 2): string => plus(v, usd(v, d));
/** Число со знаком «+» у положительных. */
export const snum = (v: Num | undefined, d = 2): string => plus(v, num(v, d));
/** Знак значения для раскраски с допуском eps (округлённый ноль — нейтральный). */
export function toneOf(v: Num | undefined, eps = 0): "pos" | "neg" | "" {
  return !isNum(v) || Math.abs(v) <= eps ? "" : v > 0 ? "pos" : "neg";
}
export function sgn(v: Num | undefined): "pos" | "neg" | "" {
  return toneOf(v);
}

// ---------- время
/** Разбор момента времени: число — секунды Unix; строка без часового пояса считается UTC. null при невалидном значении. */
export function parseTime(s: string | number | null | undefined): Date | null {
  if (s == null || s === "") return null;
  let d: Date;
  if (typeof s === "number") d = new Date(s * 1000);
  else {
    let t = s.trim().replace(" ", "T");
    if (/^\d{4}-\d{2}-\d{2}$/.test(t)) t += "T00:00:00Z";
    else if (!/(Z|[+-]\d{2}(:?\d{2})?)$/i.test(t.replace(/^\d{4}-\d{2}-\d{2}/, ""))) t += "Z";
    d = new Date(t);
  }
  return isNaN(+d) ? null : d;
}

// Форматтеры Intl создаются лениво и по языку: ru — как раньше («дд.мм.гггг», «дд.мм.гг чч:мм»), en — ISO без Intl (см. date/fmtUtc).
const dfCache = new Map<string, Intl.DateTimeFormat>();
function dtf(kind: "date" | "time"): Intl.DateTimeFormat {
  const key = `${localeTag()}:${kind}`;
  let f = dfCache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(localeTag(), kind === "date"
      ? { timeZone: "UTC", year: "numeric", month: "2-digit", day: "2-digit" }
      : { timeZone: "UTC", year: "2-digit", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    dfCache.set(key, f);
  }
  return f;
}

/** Дата (UTC): ru — «дд.мм.гггг», en — «ГГГГ-ММ-ДД». */
export function date(s: string | number | null | undefined): string {
  const d = parseTime(s);
  return d ? (getLang() === "ru" ? dtf("date").format(d) : d.toISOString().slice(0, 10)) : "—";
}
/** Дата и время в UTC с явным суффиксом: ru — «дд.мм.гг чч:мм UTC», en — «ГГГГ-ММ-ДД чч:мм UTC». */
export function fmtUtc(s: string | number | null | undefined): string {
  const d = parseTime(s);
  if (!d) return "—";
  return (getLang() === "ru" ? dtf("time").format(d).replace(",", "") : d.toISOString().slice(0, 16).replace("T", " ")) + " UTC";
}
/** Синоним fmtUtc: во всех таблицах время — UTC. */
export const datetime = fmtUtc;
/** Короткий ISO-вид «ГГГГ-ММ-ДД ЧЧ:ММ» в UTC (без суффикса — суффикс добавляет вызывающий). */
export function utcShort(s: string | number | null | undefined): string {
  const d = parseTime(s);
  return d ? d.toISOString().slice(0, 16).replace("T", " ") : "—";
}

/** «N с/мин/ч/дн назад» («N s/min/h/d ago») от момента в мс (now — для тестов). */
export function agoMs(ms: number, now = Date.now()): string {
  const sec = Math.max(0, (now - ms) / 1000);
  if (!Number.isFinite(sec)) return "—";
  if (sec < 90) return t("time.ago_s", { n: Math.round(sec) });
  if (sec < 5400) return t("time.ago_min", { n: Math.round(sec / 60) });
  if (sec < 172800) return t("time.ago_h", { n: Math.round(sec / 3600) });
  return t("time.ago_d", { n: Math.round(sec / 86400) });
}
/** То же от строки времени сервера (naive-строка = UTC); нераспознанное — «—». */
export function ago(s: string | null | undefined): string {
  const d = parseTime(s);
  return d ? agoMs(+d) : "—";
}

/** Короткие названия месяцев (по языку на момент загрузки модуля). */
export const MONTHS: readonly string[] = tList("date.months_short");
