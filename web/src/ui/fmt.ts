// Форматирование чисел и дат (ru-RU, табличные цифры задаются в CSS).
//
// ВРЕМЯ. Сервер отдаёт моменты времени в UTC (ISO-строки с «+00:00» либо «наивные» строки «YYYY-MM-DD HH:MM:SS» — они тоже UTC).
// Везде в таблицах, рейтинге и полосе свежести показываем UTC с явным суффиксом « UTC» (fmtUtc / utcShort).
// fmtLocal — то же в часовом поясе браузера, суффикс — аббревиатура пояса (например «GMT+3»). Полоса свежести дополнительно
// показывает подсказку «МСК». date() и datetime() — UTC-вариант по умолчанию (datetime = fmtUtc).
import type { Num } from "../types";

const MINUS = "−";
const nf = (d: number) => new Intl.NumberFormat("ru-RU", { minimumFractionDigits: d, maximumFractionDigits: d });
const cache = new Map<number, Intl.NumberFormat>();
function fmtN(d: number): Intl.NumberFormat {
  let f = cache.get(d);
  if (!f) cache.set(d, (f = nf(d)));
  return f;
}

const isNum = (v: Num | undefined): v is number => v != null && Number.isFinite(v);

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
  if (!isNum(v)) return "—";
  const s = pct(v, d);
  return v > 0 && /[1-9]/.test(s) ? "+" + s : s;
}
export function int(v: Num | undefined): string {
  return isNum(v) ? fixSign(fmtN(0).format(Math.round(v))) : "—";
}
export function usd(v: Num | undefined, d = 2): string {
  return isNum(v) ? fixSign(fmtN(d).format(v)) + " $" : "—";
}
export function sgn(v: Num | undefined): "pos" | "neg" | "" {
  return !isNum(v) || v === 0 ? "" : v > 0 ? "pos" : "neg";
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

const dfDateUtc = new Intl.DateTimeFormat("ru-RU", { timeZone: "UTC", year: "numeric", month: "2-digit", day: "2-digit" });
const dfTimeUtc = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "UTC", year: "2-digit", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
});
const dfTimeLocal = new Intl.DateTimeFormat("ru-RU", {
  year: "2-digit", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
});

/** Дата (UTC), «дд.мм.гггг». */
export function date(s: string | number | null | undefined): string {
  const d = parseTime(s);
  return d ? dfDateUtc.format(d) : "—";
}
/** Дата и время в UTC с явным суффиксом: «дд.мм.гг чч:мм UTC». */
export function fmtUtc(s: string | number | null | undefined): string {
  const d = parseTime(s);
  return d ? dfTimeUtc.format(d).replace(",", "") + " UTC" : "—";
}
/** Дата и время в часовом поясе браузера с суффиксом пояса. */
export function fmtLocal(s: string | number | null | undefined): string {
  const d = parseTime(s);
  return d ? dfTimeLocal.format(d).replace(",", "") : "—";
}
/** Синоним fmtUtc: во всех таблицах время — UTC. */
export const datetime = fmtUtc;
/** Короткий ISO-вид «ГГГГ-ММ-ДД ЧЧ:ММ» в UTC (без суффикса — суффикс добавляет вызывающий). */
export function utcShort(s: string | number | null | undefined): string {
  const d = parseTime(s);
  return d ? d.toISOString().slice(0, 16).replace("T", " ") : "—";
}

export function ago(s: string | null | undefined): string {
  const d = parseTime(s);
  if (!d) return "—";
  const sec = (Date.now() - +d) / 1000;
  if (!isFinite(sec)) return "—";
  if (sec < 90) return `${Math.max(0, Math.round(sec))} с назад`;
  if (sec < 5400) return `${Math.round(sec / 60)} мин назад`;
  if (sec < 172800) return `${Math.round(sec / 3600)} ч назад`;
  return `${Math.round(sec / 86400)} дн назад`;
}

export const MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];
