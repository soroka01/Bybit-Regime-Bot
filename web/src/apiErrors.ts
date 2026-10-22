// Тексты ошибок API на языке интерфейса. Сервер присылает код (data_missing, busy, …), дополнительные поля и русское сообщение. Для ИЗВЕСТНЫХ кодов
// полный текст собирается здесь из кода и полей (ru совпадает с серверным, en — перевод); переменная часть, которой нет в полях (исключение, имя файла),
// берётся из серверного сообщения. Неизвестный код (bad_request, validation, http, …) — серверное сообщение как есть. Модуль не зависит от DOM и api.ts.
import { t, type MsgKey } from "./i18n";

/** Коды, у которых текст не зависит от переменных частей. */
const FIXED: Record<string, MsgKey> = {
  shutting_down: "server.err.shutting_down",
  db_error: "server.err.db_error",
  superseded: "server.err.superseded",
  cancelled: "server.err.cancelled",
};

/** Известные фразы без кода (ответы промежуточного слоя безопасности: HTTP 403/415 со строкой в detail). */
const PLAIN: Record<string, MsgKey> = {
  "cross-site запрещён": "server.err.cross_site",
  "cross-origin запрещён": "server.err.cross_origin",
  "тело запроса должно быть application/json": "server.err.bad_content_type",
  // английские тексты сервера (APP_LANGUAGE=en, по умолчанию): интерфейс на ru показывает свой перевод независимо от языка сервера
  "cross-site is forbidden": "server.err.cross_site",
  "cross-origin is forbidden": "server.err.cross_origin",
  "the request body must be application/json": "server.err.bad_content_type",
};

/** Хвост сообщения после известного серверного префикса (русского или английского — язык сервера задаёт APP_LANGUAGE); null, если префикса нет (тогда текст не трогаем). */
const tail = (msg: string, ...prefixes: string[]): string | null => {
  const p = prefixes.find((x) => msg.startsWith(x));
  return p === undefined ? null : msg.slice(p.length);
};

/** Сообщение ошибки, пришедшей с сервера (status, code, message, поля detail), на языке интерфейса. */
export function localizeApiError(code: string, msg: string, extra: Record<string, unknown> = {}): string {
  const fixed = FIXED[code];
  if (fixed) return t(fixed);
  switch (code) {
    case "busy":
      return t(extra.lane === "net" || /загрузк|download/i.test(msg) ? "server.err.busy_net" : "server.err.busy");
    case "data_missing": {
      const { symbol, tf, missing } = extra;
      if (typeof symbol !== "string" || typeof tf !== "string" || !Array.isArray(missing)) return msg;
      return t("server.err.data_missing", { symbol, tf, missing: missing.join(", ") });
    }
    case "bad_results": {
      const m = /\(([^()]+)\)\s*$/.exec(msg);
      return m ? t("server.err.bad_results", { kind: m[1]! }) : msg;
    }
    case "timeout": {
      const lim = typeof extra.limit_s === "number" ? String(extra.limit_s) : /^(?:расчёт дольше|calculation took longer than) (\d+)(?: с| s)?$/.exec(msg)?.[1];
      return lim ? t("server.err.timeout", { s: lim }) : msg;
    }
    case "no_results": {
      const d = tail(msg, "нет файла результатов walk-forward: ", "no walk-forward results file: ");
      return d == null ? msg : t("server.err.no_results", { detail: d });
    }
    case "update_failed": {
      const d = tail(msg, "не удалось обновить данные: ", "failed to update the data: ");
      return d == null ? msg : t("server.err.update_failed", { detail: d });
    }
    case "fetch_failed":
      return t("server.err.fetch_failed", { detail: msg });
    case "http": {
      const k = PLAIN[msg];
      return k ? t(k) : msg;
    }
    default:
      return msg;
  }
}

/** Заголовок баннера ошибки по коду. */
export function errorTitle(code: string): string {
  switch (code) {
    case "data_missing": return t("server.title.data_missing");
    case "timeout": return t("server.title.timeout");
    case "busy": return t("server.title.busy");
    case "network": return t("server.title.network");
    case "no_results": return t("server.title.no_results");
    default: return t("server.title.error");
  }
}

/** Тексты ошибок, которые собирает сам фронтенд (запрос не дошёл до сервера или оборван). */
export const clientErrorText = {
  timeout: (seconds: number): string => t("server.err.timeout_client", { s: seconds }),
  aborted: (): string => t("server.err.aborted"),
  network: (): string => t("server.err.network"),
};
