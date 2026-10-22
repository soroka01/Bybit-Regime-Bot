// Словарь «live»: вкладка «Бот (live)», плашка статуса бота, причины сделок, kill_reason (F3). Ключи начинаются с «live.»; ru обязан содержать те же ключи, что и en (проверяет компилятор).
import type { Msg } from "./types";

export const en = {
} satisfies Record<string, Msg>;

export const ru: Record<keyof typeof en, Msg> = {
};
