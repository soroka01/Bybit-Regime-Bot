"""Аудит ops: валидация конфигурации (ключи обязательны, PAPER/testnet - ошибка, формат токена), завершение задач amain, порядок lock/лог,
run.bat (ASCII + CRLF, бэкофф, run.log)."""
import asyncio
from pathlib import Path

import pytest

from tradebot import app
from tradebot.trading.config import ConfigError, load_env

TOKEN = "123456789:AAFakeTokenForTestsOnly_0123456789abc"
BASE = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_OWNER_ID": "42"}
ROOT = Path(__file__).resolve().parents[1]


# ---------- config ----------
@pytest.mark.parametrize("bad", ["t", "123456789", "abc:def", "123456789:short", "123456789:" + "x" * 30 + " y", ":" + "a" * 30, " "])
def test_token_format_validated(bad):
    with pytest.raises(ConfigError) as ei:
        load_env({**BASE, "TELEGRAM_BOT_TOKEN": bad})
    assert len(bad) < 5 or bad not in str(ei.value)                    # сообщение не содержит значения токена


def test_token_ok():
    assert load_env({**BASE, **KEYS}).owner_id == 42


KEYS = {"BYBIT_API_KEY": "AKEY123", "BYBIT_API_SECRET": "ASECRET456"}


def test_keys_are_always_required_without_leaking():
    for extra in ({}, {"BYBIT_API_KEY": "AKEY123"}, {"BYBIT_API_SECRET": "ASECRET456"}, {"BYBIT_API_KEY": " ", "BYBIT_API_SECRET": "ASECRET456"}):
        with pytest.raises(ConfigError, match="BYBIT_API_KEY") as ei:
            load_env({**BASE, **extra})
        assert "AKEY123" not in str(ei.value) and "ASECRET456" not in str(ei.value) and TOKEN not in str(ei.value)
    env = load_env({**BASE, **KEYS})
    assert env.api_key == "AKEY123" and env.api_secret == "ASECRET456" and env.owner_id == 42
    assert "ASECRET456" not in repr(env) and "AKEY123" not in repr(env) and TOKEN not in repr(env)


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "Yes", "on", " On "])
def test_paper_enabled_is_a_config_error_even_with_keys(val):
    """Режима PAPER больше нет: старый .env с PAPER=1 не должен тихо запускать бота иначе, чем ждёт владелец (fail-closed)."""
    for extra in ({}, KEYS):
        with pytest.raises(ConfigError, match="PAPER больше не поддерживается: удалите PAPER из .env"):
            load_env({**BASE, **extra, "PAPER": val})


@pytest.mark.parametrize("val", ["0", "false", "no", "off", ""])
def test_paper_disabled_values_are_ignored(val):
    assert load_env({**BASE, **KEYS, "PAPER": val}).owner_id == 42


def test_bybit_env_testnet_is_a_config_error_and_mainnet_is_ignored():
    with pytest.raises(ConfigError, match="BYBIT_ENV=testnet.*не поддерживается"):
        load_env({**BASE, **KEYS, "BYBIT_ENV": "testnet"})
    with pytest.raises(ConfigError, match="testnet"):
        load_env({**BASE, **KEYS, "BYBIT_ENV": " TestNet "})
    with pytest.raises(ConfigError, match="BYBIT_ENV"):
        load_env({**BASE, **KEYS, "BYBIT_ENV": "bogus"})
    for v in ("mainnet", "MAINNET", ""):
        assert load_env({**BASE, **KEYS, "BYBIT_ENV": v, "I_UNDERSTAND_MAINNET": "1"}).owner_id == 42
    assert load_env({**BASE, **KEYS, "I_UNDERSTAND_MAINNET": "0"}).owner_id == 42          # маркер молча игнорируется


