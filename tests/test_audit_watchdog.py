"""tools/watchdog.py: решение «молчит процесс / не отвечает биржа», дедупликация, восстановление, чтение .env без утечки,
отправка не-доставленного сообщения повторяется, БД читается только на чтение."""
import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradebot.trading.state import Store

SPEC = importlib.util.spec_from_file_location("tools_watchdog", Path(__file__).resolve().parents[1] / "tools" / "watchdog.py")
wd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wd)

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
ENV = {"TELEGRAM_BOT_TOKEN": "123456:SECRETTOKEN", "TELEGRAM_OWNER_ID": "42"}


def iso(sec_ago: float, now: datetime = NOW) -> str:
    return (now - timedelta(seconds=sec_ago)).isoformat(timespec="seconds")


def mkdb(tmp_path, hb=None, mon=None, now: datetime = NOW):
    p = tmp_path / "bot.db"
    st = Store(p)
    if hb is not None:
        st.set("heartbeat", iso(hb, now))
    if mon is not None:
        st.set("monitor_ok_ts", iso(mon, now))
    st.close()
    return p


class Sender:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, token, chat, text):
        self.calls.append((token, chat, text))
        return self.ok


# ---------- классификация ----------
@pytest.mark.parametrize("ages,want", [
    ({"heartbeat": 30, "monitor_ok": 40}, wd.OK),
    ({"heartbeat": 180, "monitor_ok": 180}, wd.OK),                      # ровно порог - ещё норма
    ({"heartbeat": 181, "monitor_ok": 10}, wd.PROCESS_SILENT),
    ({"heartbeat": 600, "monitor_ok": 700}, wd.PROCESS_SILENT),         # процесс молчит важнее: биржу без него не оценить
    ({"heartbeat": None, "monitor_ok": None}, wd.PROCESS_SILENT),
    ({"heartbeat": 20, "monitor_ok": 181}, wd.EXCHANGE_SILENT),
    ({"heartbeat": 20, "monitor_ok": None}, wd.EXCHANGE_SILENT),
])
def test_classify(ages, want):
    assert wd.classify(ages) == want


def test_messages_differ_by_kind():
    a = wd.message(wd.PROCESS_SILENT, {"heartbeat": 600})
    b = wd.message(wd.EXCHANGE_SILENT, {"monitor_ok": 900})
    assert "процесс бота молчит" in a and "10 мин" in a
    assert "биржа не отвечает" in b and "15 мин" in b and a != b
    assert "снова на связи" in wd.message(wd.OK, {})


# ---------- чтение БД ----------
def test_read_ages_reads_both_pulses(tmp_path):
    p = mkdb(tmp_path, hb=30, mon=95)
    ages = wd.read_ages(p, NOW)
    assert ages["heartbeat"] == pytest.approx(30) and ages["monitor_ok"] == pytest.approx(95)


def test_read_ages_missing_or_broken_db(tmp_path):
    assert wd.read_ages(tmp_path / "none.db", NOW) == {"heartbeat": None, "monitor_ok": None}
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a sqlite file at all" * 100)
    assert wd.read_ages(bad, NOW) == {"heartbeat": None, "monitor_ok": None}
    p = mkdb(tmp_path, hb=10)                                            # monitor_ok_ts ещё не писался
    assert wd.read_ages(p, NOW)["monitor_ok"] is None


def test_db_is_opened_read_only(tmp_path, monkeypatch):
    p = mkdb(tmp_path, hb=10, mon=10)
    uris = []
    real = sqlite3.connect

    def spy(target, *a, **k):
        uris.append(str(target))
        return real(target, *a, **k)
    monkeypatch.setattr(wd.sqlite3, "connect", spy)
    wd.read_ages(p, NOW)
    assert uris and all("mode=ro" in u for u in uris)
    ro = real(f"file:{p.as_posix()}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO kv VALUES('x','1')")                     # запись такому соединению невозможна
    ro.close()


# ---------- дедупликация и восстановление ----------
def test_decide_dedupes_and_recovers():
    st: dict = {}
    s, text, st = wd.decide({"heartbeat": 400, "monitor_ok": 400}, st, NOW)
    assert s == wd.PROCESS_SILENT and text and st["alerted"]
    s, text2, st2 = wd.decide({"heartbeat": 460, "monitor_ok": 460}, st, NOW)
    assert text2 is None and st2 == st                                   # то же самое - молчим
    s, text3, st3 = wd.decide({"heartbeat": 5, "monitor_ok": 400}, st2, NOW)
    assert s == wd.EXCHANGE_SILENT and text3 and "биржа" in text3        # вид проблемы сменился - новое сообщение
    s, text4, st4 = wd.decide({"heartbeat": 5, "monitor_ok": 5}, st3, NOW)
    assert s == wd.OK and "снова на связи" in text4 and not st4["alerted"]
    s, text5, st5 = wd.decide({"heartbeat": 5, "monitor_ok": 5}, st4, NOW)
    assert text5 is None                                                 # восстановление объявляется один раз
    s, text6, _ = wd.decide({"heartbeat": 400, "monitor_ok": 400}, st5, NOW)
    assert text6                                                         # и новая проблема снова объявляется


def test_ok_from_clean_state_sends_nothing():
    s, text, st = wd.decide({"heartbeat": 5, "monitor_ok": 5}, {}, NOW)
    assert s == wd.OK and text is None


