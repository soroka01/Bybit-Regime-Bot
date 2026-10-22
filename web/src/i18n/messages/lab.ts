// Словарь «lab»: вкладка «Лаборатория», результат бэктеста, оптимизация, перебор (F1). Ключи начинаются с «lab.»; ru обязан содержать те же ключи, что и en (проверяет компилятор).
import type { Msg } from "./types";

export const en = {
} satisfies Record<string, Msg>;

export const ru: Record<keyof typeof en, Msg> = {
};
