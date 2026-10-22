"""Сторож бота: отдельный процесс, который сообщает в Telegram, когда бот замолчал.

Что делает. Раз в запуск читает storage/state/bot.db ТОЛЬКО НА ЧТЕНИЕ (mode=ro) и смотрит два пульса, которые пишет монитор бота:
  * heartbeat      - «процесс жив» (пишется в начале каждого такта монитора, раз в минуту);
  * monitor_ok_ts  - «биржа отвечает» (пишется только после успешного чтения баланса с Bybit).
Любой из них старше 180 секунд -> ОДНО сообщение владельцу:
  * heartbeat старый (или БД нет)            -> «процесс молчит» (run.bat не работает / завис / ПК спит);
  * heartbeat свежий, monitor_ok_ts старый   -> «биржа не отвечает» (бот жив, но Bybit/сеть недоступны).
Повторно то же сообщение не шлётся (состояние в storage/state/watchdog.json); смена вида проблемы шлёт новое; когда пульсы
снова свежие - одно сообщение «восстановился». Если отправить не удалось (нет сети), состояние не обновляется и следующий
запуск попробует снова.

Зависимости: стандартная библиотека + httpx. Токен и id владельца берутся из переменных окружения TELEGRAM_BOT_TOKEN /
TELEGRAM_OWNER_ID, а если их нет - из файла .env в корне проекта (разбирается здесь же, ничего не выводится и не логируется).

Запуск:
    python tools/watchdog.py --once        # одна проверка и выход (для Планировщика заданий)
    python tools/watchdog.py               # бесконечный цикл, проверка раз в 60 секунд (--interval N)

Планировщик заданий Windows (запускает ВЛАДЕЛЕЦ, не бот; раз в 5 минут, без окна консоли, под текущим пользователем).
Выполните в обычной командной строке один раз:

    schtasks /Create /SC MINUTE /MO 5 /TN "tradebot-watchdog" /TR "\\"C:\\Path\\To\\Python\\pythonw.exe\\" \\"C:\\Path\\To\\Bybit-Regime-Bot\\tools\\watchdog.py\\" --once" /F

Проверить:  schtasks /Run /TN "tradebot-watchdog"      Удалить:  schtasks /Delete /TN "tradebot-watchdog" /F
Сторож не зависит от бота: если бот и run.bat остановлены, сторож по-прежнему сообщит «процесс молчит».
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "state" / "bot.db"
STATE_PATH = ROOT / "storage" / "state" / "watchdog.json"
ENV_PATH = ROOT / ".env"
THRESHOLD_S = 180.0
OK, PROCESS_SILENT, EXCHANGE_SILENT = "ok", "process_silent", "exchange_silent"


# ---------- чтение пульсов (только чтение) ----------
def _age(raw: str | None, now: datetime) -> float | None:
    """Возраст ISO-метки в секундах; None - нет метки или она нечитаема."""
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(json.loads(raw))
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t).total_seconds())


def read_ages(db_path: Path | str, now: datetime) -> dict:
    """{'heartbeat': сек|None, 'monitor_ok': сек|None}. БД нет или не открывается -> оба None. Соединение read-only."""
    out = {"heartbeat": None, "monitor_ok": None}
    p = Path(db_path)
    if not p.exists():
        return out
    try:
        db = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            rows = dict(db.execute("SELECT k, v FROM kv WHERE k IN ('heartbeat', 'monitor_ok_ts')").fetchall())
        finally:
            db.close()
    except sqlite3.Error:
        return out
    out["heartbeat"], out["monitor_ok"] = _age(rows.get("heartbeat"), now), _age(rows.get("monitor_ok_ts"), now)
    return out


# ---------- решение (чистая функция) ----------
def classify(ages: dict, threshold: float = THRESHOLD_S) -> str:
    hb, mon = ages.get("heartbeat"), ages.get("monitor_ok")
    if hb is None or hb > threshold:
        return PROCESS_SILENT
    if mon is None or mon > threshold:
        return EXCHANGE_SILENT
    return OK


def _mins(sec: float | None) -> str:
    return "давно (меток нет)" if sec is None else f"{int(sec // 60)} мин назад"


def message(status: str, ages: dict) -> str:
    if status == PROCESS_SILENT:
        return ("Сторож: процесс бота молчит - пульс не обновлялся " + _mins(ages.get("heartbeat")) + ". Проверьте, что run.bat "
                "запущен и компьютер не спит (storage\\logs\\bot.log и run.log). Если позиция была открыта, она остаётся на бирже.")
    if status == EXCHANGE_SILENT:
        return ("Сторож: биржа не отвечает - бот жив, но последнее успешное чтение Bybit было " + _mins(ages.get("monitor_ok"))
                + ". Проверьте сеть и статус Bybit; бот продолжит попытки сам.")
    return "Сторож: бот снова на связи - пульс процесса и чтение биржи в норме."


def decide(ages: dict, prev: dict, now: datetime, threshold: float = THRESHOLD_S) -> tuple[str, str | None, dict]:
    """(статус, текст сообщения или None, новое состояние). Дедупликация: проблема объявляется один раз, пока вид не сменится;
    восстановление - одно сообщение. Состояние prev: {'status', 'alerted'}; новое состояние применяется только после
    успешной отправки (см. run_once)."""
    status = classify(ages, threshold)
    pstatus, palerted = prev.get("status", OK), bool(prev.get("alerted", False))
    stamp = now.isoformat(timespec="seconds")
    if status == OK:
        if pstatus != OK and palerted:
            return status, message(OK, ages), {"status": OK, "alerted": False, "ts": stamp}
        return status, None, {"status": OK, "alerted": False, "ts": prev.get("ts", stamp)}
    if status == pstatus and palerted:
        return status, None, dict(prev)
    return status, message(status, ages), {"status": status, "alerted": True, "ts": stamp}


# ---------- состояние / окружение / отправка ----------
def load_state(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, st: dict) -> None:
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def read_env(env_path: Path, environ: dict | None = None) -> dict:
    """TELEGRAM_BOT_TOKEN / TELEGRAM_OWNER_ID: из окружения, иначе из .env. Значения нигде не выводятся."""
    import os
    environ = os.environ if environ is None else environ
    want = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID")
    out = {k: (environ.get(k) or "").strip() for k in want}
    if all(out.values()):
        return out
    try:
        for line in Path(env_path).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k in want and not out[k]:
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                else:
                    v = v.split(" #", 1)[0].strip()
                out[k] = v
    except OSError:
        pass
    return out


def send_telegram(token: str, chat_id: str, text: str, timeout: float = 15.0) -> bool:
    """Одно сообщение через Bot API. URL содержит токен, поэтому ни он, ни тело ответа не логируются; True - доставлено."""
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=timeout)
        return r.status_code == 200 and bool(r.json().get("ok"))
    except (httpx.HTTPError, ValueError):
        return False


# ---------- одна проверка ----------
def run_once(db_path: Path, state_path: Path, env: dict, send: Callable[[str, str, str], bool] = send_telegram,
             now: datetime | None = None) -> dict:
    """Одна проверка. Возвращает {'status', 'sent', 'ages'} для вывода/тестов. Сообщение уходит не больше одного."""
    now = now or datetime.now(timezone.utc)
    ages = read_ages(db_path, now)
    prev = load_state(state_path)
    status, text, new_state = decide(ages, prev, now)
    sent = False
    if text is not None:
        token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_OWNER_ID", "")
        if token and chat:
            sent = bool(send(token, chat, text))
        if not sent:                                # не доставлено: состояние не трогаем, повтор при следующем запуске
            return {"status": status, "sent": False, "ages": ages}
    if new_state != prev:
        save_state(state_path, new_state)
    return {"status": status, "sent": sent, "ages": ages}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Сторож бота: сообщает в Telegram, если пульс бота или чтение биржи замолчали")
    ap.add_argument("--once", action="store_true", help="одна проверка и выход (для Планировщика заданий)")
    ap.add_argument("--interval", type=float, default=60.0, help="пауза между проверками без --once, секунд")
    args = ap.parse_args(argv)
    env = read_env(ENV_PATH)
    if not (env["TELEGRAM_BOT_TOKEN"] and env["TELEGRAM_OWNER_ID"]):
        print("watchdog: не заданы TELEGRAM_BOT_TOKEN / TELEGRAM_OWNER_ID (окружение или .env)")
        return 4
    while True:
        res = run_once(DB_PATH, STATE_PATH, env)
        hb, mon = res["ages"]["heartbeat"], res["ages"]["monitor_ok"]
        fmt = lambda v: "нет" if v is None else f"{v:.0f}с"  # noqa: E731
        print(f"watchdog: {res['status']} heartbeat={fmt(hb)} monitor_ok={fmt(mon)} sent={res['sent']}")
        if args.once:
            return 0
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    sys.exit(main())
