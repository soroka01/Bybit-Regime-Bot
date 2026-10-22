"""Локализация консоли/логов/API: каталог переводов (английский msgid в коде -> русский в i18n_ru.RU), выбор языка (APP_LANGUAGE),
проверка значения в load_env, английский вывод --check / ConfigError / argparse."""
import ast
import asyncio
import re
from collections import Counter
from pathlib import Path

import pytest

from tradebot import app, check, i18n
from tradebot.i18n_ru import RU
from tradebot.trading import config
from tradebot.trading.config import ConfigError, load_env

SRC = Path(i18n.__file__).resolve().parent
TOKEN = "123456789:AAFakeTokenForTestsOnly_0123456789abc"
GOOD = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_OWNER_ID": "42", "BYBIT_API_KEY": "AKEY123", "BYBIT_API_SECRET": "ASECRET456"}
CYRILLIC = re.compile("[А-Яа-яЁё]")
PLACEHOLDER = re.compile(r"%\([^)]*\)[-+#0 ]*\d*(?:\.\d+)?[sdrfi]|%[-+#0 ]*\d*(?:\.\d+)?[sdrfxi]|\{[^{}]*\}")


# ------------------------------------------------------------------------------------------------ каталог переводов
def _tr_calls():
    """(файл, строка, первый аргумент) каждого вызова tr(...) в src/tradebot; первый аргумент None, если это не строковый литерал."""
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr":
                arg = node.args[0] if node.args else None
                lit = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None
                yield path.relative_to(SRC), node.lineno, lit


def _msgids():
    return {lit for _, _, lit in _tr_calls() if lit is not None}


def test_tr_is_always_called_with_a_string_literal():
    bad = [(str(p), n) for p, n, lit in _tr_calls() if lit is None]
    assert not bad, f"tr() принимает только строковый литерал (иначе каталог не проверить): {bad}"


def test_catalog_has_entry_for_every_msgid_and_no_stale_entries():
    ids = _msgids()
    assert ids, "в коде нет ни одного tr(...)"
    assert not ids - RU.keys(), f"нет русского перевода: {sorted(ids - RU.keys())}"
    assert not RU.keys() - ids, f"лишние записи в i18n_ru.RU: {sorted(RU.keys() - ids)}"


def test_catalog_has_no_duplicate_keys():
    tree = ast.parse((SRC / "i18n_ru.py").read_text(encoding="utf-8"))
    dicts = [n.value for n in ast.walk(tree) if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "RU"]
    assert len(dicts) == 1
    keys = [k.value for k in dicts[0].keys]
    dup = [k for k, c in Counter(keys).items() if c > 1]
    assert not dup, f"повторяющиеся ключи в i18n_ru.RU (последний молча затирает первый): {dup}"


def test_msgids_are_english_and_translations_are_russian():
    assert not [m for m in RU if CYRILLIC.search(m)], "в msgid кириллицы быть не должно"
    assert not [m for m, ru in RU.items() if not CYRILLIC.search(ru)], "русский перевод без кириллицы"


def test_placeholders_match_between_msgid_and_translation():
    bad = {m: ru for m, ru in RU.items() if Counter(PLACEHOLDER.findall(m)) != Counter(PLACEHOLDER.findall(ru))}
    assert not bad, f"плейсхолдеры msgid и перевода не совпадают: {bad}"
    # позиционные %-аргументы нельзя переставлять: порядок тоже должен совпасть (для %(name)s и {name} порядок не важен)
    for m, ru in RU.items():
        if "%(" not in m and "{" not in m:
            assert PLACEHOLDER.findall(m) == PLACEHOLDER.findall(ru), m


def test_translations_format_and_percent_cleanly():
    """Русская запись форматируется теми же аргументами, что и английская (ловит стрей-% и лишние {})."""
    for m, ru in RU.items():
        names = [p for p in PLACEHOLDER.findall(m) if p.startswith("{")]
        if names:
            kw = {re.match(r"\{(\w+)", p).group(1): 1 for p in names}
            assert m.format(**kw) and ru.format(**kw), m
        elif "%" in m and "%(" not in m:
            args = tuple(1 for p in PLACEHOLDER.findall(m) if p.startswith("%"))
            assert m % args and ru % args, m


