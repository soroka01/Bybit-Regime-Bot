// Вкладка «Статистика»: выбор состояния страницы и тексты «данных нет» (чистая логика).
import { t } from "../i18n";
import type { StatsResponse } from "../types";

interface ApiErrLike { status: number; code: string; message: string }
type PageState =
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

const UNAVAILABLE_COPY: Record<string, { title: string; hint: string; icon: string }> = {
  no_db: { title: t("stats.unavail.no_db.title"), hint: t("stats.unavail.no_db.hint"), icon: "power" },
  no_trades: { title: t("stats.unavail.no_trades.title"), hint: t("stats.unavail.no_trades.hint"), icon: "chart" },
  no_equity: { title: t("stats.unavail.no_equity.title"), hint: t("stats.unavail.no_equity.hint"), icon: "chart" },
  db_error: { title: t("stats.unavail.db_error.title"), hint: t("stats.unavail.db_error.hint"), icon: "warn" },
  error: { title: t("stats.unavail.error.title"), hint: t("stats.unavail.error.hint"), icon: "warn" },
};
/** Для этого кода причины у интерфейса есть собственные заголовок и пояснение (серверное сообщение на другом языке их только дублирует). */
export const isKnownUnavailable = (reason: string): boolean => reason in UNAVAILABLE_COPY;
export const unavailableCopy = (reason: string): { title: string; hint: string; icon: string } => UNAVAILABLE_COPY[reason] ?? UNAVAILABLE_COPY.error!;

/** Сообщение сервера без повтора заголовка: «База бота не найдена: бот ещё не запускался.» под заголовком «База бота не найдена» -> «Бот ещё не запускался.»
 *  Не зависит от языка: заголовок убирается, только если сообщение действительно с него начинается; иначе сообщение остаётся как есть. */
export function trimMessage(title: string, message: string): string {
  const m = (message ?? "").trim();
  if (!m) return "";
  if (m.toLowerCase().startsWith(title.toLowerCase())) {
    const rest = m.slice(title.length).replace(/^[\s:—–-]+/, "");
    return rest ? rest[0]!.toUpperCase() + rest.slice(1) : "";
  }
  return m;
}
