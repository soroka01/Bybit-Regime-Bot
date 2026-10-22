// Словарь «server»: тексты по кодам/ключам, пришедшим с сервера: ошибки API, определения метрик, причины (F5, F4). Ключи начинаются с «server.»; ru обязан содержать те же ключи, что и en (проверяет компилятор).
import type { Msg } from "./types";

export const en = {
} satisfies Record<string, Msg>;

export const ru: Record<keyof typeof en, Msg> = {
};