# ------------------------------------------------------------------------------------------------ выбор языка
@pytest.fixture
def clean_lang(monkeypatch, tmp_path):
    """Нет ни APP_LANGUAGE в окружении, ни .env (ROOT -> пустая tmp-папка)."""
    monkeypatch.delenv("APP_LANGUAGE", raising=False)
    monkeypatch.setattr(i18n, "ROOT", tmp_path)
    i18n.reset()
    return tmp_path


def test_default_is_english(clean_lang):
    assert i18n.lang() == "en"
    assert i18n.tr("starting") == "starting"


def test_environment_variable_wins_over_dotenv(clean_lang, monkeypatch):
    (clean_lang / ".env").write_text("APP_LANGUAGE=ru\n", encoding="utf-8")
    monkeypatch.setenv("APP_LANGUAGE", "en")
    assert i18n.lang() == "en"
    i18n.reset()
    monkeypatch.setenv("APP_LANGUAGE", "ru")
    (clean_lang / ".env").write_text("APP_LANGUAGE=en\n", encoding="utf-8")
    assert i18n.lang() == "ru"


def test_dotenv_is_read_without_touching_os_environ(clean_lang):
    import os
    (clean_lang / ".env").write_text("APP_LANGUAGE=ru\n", encoding="utf-8")
    assert i18n.lang() == "ru"
    assert "APP_LANGUAGE" not in os.environ


@pytest.mark.parametrize("raw", ["﻿APP_LANGUAGE=ru\n", 'APP_LANGUAGE="ru"\n', "APP_LANGUAGE='RU'\n", "APP_LANGUAGE =  Ru  \n",
                                 "# c\nOTHER=1\nAPP_LANGUAGE=ru # note\n"])
def test_dotenv_bom_quotes_spaces_tolerated(clean_lang, raw):
    (clean_lang / ".env").write_bytes(raw.encode("utf-8"))
    assert i18n.lang() == "ru"


@pytest.mark.parametrize("raw", ["de", "", " ", "russian", "r u"])
def test_invalid_values_fall_back_to_english_and_never_raise(clean_lang, monkeypatch, raw):
    monkeypatch.setenv("APP_LANGUAGE", raw)
    assert i18n.lang() == "en"
    i18n.reset()
    monkeypatch.delenv("APP_LANGUAGE")
    (clean_lang / ".env").write_text(f"APP_LANGUAGE={raw}\n", encoding="utf-8")
    assert i18n.lang() == "en"


def test_unreadable_dotenv_does_not_raise(clean_lang):
    (clean_lang / ".env").write_bytes(b"\xff\xfe\x00garbage\xff")
    assert i18n.lang() in ("en", "ru")


def test_set_language_and_reset(clean_lang, monkeypatch):
    i18n.set_language("RU")
    assert i18n.lang() == "ru" and i18n.tr("starting") == "старт"
    i18n.set_language("klingon")
    assert i18n.lang() == "en"
    i18n.set_language("ru")
    monkeypatch.setenv("APP_LANGUAGE", "en")
    assert i18n.lang() == "ru"                       # кэш/явная установка сильнее окружения, пока не reset()
    i18n.reset()
    assert i18n.lang() == "en"


def test_tr_returns_msgid_when_there_is_no_translation():
    i18n.set_language("ru")
    assert i18n.tr("a string nobody translated") == "a string nobody translated"


# ------------------------------------------------------------------------------------------------ load_env
@pytest.mark.parametrize("val,expected", [("en", "en"), ("ru", "ru"), ("RU", "ru"), (" En ", "en"), ("", "en")])
def test_load_env_accepts_en_ru_and_empty(val, expected):
    env = load_env({**GOOD, "APP_LANGUAGE": val})
    assert env.language == expected and i18n.lang() == expected


@pytest.mark.parametrize("val", ["de", "english", "русский", "1", "ru,en"])
def test_load_env_rejects_unknown_language(val):
    with pytest.raises(ConfigError, match="APP_LANGUAGE"):
        load_env({**GOOD, "APP_LANGUAGE": val})


def test_load_env_without_the_key_keeps_the_resolved_language(monkeypatch):
    monkeypatch.setenv("APP_LANGUAGE", "ru")
    assert load_env(GOOD).language == "ru"
    i18n.reset()
    monkeypatch.setenv("APP_LANGUAGE", "en")
    assert load_env(GOOD).language == "en"


