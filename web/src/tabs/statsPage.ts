// Вкладка «Статистика»: выбор состояния страницы и тексты «данных нет» (чистая логика).
import type { StatsResponse } from "../types";

export interface ApiErrLike { status: number; code: string; message: string }
export type PageState =
  | { kind: "loading" }
  | { kind: "error"; message: string; code: string }
  | { kind: "unavailable"; reason: string; message: string }
  | { kind: "ready" };

export function selectState(input: { res: StatsResponse | null; err: ApiErrLike | null; loading?: boolean }): PageState {
  const { res, err } = input;
  if (err) return { kind: "error", message: err.message, code: err.code };
  if (!res) return { kind: "loading" };
  if (!res.available) return { kind: "unavailable", reason: res.reason ?? "error", message: res.message ?? "" };
  return { kind: "ready" };
}

export const UNAVAILABLE_COPY: Record<string, { title: string; hint: string; icon: string }> = {
  no_db: { title: "База бота не найдена", hint: "Бот ещё не запускался. Запустите его (run.bat): он создаст storage/state/bot.db и начнёт записывать сделки и капитал — статистика появится сама.", icon: "power" },
  no_trades: { title: "Закрытых сделок пока нет", hint: "Статистика строится по сделкам бота. Она появится после первой завершённой сделки (вход и выход).", icon: "chart" },
  no_equity: { title: "Нет истории капитала", hint: "Бот записывает капитал кошелька в таблицу equity; подождите несколько минут работы бота — график и ROI появятся.", icon: "chart" },
  db_error: { title: "Не удалось прочитать базу бота", hint: "Файл базы занят или повреждён. Подождите минуту и нажмите «Обновить»; если не проходит — проверьте журнал бота.", icon: "warn" },
  error: { title: "Не удалось посчитать статистику", hint: "На сервере возникла ошибка расчёта. Нажмите «Обновить»; подробности — в журнале сервера дашборда.", icon: "warn" },
};
export const unavailableCopy = (reason: string): { title: string; hint: string; icon: string } => UNAVAILABLE_COPY[reason] ?? UNAVAILABLE_COPY.error!;

/** Сообщение сервера без повтора заголовка: «База бота не найдена: бот ещё не запускался.» под заголовком «База бота не найдена» -> «Бот ещё не запускался.» */
export function trimMessage(title: string, message: string): string {
  const m = (message ?? "").trim();
  if (!m) return "";
  if (m.toLowerCase().startsWith(title.toLowerCase())) {
    const rest = m.slice(title.length).replace(/^[\s:—–-]+/, "");
    return rest ? rest[0]!.toUpperCase() + rest.slice(1) : "";
  }
  return m;
}
