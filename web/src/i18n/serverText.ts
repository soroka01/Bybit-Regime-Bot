// Тексты по стабильным именам/кодам, пришедшим с сервера. Сервер отдаёт свой (русский) текст как запасной: если для ключа есть перевод — показываем
// его на языке интерфейса, иначе серверный текст как есть. Словари: strategies.json (по имени стратегии), definitions.json (по ключу метрики),
// server.ts (прочее по коду: ошибки API, причины).
import { tDyn, type Params } from "./index";

export const serverText = (prefix: string, key: string, fallback = "", params?: Params): string => tDyn(`${prefix}.${key}`, params) ?? fallback;

/** Описание стратегии по имени (сервер: strategies[].desc). */
export const strategyDesc = (name: string, fallback = ""): string => serverText("strategies.desc", name, fallback);
/** Определение метрики статистики по ключу (сервер: stats.definitions[key]). */
export const metricDefinition = (key: string, fallback = "", params?: Params): string => serverText("definitions", key, fallback, params);