# ---------- run_once: сквозной сценарий с внедрёнными часами/БД/отправкой ----------
def test_run_once_flow_with_state_file(tmp_path):
    state = tmp_path / "watchdog.json"
    send = Sender()
    p = mkdb(tmp_path, hb=500, mon=500)                                  # процесс молчит
    r = wd.run_once(p, state, ENV, send, NOW)
    assert r["status"] == wd.PROCESS_SILENT and r["sent"] and len(send.calls) == 1
    assert send.calls[0][:2] == ("123456:SECRETTOKEN", "42") and "процесс бота молчит" in send.calls[0][2]
    assert json.loads(state.read_text(encoding="utf-8"))["status"] == wd.PROCESS_SILENT
    r = wd.run_once(p, state, ENV, send, NOW + timedelta(minutes=5))      # через 5 минут то же самое
    assert not r["sent"] and len(send.calls) == 1                         # дубль не шлётся
    # бот ожил: свежие пульсы
    st = Store(p)
    st.set("heartbeat", iso(-290))
    st.set("monitor_ok_ts", iso(-290))
    st.close()
    r = wd.run_once(p, state, ENV, send, NOW + timedelta(minutes=5, seconds=10))
    assert r["status"] == wd.OK and r["sent"] and "снова на связи" in send.calls[-1][2] and len(send.calls) == 2
    r = wd.run_once(p, state, ENV, send, NOW + timedelta(minutes=5, seconds=20))
    assert not r["sent"] and len(send.calls) == 2


def test_run_once_exchange_down(tmp_path):
    send = Sender()
    p = mkdb(tmp_path, hb=20, mon=900)
    r = wd.run_once(p, tmp_path / "w.json", ENV, send, NOW)
    assert r["status"] == wd.EXCHANGE_SILENT and "биржа не отвечает" in send.calls[0][2]


def test_failed_send_is_retried_next_time(tmp_path):
    state = tmp_path / "w.json"
    p = mkdb(tmp_path, hb=500, mon=500)
    bad = Sender(ok=False)
    r = wd.run_once(p, state, ENV, bad, NOW)
    assert r["sent"] is False and not state.exists()                      # не доставлено - состояние не записано
    good = Sender()
    r = wd.run_once(p, state, ENV, good, NOW + timedelta(minutes=5))
    assert r["sent"] and len(good.calls) == 1


def test_missing_credentials_never_send(tmp_path):
    send = Sender()
    p = mkdb(tmp_path, hb=500, mon=500)
    r = wd.run_once(p, tmp_path / "w.json", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_OWNER_ID": ""}, send, NOW)
    assert not r["sent"] and send.calls == []


# ---------- окружение ----------
def test_read_env_parses_file_and_prefers_environment(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# комментарий\nBYBIT_API_SECRET=NEVERREAD\nTELEGRAM_BOT_TOKEN="111:AAA"  \nTELEGRAM_OWNER_ID=42 # владелец\n', encoding="utf-8")
    assert wd.read_env(f, {}) == {"TELEGRAM_BOT_TOKEN": "111:AAA", "TELEGRAM_OWNER_ID": "42"}
    assert wd.read_env(f, {"TELEGRAM_BOT_TOKEN": "999:ZZZ", "TELEGRAM_OWNER_ID": "7"}) == {"TELEGRAM_BOT_TOKEN": "999:ZZZ", "TELEGRAM_OWNER_ID": "7"}
    assert wd.read_env(tmp_path / "нет.env", {}) == {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_OWNER_ID": ""}


def test_main_without_credentials_exits_4_and_prints_no_secret(monkeypatch, capsys):
    monkeypatch.setattr(wd, "read_env", lambda *_: {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_OWNER_ID": ""})
    assert wd.main(["--once"]) == 4
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().out


def test_main_once_prints_status_without_secrets(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(wd, "read_env", lambda *_: dict(ENV))
    # main() берёт настоящее время, поэтому пульсы в БД считаются от него, а не от фиксированного NOW
    monkeypatch.setattr(wd, "DB_PATH", mkdb(tmp_path, hb=10, mon=10, now=datetime.now(timezone.utc)))
    monkeypatch.setattr(wd, "STATE_PATH", tmp_path / "w.json")
    monkeypatch.setattr(wd, "send_telegram", lambda *a: pytest.fail("при норме ничего не отправляется"))
    assert wd.main(["--once"]) == 0
    out = capsys.readouterr().out
    assert "ok" in out and "SECRETTOKEN" not in out and "42" not in out.replace("heartbeat", "")


def test_send_telegram_posts_without_leaking(monkeypatch):
    import httpx
    seen = {}

    def post(url, json=None, timeout=None):
        seen.update(url=url, json=json)
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))
    monkeypatch.setattr(wd.httpx, "post", post)
    assert wd.send_telegram("1:T", "42", "привет") is True
    assert seen["url"] == "https://api.telegram.org/bot1:T/sendMessage" and seen["json"] == {"chat_id": "42", "text": "привет"}

    def boom(*a, **k):
        raise httpx.ConnectError("нет сети")
    monkeypatch.setattr(wd.httpx, "post", boom)
    assert wd.send_telegram("1:T", "42", "x") is False


def test_docstring_has_exact_schtasks_command():
    line = next(l for l in wd.__doc__.splitlines() if "schtasks /Create" in l)
    assert '/SC MINUTE /MO 5' in line and 'watchdog.py' in line and '--once' in line and '/TN "tradebot-watchdog"' in line