def test_load_env_calls_set_language_after_validation(monkeypatch):
    seen = []
    monkeypatch.setattr(config.i18n, "set_language", lambda v: seen.append(v))
    load_env({**GOOD, "APP_LANGUAGE": "ru"})
    assert seen == ["ru"]
    seen.clear()
    with pytest.raises(ConfigError):
        load_env({**GOOD, "APP_LANGUAGE": "zz"})
    assert seen == []                                # неверное значение язык не меняет


def test_config_error_texts_follow_the_language():
    with pytest.raises(ConfigError) as ei:
        load_env({"APP_LANGUAGE": "en"})
    assert str(ei.value) == "Set TELEGRAM_BOT_TOKEN and a numeric TELEGRAM_OWNER_ID in .env"
    with pytest.raises(ConfigError) as ei:
        load_env({"APP_LANGUAGE": "ru"})
    assert str(ei.value) == "Укажите TELEGRAM_BOT_TOKEN и числовой TELEGRAM_OWNER_ID в .env"
    with pytest.raises(ConfigError, match="PAPER mode is no longer supported"):
        load_env({**GOOD, "APP_LANGUAGE": "en", "PAPER": "1"})


# ------------------------------------------------------------------------------------------------ английский вывод
def _run(client):
    lines = []
    code = asyncio.run(check.run_check(client, lines.append))
    return code, "\n".join(lines)


def test_check_report_in_english():
    from test_check import FLAT, mock_client
    i18n.set_language("en")
    code, text = _run(mock_client())
    assert code == 0 and "[OK]" in text and "[ERROR]" not in text and "Result: all good (code 0)" in text
    assert "leverage 2x, isolated margin, one-way mode" in text and "position: none" in text
    assert not CYRILLIC.search(text)
    code, text = _run(mock_client(key={"readOnly": 1, "permissions": {"Wallet": []}, "ips": []}))
    assert code == 4 and "API key is read-only" in text and "[ERROR]" in text and not CYRILLIC.search(text)
    pos = {"list": [{**FLAT["list"][0], "size": "0.003", "side": "Buy", "avgPrice": "81550.1", "stopLoss": "0"}]}
    code, text = _run(mock_client(position=pos))
    assert code == 0 and "[WARN]" in text and "NO exchange stop" in text and "position: long 0.003 BTC" in text


def test_check_report_in_russian_keeps_todays_text():
    from test_check import mock_client
    i18n.set_language("ru")
    code, text = _run(mock_client())
    assert code == 0 and "[ОК]       " in text and "Итог: всё в порядке (код 0)" in text


def test_check_main_config_error_in_english():
    out = []
    assert check.check_main({**GOOD, "APP_LANGUAGE": "en", "BYBIT_API_SECRET": ""}, out=out.append) == 4
    assert out == ["Configuration error: Set BYBIT_API_KEY and BYBIT_API_SECRET in .env (permissions: Contract Orders + Positions, no Withdraw)"]


@pytest.mark.parametrize("lang,needle", [("en", "check .env, the API key permissions"), ("ru", "проверить .env, права API-ключа")])
def test_argparse_help_follows_the_language(capsys, monkeypatch, lang, needle):
    monkeypatch.setenv("APP_LANGUAGE", lang)
    i18n.reset()
    with pytest.raises(SystemExit):
        app.main(["--help"])
    assert needle in " ".join(capsys.readouterr().out.split())


# ------------------------------------------------------------------------------------------------ тексты ошибок API
def _client():
    from fastapi.testclient import TestClient
    from tradebot.web.api import app as web_app
    return TestClient(web_app)


JSON = {"content-type": "application/json"}