def test_env_example_documents_scopes_without_values():
    txt = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "Contract Orders" in txt and "Withdraw" in txt
    for line in txt.splitlines():
        if line.startswith(("TELEGRAM_BOT_TOKEN=", "BYBIT_API_KEY=", "BYBIT_API_SECRET=")):
            assert line.split("=", 1)[1] == ""                                  # только имена, значений нет


# ---------- amain: завершение рабочих задач ----------
def _tasks(**coros):
    async def go():
        ts = [asyncio.create_task(c, name=n) for n, c in coros.items()]
        done, _ = await asyncio.wait(ts, return_when=asyncio.FIRST_COMPLETED)
        for t in ts:
            if not t.done():
                t.cancel()
        await asyncio.gather(*ts, return_exceptions=True)
        return done
    return asyncio.run(go())


async def _quiet():
    return None


async def _boom():
    raise ValueError("упала")


async def _forever():
    await asyncio.sleep(3600)


def test_worker_finished_without_error_is_failure():
    exc = app.finished_task_error(_tasks(runner=_quiet(), stop=_forever()))
    assert isinstance(exc, RuntimeError) and "runner" in str(exc)


def test_worker_exception_is_reraised():
    exc = app.finished_task_error(_tasks(monitor=_boom(), stop=_forever()))
    assert isinstance(exc, ValueError)


def test_worker_cancelled_is_failure_not_cancellederror():
    async def go():
        t = asyncio.create_task(_forever(), name="polling")
        await asyncio.sleep(0)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        return app.finished_task_error({t})
    exc = asyncio.run(go())
    assert isinstance(exc, RuntimeError) and "отменена" in str(exc)


def test_stop_alone_is_clean_shutdown():
    assert app.finished_task_error(_tasks(stop=_quiet(), runner=_forever())) is None


def test_stop_together_with_finished_worker_is_not_a_crash():
    async def go():
        a = asyncio.create_task(_quiet(), name="stop")
        b = asyncio.create_task(_quiet(), name="runner")
        await asyncio.wait([a, b])
        return app.finished_task_error({a, b})
    assert asyncio.run(go()) is None


# ---------- main: блокировка до лога ----------
def test_second_instance_exits_before_touching_log(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(app, "disable_quickedit", lambda: None)
    monkeypatch.setattr(app, "setup_logging", lambda: calls.append("log"))

    def locked():
        calls.append("lock")
        raise app.AlreadyRunning()
    monkeypatch.setattr(app, "acquire_lock", locked)
    assert app.main([]) == app.EXIT_LOCKED == 3
    assert calls == ["lock"]                                                    # лог второй экземпляр даже не открывал


def test_config_error_exit_code_4_after_lock_then_log(monkeypatch, capsys):
    calls = []

    class Lock:
        def close(self):
            calls.append("unlock")
    monkeypatch.setattr(app, "disable_quickedit", lambda: None)
    monkeypatch.setattr(app, "setup_logging", lambda: calls.append("log"))
    monkeypatch.setattr(app, "acquire_lock", lambda: calls.append("lock") or Lock())

    def bad():
        raise ConfigError("нет токена")
    monkeypatch.setattr("tradebot.trading.config.load_env", bad)
    assert app.main([]) == app.EXIT_CONFIG == 4
    assert calls[:2] == ["lock", "log"]
    assert "нет токена" in capsys.readouterr().out


# ---------- run.bat ----------
def test_run_bat_is_ascii_crlf_and_has_backoff():
    raw = (ROOT / "run.bat").read_bytes()
    raw.decode("ascii")                                                         # только ASCII
    assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n") and b"\r" not in raw.replace(b"\r\n", b"")
    txt = raw.decode("ascii")
    for needle in ("chcp 65001 >nul", "set DELAY=15", "set DELAY=60", "set DELAY=300", "GEQ 600", r"storage\logs\run.log",
                   "exitcode=%RC%", "uptime=%UP%", 'if "%RC%"=="0" goto end', 'if "%RC%"=="3" goto end', 'if "%RC%"=="4" goto end',
                   "python -m tradebot.app"):
        assert needle in txt, needle
