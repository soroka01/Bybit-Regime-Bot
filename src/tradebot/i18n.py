"""Язык консоли/логов/текстов API: английский по умолчанию, русский включается APP_LANGUAGE=ru (.env или переменная окружения).
Английская строка в коде и есть msgid; русский перевод лежит в i18n_ru.RU (английский -> русский). Telegram-бот сюда не входит:
его тексты всегда русские. lang() никогда не бросает исключений: неизвестное значение -> "en" (строгая проверка - в load_env)."""
from __future__ import annotations

import os

from tradebot.paths import ROOT

LANGS = ("en", "ru")
_lang: str | None = None


def _from_sources() -> str:
    """APP_LANGUAGE из окружения, иначе из .env проекта (os.environ не меняется: работает до load_env и в отдельном web-процессе)."""
    raw = os.environ.get("APP_LANGUAGE")
    if not (raw or "").strip():
        try:
            from dotenv import dotenv_values
            raw = dotenv_values(ROOT / ".env", encoding="utf-8-sig").get("APP_LANGUAGE")
        except Exception:
            raw = None
    value = (raw or "").strip().lower()
    return value if value in LANGS else "en"


def lang() -> str:
    """Текущий язык: "en" или "ru". Результат кэшируется; set_language/reset его перекрывают/сбрасывают."""
    global _lang
    if _lang is None:
        try:
            _lang = _from_sources()
        except Exception:
            _lang = "en"
    return _lang


def set_language(value: str | None) -> None:
    """Явно задать язык (load_env после проверки значения). Неизвестное значение -> "en"."""
    global _lang
    v = (value or "").strip().lower()
    _lang = v if v in LANGS else "en"


def reset() -> None:
    """Забыть закэшированный язык (следующий lang() перечитает окружение и .env)."""
    global _lang
    _lang = None


def tr(msg: str) -> str:
    """Перевод строки: для "ru" - запись из i18n_ru.RU (если есть), иначе исходная английская строка."""
    if lang() != "ru":
        return msg
    from tradebot.i18n_ru import RU
    return RU.get(msg, msg)
