"""Русские копии серверных текстов во фронтенде не должны разойтись с сервером.

Сервер отдаёт описания стратегий (core/strategies.py) и определения метрик статистики (web/stats_base.DEFINITIONS) по-русски; интерфейс переводит их
на английский по имени/ключу (web/src/i18n/messages/strategies.json и definitions.json), а русский показывает из своей копии. Если текст на сервере
правят, копия во фронтенде обязана измениться вместе с ним: иначе в русском интерфейсе окажется устаревшее описание."""
import json
import re
from pathlib import Path

from tradebot.core.strategies import STRATEGIES
from tradebot.web import stats_base

MESSAGES = Path(__file__).resolve().parent.parent / "web" / "src" / "i18n" / "messages"
CYR = re.compile("[А-Яа-яЁё]")


def _load(name: str) -> dict:
    return json.loads((MESSAGES / f"{name}.json").read_text(encoding="utf-8"))


def test_strategy_descriptions_ru_copy_equals_server_and_en_is_complete():
    cat = _load("strategies")
    want = {f"strategies.desc.{name}": s.desc for name, s in STRATEGIES.items()}
    assert cat["ru"] == want, "ru-копия описаний стратегий разошлась с core/strategies.py: обновите web/src/i18n/messages/strategies.json"
    assert set(cat["en"]) == set(want), "в en нет перевода для части стратегий (или есть лишние)"
    assert all(v.strip() and not CYR.search(v) for v in cat["en"].values()), "английское описание пусто или содержит кириллицу"


def test_metric_definitions_ru_copy_equals_server_and_en_is_complete():
    cat = _load("definitions")
    fee = f"{stats_base.TAKER_FEE_EST:.3%}"
    want = {f"definitions.{k}": v for k, v in stats_base.DEFINITIONS.items()}
    ru = {k: v.replace("{fee}", fee) for k, v in cat["ru"].items()}
    assert ru == want, "ru-копия определений метрик разошлась с web/stats_base.DEFINITIONS: обновите web/src/i18n/messages/definitions.json"
    assert set(cat["en"]) == set(want), "в en нет перевода для части определений (или есть лишние)"
    assert all(v.strip() and not CYR.search(v) for v in cat["en"].values()), "английское определение пусто или содержит кириллицу"
    assert "{fee}" in cat["en"]["definitions.fees_est"], "в английском fees_est потерян плейсхолдер {fee}"


def test_leaderboard_reason_codes_cover_server_sentences():
    """Фронтенд сопоставляет русские тексты причин (запасной путь для старого сервера) с кодами; сервер отдаёт и сам код (reason_code)."""
    src = (MESSAGES.parent.parent / "tabs" / "lbCard.ts").read_text(encoding="utf-8")
    from tradebot.web import research
    for sentence, code in (("нет свечей — скачайте данные", "no_klines"), ("нет данных фандинга/OI/LSR", "no_derivs"),
                           ("недостаточно сделок на in-sample для надёжного подбора", "few_trades")):
        assert f'"{sentence}": "{code}"' in src, f"таблица REASON_CODES в lbCard.ts не знает «{sentence}»"
        assert sentence in Path(research.__file__).read_text(encoding="utf-8"), f"сервер больше не отдаёт «{sentence}»: уберите её из lbCard.ts"
