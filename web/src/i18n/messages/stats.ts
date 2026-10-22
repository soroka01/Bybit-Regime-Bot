// Словарь «stats»: вкладка «Статистика» (F4). Ключи начинаются с «stats.»; ru обязан содержать те же ключи, что и en (проверяет компилятор).
import type { Msg } from "./types";

export const en = {
} satisfies Record<string, Msg>;

export const ru: Record<keyof typeof en, Msg> = {
};
