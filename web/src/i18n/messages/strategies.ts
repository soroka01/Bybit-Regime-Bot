// Словарь «strategies»: описания стратегий по имени: strategies.desc.<имя стратегии>. Данные — в strategies.json (чтобы pytest мог сверить русскую копию с серверным текстом: tests/test_i18n_catalog.py).
import type { Msg } from "./types";
import data from "./strategies.json";

export const en = data.en satisfies Record<string, Msg>;
export const ru: Record<keyof typeof en, Msg> = data.ru;
