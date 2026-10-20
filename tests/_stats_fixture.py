"""Детерминированная синтетическая bot.db для тестов статистики (/api/stats): tmp-файл, реальный bot.db не трогается."""
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tradebot.trading import state

UTC = timezone.utc


def ts(s: str) -> float:
    d = datetime.fromisoformat(s)
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).timestamp()


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")


def make_db(path: Path, *, trades=(), equity=(), events=(), kv=None, roundtrips=()) -> Path:
    """trades: (ts_iso, side, qty, price, reason[, order_id]); equity: (ts_iso, v); events: (ts_iso, level, text); kv: dict (значения -> JSON);
    roundtrips: dict со столбцами таблицы roundtrips (итоги закрытых сделок по данным биржи; недостающие поля — NULL)."""
    db = sqlite3.connect(str(path))
    db.executescript(state.SCHEMA)
    for i, tr in enumerate(trades):
        t, side, qty, price, reason = tr[:5]
        db.execute("INSERT INTO trades(ts,side,qty,price,reason,order_id) VALUES(?,?,?,?,?,?)",
                   (t, side, qty, price, reason, tr[5] if len(tr) > 5 else f"x{i}"))
    for r in roundtrips:
        cols = list(state.RT_COLS)
        db.execute(f"INSERT INTO roundtrips({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(r.get(c) for c in cols))
    for t, v in equity:
        db.execute("INSERT INTO equity(ts,v) VALUES(?,?)", (t, v))
    for t, lvl, text in events:
        db.execute("INSERT INTO events(ts,level,text) VALUES(?,?,?)", (t, lvl, text))
    for k, v in (kv or {}).items():
        db.execute("INSERT INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v)))
    db.commit()
    db.close()
    return path


def add_trade(path: Path, t: str, side: str, qty: float, price: float, reason: str = "бар") -> None:
    db = sqlite3.connect(str(path))
    db.execute("INSERT INTO trades(ts,side,qty,price,reason,order_id) VALUES(?,?,?,?,?,?)", (t, side, qty, price, reason, "x"))
    db.commit()
    db.close()


def equity_points(start: str, n: int, v0: float, step: float = 0.0, wiggle=None, every_s: int = 3600):
    """n точек equity с шагом every_s секунд: v0 + step*i + wiggle(i)."""
    t0 = ts(start)
    return [(iso(t0 + every_s * i), round(v0 + step * i + (wiggle(i) if wiggle else 0.0), 6)) for i in range(n)]


# --- «золотая» БД: 40 суток до END синтетического рынка (_synth), цены сделок берутся из его свечей
D0 = "2026-04-22T00:00:00+00:00"          # END(2026-06-01) - 40 суток
NOW1 = ts("2026-06-01T01:00:00+00:00")    # «сейчас»: свеча 00:00 закрыта, есть цена на конец окна
# (смещение в часах от D0 * SCALE, сторона, объём, причина)
PLAN = [
    (24, "buy", 1, "бар"), (60, "sell", 1, "бар"),                                           # лонг
    (84, "sell", 1, "старт/перезапуск, с опозданием 12 мин"), (130, "buy", 1, "бар"),       # шорт
    (150, "buy", 1, "бар"), (170, "sell", 0.4, "бар"), (190, "sell", 0.6, "бар"),            # лонг, частичное закрытие
    (210, "sell", 0.5, "бар"), (220, "sell", 0.5, "бар"), (260, "buy", 1, "закрытие вручную"),  # шорт с добавлением
    (300, "buy", 1, "бар"), (330, "sell", 2, "бар"), (360, "buy", 1, "бар"),                # лонг -> переворот в шорт -> закрытие
    (400, "buy", 1, "бар"),                                                                  # лонг, закроется стопом (событие)
    (440, "sell", 1, "бар"), (470, "buy", 1, "бар"), (520, "buy", 1, "бар"), (545, "sell", 1, "бар"),
    (560, "sell", 1, "бар"), (575, "buy", 1, "бар"),
    (600, "buy", 1, "бар"),                                                                  # открытая позиция
]
SCALE = 1.5
STOP_EVENT_H = 430


def golden_db(path: Path) -> Path:
    from _synth import bars
    close = bars(1, 3000, 11)["close"]            # индекс — часы UTC, как в parquet synth_market
    t0 = ts(D0)

    def px(h: float) -> float:
        return round(float(close.asof(datetime.fromtimestamp(t0 + 3600 * h, UTC))), 2)
    trades = [(iso(t0 + 3600 * SCALE * h + 14), side, q, px(SCALE * h), reason, f"g{i}") for i, (h, side, q, reason) in enumerate(PLAN)]
    last_h = SCALE * PLAN[-1][0]
    equity = equity_points(D0, 960 * 6 + 7, 160.0, 0.0003, lambda i: 3.0 * math.sin(i / 220.0), every_s=600)
    equity = [e for i, e in enumerate(equity) if not 1800 <= i <= 1920]        # бот не работал 20 часов (раз в 10 минут); ряд кончается в NOW1
    events = [(iso(t0 + 1800), "info", "Бот запущен ✅ · счёт 160.00 $ · торговля включена · позиции нет"),
              (iso(t0 + 3600 * SCALE * STOP_EVENT_H + 60), "error", "Позиция исчезла (стоп/ликвидация). Защёлка: не вхожу заново, "
                                                                 "пока целевая экспозиция не изменится.")]
    kv = {"position": {"side": "long", "qty": 1.0, "entry": px(last_h), "upnl": 1.5, "mark": px(960)},
          "s_leverage": 2.0, "s_stop_pct": 0.05, "heartbeat": iso(NOW1 - 60)}
    return make_db(path, trades=trades, equity=equity, events=events, kv=kv, roundtrips=golden_roundtrips(t0))


def golden_roundtrips(t0: float) -> list[dict]:
    """Итоги по данным биржи для части сделок золотой БД (остальные остаются оценкой):
    №1 — по order_id закрывающего fill (g1); №2 — запасное сопоставление по времени (+30 с, сторона, объём); №3 — ОЦЕНОЧНАЯ строка
    (approx=1) игнорируется; №7 (реконструированный стоп) — по времени; строка для №5 слишком далеко по времени и не подходит."""
    def at(h): return t0 + 3600 * SCALE * h + 14
    base = {"qty": 1.0, "eq_est": 0, "kind": "close", "reason": "бар"}
    return [
        {**base, "ts_open": iso(at(24)), "ts_close": iso(at(60)), "side": "long", "pnl": 1.2975, "fees": 0.21, "funding": -0.06, "approx": 0,
         "order_id": "g1"},
        {**base, "ts_open": iso(at(84)), "ts_close": iso(at(130) + 30), "side": "short", "pnl": -0.70, "fees": 0.215, "funding": 0.10,
         "approx": 0, "order_id": "NOPE-2"},
        {**base, "ts_open": iso(at(150)), "ts_close": iso(at(190)), "side": "long", "pnl": 999.0, "fees": 0.2, "funding": 0.0, "approx": 1,
         "order_id": "g6"},
        {**base, "ts_open": iso(at(400)), "ts_close": iso(t0 + 3600 * SCALE * STOP_EVENT_H + 60 + 20), "side": "long", "pnl": -3.3, "fees": 0.45,
         "funding": 0.0, "approx": 0, "kind": "stop", "order_id": "STOP-1"},
        {**base, "ts_open": iso(at(300)), "ts_close": iso(at(330) + 900), "side": "long", "pnl": 777.0, "fees": 0.2, "funding": 0.0, "approx": 0,
         "order_id": "FAR-1"},
    ]
