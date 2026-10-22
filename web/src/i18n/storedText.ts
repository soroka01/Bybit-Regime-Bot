// Русские тексты, сохранённые ботом в БД и отдаваемые API как есть: trades.reason / roundtrips.reason («<повод>[, <нога> · <тф>: <было> → <стало>]»),
// reason_group (текст до первой запятой) и kill_reason. В БД они остаются русскими (по ним группируется статистика и ищется маркер стопа), а на сайте
// переводятся при показе. Для ru строка возвращается БЕЗ изменений (регрессии быть не может); для en переводятся только известные значения,
// остальное показывается как есть. Для логики (группировка, поиск «реконструкция») всегда используйте исходную строку, а не подпись.
import { getLang, t, tDyn } from "./index";

/** Известные поводы (`why`) -> id ключа stored.why.<id>. Источник: trading/runner.py, executor.py, telegram/handlers.py, web/stats_base.py (STOP_REASON). */
const WHY_ID: Record<string, string> = {
  "бар": "bar",
  "старт/перезапуск": "start",
  "догоняющий пересчёт": "catchup",
  "внеплановый пересчёт": "manual_recalc",
  "сигнал": "signal",
  "закрытие вручную": "manual_close",
  "закрытие из Telegram": "tg_close",
  "паника": "panic",
  "kill-switch": "kill",
  "нет стопа на бирже": "no_stop",
  "закрыто биржей": "closed_by_exchange",
  "стоп (реконструкция)": "stop_reconstructed",
};
const WORD_ID: Record<string, string> = { "нет данных": "none", "вне рынка": "flat", "лонг": "long", "шорт": "short" };
const SIG_CHANGE = /^(.+ · \S+): (нет данных|вне рынка|лонг|шорт) → (нет данных|вне рынка|лонг|шорт)$/;
const LATE = /^с опозданием (\d+) мин$/;

const word = (w: string): string => tDyn(`stored.word.${WORD_ID[w]}`) ?? w;

/** Повод без хвоста (и reason_group): «бар» -> en «bar». */
export function whyLabel(why: string): string {
  if (getLang() === "ru") return why;
  const id = WHY_ID[why.trim()];
  return id ? t(`stored.why.${id}` as never) : why;
}

function tailLabel(tail: string): string {
  const late = LATE.exec(tail);
  if (late) return t("stored.late", { n: late[1]! });
  return tail.split("; ").map((part) => {
    const m = SIG_CHANGE.exec(part);
    return m ? `${m[1]}: ${word(m[2]!)} → ${word(m[3]!)}` : part;
  }).join("; ");
}

/** Причина сделки целиком: «<повод>[, <хвост>]». */
export function reasonLabel(raw: string | null | undefined): string {
  if (!raw || getLang() === "ru") return raw ?? "";
  const i = raw.indexOf(",");
  if (i < 0) return whyLabel(raw);
  return `${whyLabel(raw.slice(0, i))}, ${tailLabel(raw.slice(i + 1).trim())}`;
}

const KILL_DD = /^просадка от пика (\S+) >= (\S+)$/;
const KILL_DAILY = /^дневной убыток (\S+) >= (\S+)$/;

/** Причина kill-switch из kv (risk.check_kill): «просадка от пика 12.3% >= 25%» / «дневной убыток 4.1% >= 3%». */
export function killReasonLabel(raw: string | null | undefined): string {
  if (!raw || getLang() === "ru") return raw ?? "";
  const dd = KILL_DD.exec(raw), daily = KILL_DAILY.exec(raw);
  if (dd) return t("stored.kill_dd", { a: dd[1]!, b: dd[2]! });
  if (daily) return t("stored.kill_daily", { a: daily[1]!, b: daily[2]! });
  return raw;
}