@pytest.mark.parametrize("lang,site_msg,body_msg", [
    ("en", "cross-site is forbidden", "the request body must be application/json"),
    ("ru", "cross-site запрещён", "тело запроса должно быть application/json"),
])
def test_security_middleware_messages_follow_the_language(lang, site_msg, body_msg):
    i18n.set_language(lang)
    c = _client()
    r = c.post("/api/backtest", content=b"{}", headers={**JSON, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and r.json() == {"detail": site_msg}
    r = c.post("/api/backtest", content=b"x", headers={"content-type": "text/plain"})
    assert r.status_code == 415 and r.json() == {"detail": body_msg}


def test_api_error_codes_stay_and_messages_switch():
    c = _client()
    i18n.set_language("en")
    r = c.get("/api/stats", params={"range": "bogus"})
    d = r.json()["detail"]
    assert r.status_code == 400 and d["code"] == "bad_request" and d["message"].startswith("unknown range «bogus»: allowed ")
    i18n.set_language("ru")
    r = c.get("/api/stats", params={"range": "bogus"})
    d = r.json()["detail"]
    assert r.status_code == 400 and d["code"] == "bad_request" and d["message"].startswith("неизвестный диапазон «bogus»: допустимо ")


def test_web_error_texts_in_english():
    from tradebot.web import data, meta
    i18n.set_language("en")
    e = data.DataMissing("BTCUSDT", "1h", ["candles", "funding"])
    assert str(e) == "No local data for BTCUSDT 1h: candles, funding. Download it explicitly (POST /api/data/fetch)."
    assert str(data.DataEmpty("x.parquet")).startswith("the data file is empty")
    with pytest.raises(ValueError, match="unknown strategy: nope"):
        meta.get_strategy("nope")
    with pytest.raises(ValueError, match="unknown symbol or timeframe"):
        data.check_sym_tf("XXX", "1h")
    i18n.set_language("ru")
    with pytest.raises(ValueError, match="неизвестная стратегия: nope"):
        meta.get_strategy("nope")


# ------------------------------------------------------------------------------------------------ index.html и язык по умолчанию
INDEX_TEXT = ("<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"tb-default-lang\" content=\"en\">"
              "<title>Дашборд</title></head><body>Привет, мир</body></html>")


@pytest.fixture
def dist(tmp_path, monkeypatch):
    from tradebot.web import api
    d = tmp_path / "dist"
    d.mkdir()
    (d / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(api, "WEB_DIST", d)
    return d


@pytest.mark.parametrize("path", ["/", "/index.html"])
@pytest.mark.parametrize("lang", ["en", "ru"])
def test_index_html_default_language_is_injected(dist, path, lang):
    (dist / "index.html").write_bytes(INDEX_TEXT.encode("utf-8"))
    i18n.set_language(lang)
    r = _client().get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].replace(" ", "").lower() == "text/html;charset=utf-8"
    assert r.headers["cache-control"] == "no-cache"
    assert f'<meta name="tb-default-lang" content="{lang}">' in r.text
    assert "Дашборд" in r.text and "Привет, мир" in r.text                       # кириллица в utf-8 цела
    assert r.text.count("tb-default-lang") == 1


@pytest.mark.parametrize("tag", [
    "<meta content='en' name='tb-default-lang'>",
    '<META NAME="tb-default-lang" CONTENT="de" />',
    '<meta   name = "tb-default-lang"   content = "en">',
])
def test_index_html_tag_variants(dist, tag):
    (dist / "index.html").write_text(f"<head>{tag}</head>", encoding="utf-8")
    i18n.set_language("ru")
    for path in ("/", "/index.html"):
        assert re.search(r"""content\s*=\s*(['"])ru\1""", _client().get(path).text, re.I)


@pytest.mark.parametrize("html", ["<head><title>x</title></head>", '<meta name="tb-default-lang">', '<meta name="other" content="en">'])
def test_index_html_without_a_usable_tag_is_served_unchanged(dist, html):
    (dist / "index.html").write_text(html, encoding="utf-8")
    i18n.set_language("ru")
    for path in ("/", "/index.html"):
        r = _client().get(path)
        assert r.status_code == 200 and r.text == html


def test_other_static_files_and_traversal_are_unchanged(dist):
    (dist / "index.html").write_bytes(INDEX_TEXT.encode("utf-8"))
    c = _client()
    assert c.get("/app.js").text == "console.log(1)"
    assert c.get("/missing.js").status_code == 404
    assert c.get("/%2e%2e/secret.txt").status_code == 404


@pytest.mark.parametrize("lang,title,text", [("en", "The UI is not built", "Run <code>cd web"),
                                             ("ru", "Интерфейс не собран", "Выполните <code>cd web")])
def test_fallback_page_follows_the_language(dist, lang, title, text):
    i18n.set_language(lang)
    r = _client().get("/")
    assert r.status_code == 200 and title in r.text and text in r.text


# ------------------------------------------------------------------------------------------------ watchdog (отдельный инструмент): простой английский
def test_watchdog_console_text_is_plain_english(capsys, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("tools_watchdog_i18n", SRC.parents[1] / "tools" / "watchdog.py")
    wd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wd)
    monkeypatch.setattr(wd, "read_env", lambda *_: {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_OWNER_ID": ""})
    assert wd.main(["--once"]) == 4
    assert "are not set" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        wd.main(["--help"])
    assert not CYRILLIC.search(capsys.readouterr().out)
