// Словарь «stored»: тексты, которые бот сохранил в БД по-русски (trades.reason, roundtrips.reason, kill_reason) и сайт показывает на выбранном языке.
// Используются только через i18n/storedText.ts (ru — исходная строка без изменений, en — перевод известных значений). Ключи начинаются с «stored.».
import type { Msg } from "./types";

export const en = {
  "stored.why.bar": "bar",
  "stored.why.start": "start/restart",
  "stored.why.catchup": "catch-up recalculation",
  "stored.why.manual_recalc": "unscheduled recalculation",
  "stored.why.signal": "signal",
  "stored.why.manual_close": "manual close",
  "stored.why.tg_close": "close from Telegram",
  "stored.why.panic": "panic",
  "stored.why.kill": "kill-switch",
  "stored.why.no_stop": "no exchange stop",
  "stored.why.closed_by_exchange": "closed by exchange",
  "stored.why.stop_reconstructed": "stop (reconstructed)",
  "stored.late": "{n} min late",
  "stored.word.none": "no data",
  "stored.word.flat": "flat",
  "stored.word.long": "long",
  "stored.word.short": "short",
  "stored.kill_dd": "drawdown from peak {a} >= {b}",
  "stored.kill_daily": "daily loss {a} >= {b}",
} satisfies Record<string, Msg>;

export const ru: Record<keyof typeof en, Msg> = {
  "stored.why.bar": "бар",
  "stored.why.start": "старт/перезапуск",
  "stored.why.catchup": "догоняющий пересчёт",
  "stored.why.manual_recalc": "внеплановый пересчёт",
  "stored.why.signal": "сигнал",
  "stored.why.manual_close": "закрытие вручную",
  "stored.why.tg_close": "закрытие из Telegram",
  "stored.why.panic": "паника",
  "stored.why.kill": "kill-switch",
  "stored.why.no_stop": "нет стопа на бирже",
  "stored.why.closed_by_exchange": "закрыто биржей",
  "stored.why.stop_reconstructed": "стоп (реконструкция)",
  "stored.late": "с опозданием {n} мин",
  "stored.word.none": "нет данных",
  "stored.word.flat": "вне рынка",
  "stored.word.long": "лонг",
  "stored.word.short": "шорт",
  "stored.kill_dd": "просадка от пика {a} >= {b}",
  "stored.kill_daily": "дневной убыток {a} >= {b}",
};
