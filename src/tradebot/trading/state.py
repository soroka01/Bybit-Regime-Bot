"""SQLite (WAL) состояние бота. Запись — класс Store (один процесс, lock); чтение для веба — read_snapshot()
(отдельное read-only соединение, безопасно из любого потока)."""
from __future__ import annotations

import dataclasses
import json
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradebot.core.portfolio import DEFAULT_LEGS, Leg
from tradebot.core.strategies import STRATEGIES
from tradebot.paths import STATE, STORAGE
from tradebot.trading.config import MAX_LEVERAGE

DB_PATH = STATE / "bot.db"
BACKUP_DIR = STORAGE / "backups" / "db"   # ежедневные копии bot.db (sqlite backup API): bot_YYYYMMDD.db
BACKUP_KEEP = 14
SIGNALS_KEEP_DAYS = 90                    # журнал сигналов старше — удаляется (последняя строка по каждому ключу остаётся всегда)
EVENTS_KEEP_DAYS = 400                    # журнал событий старше — удаляется; trades/roundtrips/equity не чистятся

DEFAULTS = {
    "enabled": False, "leverage": 1.0, "stop_pct": 0.08,
    "kill_dd": 0.25, "kill_daily": 0.10, "legs": {}, "legs_catalog": None,
}
# Старая настройка s_allocated_capital («выделенный капитал») удалена: бот торгует всем балансом счёта. Ключ может лежать
# в БД — settings()/read_snapshot итерируют DEFAULTS и его не читают; в БД его никто не стирает и не мигрирует.
MAX_LEGS = 10                 # индекс ноги в callback Telegram — одна цифра (keyboards.PATTERNS: lg:[0-9])
MAX_OPEN_PROPOSALS = 5        # предложений «ждут решения» одновременно (защита от заспамленного бота)
PROPOSAL_TTL_S = 24 * 3600    # старше — игнорируются
# допустимые диапазоны (серверная валидация для любых источников — Telegram, код)
RANGES = {"leverage": (1.0, float(MAX_LEVERAGE)),
          "stop_pct": (0.01, 0.30), "kill_dd": (0.05, 0.50), "kill_daily": (0.02, 0.30)}

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY, ts TEXT, bar TEXT, key TEXT, signal REAL, target REAL);
CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, ts TEXT, side TEXT, qty REAL, price REAL, reason TEXT, order_id TEXT);
CREATE TABLE IF NOT EXISTS equity(ts TEXT, v REAL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts TEXT, level TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY, ts TEXT, source TEXT, status TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS roundtrips(id INTEGER PRIMARY KEY, ts_open TEXT, ts_close TEXT, side TEXT, qty REAL, entry REAL, exit REAL,
    pnl REAL, fees REAL, funding REAL, equity_open REAL, eq_est INTEGER, approx INTEGER, kind TEXT, reason TEXT, order_id TEXT);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity(ts);
CREATE INDEX IF NOT EXISTS idx_signals_key_id ON signals(key, id);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""
RT_COLS = ("ts_open", "ts_close", "side", "qty", "entry", "exit", "pnl", "fees", "funding", "equity_open", "eq_est", "approx",
           "kind", "reason", "order_id")


NO_ORDERS_MSG = "Торговля заблокирована: бот запущен без права отправлять ордера (проверьте настройки запуска)"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def legs_catalog(s: dict) -> list[Leg]:
    """Список ног бота. Пока пользователь ничего не добавлял и не удалял (s['legs_catalog'] is None) — штатные DEFAULT_LEGS.
    После первого добавления/удаления в БД хранится полный список определений (стратегия, tf, параметры, long_only, warmup);
    вес и «включена» живут отдельно (s['legs']), а вес в самом определении всегда 0 — без явного переопределения нога не торгует.
    Мусор в БД пропускается: он не должен ронять бота."""
    raw = s.get("legs_catalog")
    if raw is None:
        return list(DEFAULT_LEGS)
    out: list[Leg] = []
    seen: set[str] = set()
    for d in raw:
        try:
            leg = Leg(str(d["strategy"]), str(d["tf"]), dict(d["params"]), 0.0, bool(d.get("long_only", False)), int(d.get("warmup", 0)))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if leg.key not in seen:
            seen.add(leg.key)
            out.append(leg)
    return out


def leg_def(leg: Leg) -> dict:
    return {"strategy": leg.strategy, "tf": leg.tf, "params": leg.params, "long_only": leg.long_only, "warmup": leg.warmup}


def resolve_weights(legs: list[Leg], ov: dict) -> tuple[dict[str, tuple[float, bool]], list[str]]:
    """Эффективные (вес, включена) по ногам из дефолтов и сохранённых переопределений ov, плюс список пояснений.
    Гарантия: сумма весов ВКЛЮЧЁННЫХ ног <= 1.
    1) Нога с сохранённым переопределением веса сохраняет его (пользователь настроил осознанно).
    2) Нога БЕЗ переопределения (например, добавленная новым релизом) получает свой дефолтный вес, но все такие ноги
       вместе не больше остатка (1 - сумма явных весов включённых ног): дефолты пропорционально уменьшаются. Так новая
       нога не меняет настроенный портфель (остаток 0 -> вес 0), а на чистой БД дефолты делят 100% поровну.
    3) Если сумма ЯВНЫХ весов сама > 1 (ручная правка БД) — все веса нормируются пропорционально (w / сумма)."""
    notes: list[str] = []
    eff: dict[str, tuple[float, bool]] = {}
    explicit = {l.key for l in legs if "weight" in (ov.get(l.key) or {})}
    for l in legs:
        o = ov.get(l.key) or {}
        try:
            w = float(o["weight"]) if l.key in explicit else float(l.weight)
        except (TypeError, ValueError):
            w = 0.0                          # мусор в БД -> нога выключена по весу, а не падение
        if not (w == w) or w < 0 or w == float("inf"):
            w = 0.0
        eff[l.key] = (w, bool(o.get("enabled", True)))
    tot_explicit = sum(eff[k][0] for k in explicit if eff[k][1])
    if tot_explicit > 1.0 + 1e-9:
        for k in explicit:
            if eff[k][1]:
                eff[k] = (math.floor(eff[k][0] / tot_explicit * 1e6) / 1e6, True)
        notes.append(f"сумма сохранённых весов {tot_explicit:.0%} > 100% — веса нормированы пропорционально")
        room = 0.0
    else:
        room = 1.0 - tot_explicit
    loose = [l for l in legs if l.key not in explicit and eff[l.key][1]]
    want = sum(eff[l.key][0] for l in loose)
    if want > max(room, 0.0) + 1e-9:
        f = max(room, 0.0) / want
        for l in loose:
            w = eff[l.key][0]
            eff[l.key] = (math.floor(w * f * 1e6) / 1e6, True)  # вниз: сумма заведомо не превысит 1
            notes.append(f"{l.key}: вес по умолчанию {w:.0%} ограничен до {eff[l.key][0]:.0%} (сумма весов не больше 100%)")
    return eff, notes


def validate_setting(key: str, value):
    """Приводит и проверяет значение настройки; ValueError при выходе за диапазон."""
    if key == "enabled":
        return bool(value)
    if key in RANGES:
        v = float(value)
        lo, hi = RANGES[key]
        if v != v or not (lo - 1e-12 <= v <= hi + 1e-12):
            raise ValueError(f"{key}: {v} вне диапазона [{lo}, {hi}]")
        return round(v, 6)
    raise ValueError(f"неизвестная настройка {key}")


class Store:
    def __init__(self, path: Path | str = DB_PATH, paper: bool = False):
        self.paper = paper  # PAPER: enabled принудительно False и не включается
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)

    def _x(self, sql: str, args=()):
        with self._lock:
            return self._db.execute(sql, args)

    def close(self):
        with self._lock:
            self._db.close()

    # --- kv ---
    def get(self, k: str, default=None):
        r = self._x("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def set(self, k: str, v) -> None:
        self._x("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    def set_many(self, items: dict) -> None:
        """Несколько записей kv ОДНОЙ транзакцией (всё или ничего). Нельзя вызывать внутри другой транзакции (_txn)."""
        with self._txn():
            for k, v in items.items():
                self.set(k, v)

    def set_acted(self, exposure: float | None, qty: float | None, latch: float | None = None) -> None:
        """Инвариант: acted_exposure (последняя исполненная цель) и acted_qty (удерживаемый размер в BTC со знаком) пишутся
        ТОЛЬКО парой и одной транзакцией — сбой между двумя записями не должен оставить цель без размера (или наоборот).
        latch не None -> в той же транзакции ставится защёлка (latch=True, latch_exposure=latch)."""
        items = {"acted_exposure": exposure, "acted_qty": qty}
        if latch is not None:
            items.update(latch=True, latch_exposure=latch)
        self.set_many(items)

    def clear_acted(self) -> None:
        """Забыть «отработанную» цель: следующая сверка пересчитает размер от свежего баланса счёта."""
        self.set_acted(None, None)

    def set_latch(self, exposure: float) -> None:
        """Защёлка после закрытия биржей: latch и latch_exposure — парой, одной транзакцией."""
        self.set_many({"latch": True, "latch_exposure": exposure})

    def mark_killed(self, reason: str) -> None:
        """kill-switch: торговля выключена + killed + причина ОДНОЙ транзакцией (иначе сбой между записями оставил бы
        enabled=False без killed — и владелец мог бы включить торговлю кнопкой, не зная о срабатывании)."""
        self.set_many({"s_enabled": False, "killed": True, "kill_reason": reason})

    def next_seq(self) -> int:
        with self._lock:
            n = int(self.get("order_seq", 0)) + 1
            self.set("order_seq", n)
            return n

    def seed_order_seq(self, now: float | None = None) -> int:
        """Инвариант: порядковый номер в orderLinkId не повторяется НИКОГДА. Дубль биржа отклонила бы кодом 110072, а исполнитель
        трактует 110072 как «ордер уже существует» — то есть новый ордер молча принялся бы за старый. Поэтому при старте счётчик
        поднимается до max(сохранённый order_seq, max(trades.id), текущее время в секундах): восстановленная из копии или старая
        БД не вернёт его назад. Возвращает новое значение."""
        with self._lock:
            mx = self._db.execute("SELECT MAX(id) FROM trades").fetchone()[0] or 0
            n = max(int(self.get("order_seq", 0)), int(mx), int(now if now is not None else time.time()))
            self.set("order_seq", n)
            return n

    # --- настройки ---
    def settings(self) -> dict:
        s = {k: self.get("s_" + k, d) for k, d in DEFAULTS.items()}
        s["leverage"] = min(float(s["leverage"]), float(MAX_LEVERAGE))
        if self.paper:
            s["enabled"] = False
        return s

    def set_setting(self, key: str, value) -> None:
        if self.paper and key == "enabled" and value:
            raise ValueError(NO_ORDERS_MSG)
        self.set("s_" + key, validate_setting(key, value))

    @contextmanager
    def _txn(self):
        """Несколько записей kv одной транзакцией: ноги не должны оказаться наполовину изменёнными."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def catalog(self) -> list[Leg]:
        return legs_catalog(self.settings())

    def leg_cfg(self) -> list[tuple[Leg, float, bool]]:
        """(нога, эффективный вес, включена). Веса уже приведены resolve_weights: сумма включённых <= 1."""
        s = self.settings()
        cat = legs_catalog(s)
        eff, _ = resolve_weights(cat, s["legs"] or {})
        return [(l, eff[l.key][0], eff[l.key][1]) for l in cat]

    def weight_notes(self) -> list[str]:
        """Пояснения, если эффективные веса отличаются от сохранённых/дефолтных (пусто — всё как настроено)."""
        s = self.settings()
        cat = legs_catalog(s)
        eff, notes = resolve_weights(cat, s["legs"] or {})
        notes += [f"{l.key}: такой стратегии нет в этой версии бота — нога пропущена (её можно удалить в «Стратегии»)"
                  for l in cat if l.strategy not in STRATEGIES and eff[l.key][1] and eff[l.key][0] > 0]
        return notes

    def effective_legs(self) -> list[Leg]:
        return [dataclasses.replace(l, weight=w) for l, w, en in self.leg_cfg() if en and w > 0 and l.strategy in STRATEGIES]

    def set_leg(self, key: str, weight: float | None = None, enabled: bool | None = None) -> None:
        """Меняет ногу; сумма весов включённых ног не должна превышать 1."""
        with self._lock:
            cfg = {l.key: [w, en] for l, w, en in self.leg_cfg()}
            if key not in cfg:
                raise ValueError("нет такой стратегии")
            if weight is not None:
                if not (0.0 <= weight <= 1.0):
                    raise ValueError("вес вне 0..1")
                cfg[key][0] = round(weight, 2)
            if enabled is not None:
                cfg[key][1] = bool(enabled)
            if sum(w for w, en in cfg.values() if en) > 1.0 + 1e-9:
                raise ValueError("сумма весов включённых стратегий больше 100%")
            self.set("s_legs", {k: {"weight": w, "enabled": en} for k, (w, en) in cfg.items()})

    def add_leg(self, leg: Leg, weight: float, others: dict[str, float] | None = None) -> bool:
        """Добавляет ногу или заменяет одноимённую (параметры, long_only, warmup; включена). others — новые веса других ног
        (для «Поровну»). Сумма весов включённых <= 1, ног не больше MAX_LEGS. True — нога с таким ключом уже была."""
        with self._lock:
            cat = self.catalog()
            replaced = leg.key in {l.key for l in cat}
            if not replaced and len(cat) >= MAX_LEGS:
                raise ValueError(f"не больше {MAX_LEGS} стратегий: сначала удалите ненужную")
            if not (0.0 <= weight <= 1.0):
                raise ValueError("вес вне 0..1")
            cfg = {l.key: [w, en] for l, w, en in self.leg_cfg()}
            for k, w in (others or {}).items():
                if k not in cfg or k == leg.key or not (0.0 <= w <= 1.0):
                    raise ValueError("нет такой стратегии или вес вне 0..1")
                cfg[k][0] = round(w, 2)
            cfg[leg.key] = [round(weight, 2), True]
            if sum(w for w, en in cfg.values() if en) > 1.0 + 1e-9:
                raise ValueError("сумма весов включённых стратегий больше 100%")
            defs = [leg_def(leg) if l.key == leg.key else leg_def(l) for l in cat]
            if not replaced:
                defs.append(leg_def(leg))
            with self._txn():
                self.set("s_legs_catalog", defs)
                self.set("s_legs", {k: {"weight": w, "enabled": en} for k, (w, en) in cfg.items()})
            return replaced

    def remove_leg(self, key: str) -> None:
        """Удаляет ногу целиком; её вес и «включена» стираются. Позицию на бирже не трогает — цель пересчитает раннер."""
        with self._lock:
            cat = self.catalog()
            if key not in {l.key for l in cat}:
                raise ValueError("нет такой стратегии")
            cfg = {l.key: [w, en] for l, w, en in self.leg_cfg() if l.key != key}      # веса оставшихся фиксируем явно (дефолты не теряем)
            with self._txn():
                self.set("s_legs_catalog", [leg_def(l) for l in cat if l.key != key])
                self.set("s_legs", {k: {"weight": w, "enabled": en} for k, (w, en) in cfg.items()})

    # --- предложения ног (текст из Telegram или кнопка «В бота» на сайте) ---
    def add_proposal(self, source: str, payload: dict) -> int:
        cur = self._x("INSERT INTO proposals(ts,source,status,payload) VALUES(?,?,?,?)", (now_iso(), source, "new", json.dumps(payload)))
        return int(cur.lastrowid)

    def proposal(self, pid: int) -> dict | None:
        r = self._x("SELECT id,ts,source,status,payload FROM proposals WHERE id=?", (pid,)).fetchone()
        return _proposal_row(r) if r else None

    def set_proposal(self, pid: int, status: str | None = None, payload: dict | None = None) -> None:
        with self._lock:
            if status is not None:
                self._x("UPDATE proposals SET status=? WHERE id=?", (status, pid))
            if payload is not None:
                self._x("UPDATE proposals SET payload=? WHERE id=?", (json.dumps(payload), pid))

    def close_proposal(self, pid: int, status: str) -> bool:
        """Атомарно переводит ожидающее предложение (new/announced) в итоговый статус; False — оно уже обработано/устарело."""
        cur = self._x("UPDATE proposals SET status=? WHERE id=? AND status IN ('new','announced')", (status, pid))
        return cur.rowcount == 1

    def expire_proposals(self) -> None:
        since = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - PROPOSAL_TTL_S, timezone.utc).isoformat(timespec="seconds")
        self._x("UPDATE proposals SET status='expired' WHERE status IN ('new','announced') AND ts<?", (since,))

    def proposals(self, status: str, limit: int = 20, source: str | None = None) -> list[dict]:
        q, args = "SELECT id,ts,source,status,payload FROM proposals WHERE status=?", [status]
        if source:
            q, args = q + " AND source=?", args + [source]
        return [_proposal_row(r) for r in self._x(q + " ORDER BY id LIMIT ?", (*args, limit)).fetchall()]

    def claim_proposal(self, pid: int) -> bool:
        """Атомарно new -> announced (кто успел — тот и объявляет); False — предложение уже обработано или объявлено."""
        return self._x("UPDATE proposals SET status='announced' WHERE id=? AND status='new'", (pid,)).rowcount == 1

    def unclaim_proposal(self, pid: int) -> None:
        """Отправка уведомления не удалась: announced -> new (закрытые предложения не трогаем)."""
        self._x("UPDATE proposals SET status='new' WHERE id=? AND status='announced'", (pid,))

    def open_proposals(self) -> int:
        return _open_proposals(self._db)

    # --- журналы ---
    def log_signals(self, bar: str, sigs: dict[str, float | None], target: float | None) -> None:
        ts = now_iso()
        with self._lock:
            for k, v in sigs.items():
                self._x("INSERT INTO signals(ts,bar,key,signal,target) VALUES(?,?,?,?,?)", (ts, bar, k, v, target))

    def latest_signals(self) -> dict[str, tuple[float | None, str]]:
        return _latest_signals(self._db)

    def recent_signals(self, n: int = 20, offset: int = 0) -> list[tuple]:
        return self._x("SELECT bar,key,signal,target FROM signals ORDER BY id DESC LIMIT ? OFFSET ?", (n, offset)).fetchall()

    def add_trade(self, side: str, qty: float, price: float, reason: str, order_id: str) -> int:
        cur = self._x("INSERT INTO trades(ts,side,qty,price,reason,order_id) VALUES(?,?,?,?,?,?)",
                      (now_iso(), side, qty, price, reason, order_id))
        return int(cur.lastrowid)

    def all_trades(self) -> list[dict]:
        """Все исполнения по возрастанию id (для реплея P/L)."""
        rows = self._x("SELECT ts,side,qty,price,reason,order_id FROM trades ORDER BY id").fetchall()
        return [dict(zip(("ts", "side", "qty", "price", "reason", "order_id"), r)) for r in rows]

    # --- закрытые сделки (круговые: flat -> позиция -> flat) ---
    def add_roundtrip(self, **kw) -> int:
        """Запись закрытой сделки: точная (с биржи) или оценка (approx=1). Лишние ключи — ошибка (опечатки ловим сразу)."""
        bad = set(kw) - set(RT_COLS)
        if bad:
            raise ValueError(f"неизвестные поля {sorted(bad)}")
        row = {c: kw.get(c) for c in RT_COLS}
        cur = self._x(f"INSERT INTO roundtrips({','.join(RT_COLS)}) VALUES({','.join('?' * len(RT_COLS))})",
                      tuple(row[c] for c in RT_COLS))
        return int(cur.lastrowid)

    def update_roundtrip(self, rid: int, **kw) -> None:
        bad = set(kw) - set(RT_COLS)
        if bad or not kw:
            raise ValueError(f"неизвестные поля {sorted(bad)}")
        self._x(f"UPDATE roundtrips SET {','.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), rid))

    def roundtrip_rows(self) -> list[dict]:
        rows = self._x(f"SELECT id,{','.join(RT_COLS)} FROM roundtrips ORDER BY id").fetchall()
        return [dict(zip(("id", *RT_COLS), r)) for r in rows]

    def apply_backfill(self, plan: list[dict]) -> dict:
        """Записывает план pnl.plan_backfill в roundtrips одной транзакцией. Идемпотентно: строка с этим order_id и ТОЧНЫМ итогом
        (approx=0, pnl задан) не трогается никогда; оценочная — заменяется точной; нет строки — создаётся (эквити на входе — по кривой
        equity, помечено оценкой). Возвращает {inserted, updated, skipped}."""
        res = {"inserted": 0, "updated": 0, "skipped": 0}
        if not plan:
            return res
        with self._lock:
            with self._txn():
                for it in plan:
                    row = self._db.execute("SELECT id,pnl,approx FROM roundtrips WHERE order_id=? ORDER BY id LIMIT 1",
                                           (it["order_id"],)).fetchone()
                    if row is not None and row[2] == 0 and row[1] is not None:
                        res["skipped"] += 1
                        continue
                    vals = {"ts_open": it["ts_open"], "ts_close": it["ts_close"], "side": it["side"], "qty": it["qty"],
                            "entry": it["entry"], "exit": it["exit"], "pnl": it["pnl"], "fees": it["fees"], "funding": it["funding"],
                            "approx": 0, "kind": "close", "reason": it.get("reason"), "order_id": it["order_id"]}
                    if row is not None:
                        sets = ",".join(f"{k}=?" for k in vals)
                        self._db.execute(f"UPDATE roundtrips SET {sets} WHERE id=?", (*vals.values(), row[0]))
                        res["updated"] += 1
                    else:
                        vals.update(equity_open=self.equity_before(it["ts_open"]), eq_est=1)
                        cols = list(vals)
                        self._db.execute(f"INSERT INTO roundtrips({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                                         tuple(vals.values()))
                        res["inserted"] += 1
        return res

    def equity_before(self, ts_iso: str) -> float | None:
        """Ближайшая ПРЕДШЕСТВУЮЩАЯ точка кривой equity (ISO-времена в одном формате сортируются как время)."""
        r = self._x("SELECT v FROM equity WHERE ts<=? ORDER BY ts DESC LIMIT 1", (ts_iso,)).fetchone()
        return float(r[0]) if r and r[0] is not None else None

    def stop_breaks(self) -> list[float]:
        """Моменты (unix-секунды), когда позицию закрыла биржа мимо ленты исполнений: события «Позиция исчезла …». Для реплея
        trades (иначе средняя цена и время входа смешают позицию до и после стоп-аута). Уточнение по стоп-ауту (followup)
        этой подстроки не содержит и вторым разрывом не считается.
        ИЗВЕСТНЫЙ РИСК (отложено, T3b): разрывы берутся из последних 2000 событий; при очень насыщенном журнале событий старый
        «Позиция исчезла» выпадет из окна, и реплей смешает позицию до и после того стоп-аута. Правильное решение — выводить
        разрывы из roundtrips.kind (стоп-ауты пишутся туда), но это меняет учёт P/L и сделано не было."""
        from tradebot.trading import pnl as pnl_mod
        return [t for t in (pnl_mod.parse_ts(e["ts"]) for e in self.events(2000) if "озиция исчезла" in e["text"]) if t]

    def closed_trips(self) -> list[dict]:
        """Закрытые сделки, новые сверху: таблица roundtrips + реконструкция по trades для более ранней истории (pnl.merge_trips).
        Для реконструкции доля счёта берётся из кривой equity на момент открытия (если точка есть)."""
        from tradebot.trading import pnl as pnl_mod
        table = self.roundtrip_rows()
        _, derived, _ = pnl_mod.replay(self.all_trades(), breaks=self.stop_breaks())
        out = pnl_mod.merge_trips(table, derived)
        for r in out:
            if r.get("equity_open") is None and r.get("ts_open"):
                r["equity_open"] = self.equity_before(r["ts_open"])
        return out

    def trades(self, n: int = 50, offset: int = 0) -> list[dict]:
        return _trades(self._db, n, offset)

    def count(self, table: str) -> int:
        assert table in ("trades", "events", "signals")
        return self._x(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def add_equity(self, v: float, ts: str | None = None) -> None:
        self._x("INSERT INTO equity(ts,v) VALUES(?,?)", (ts or now_iso(), float(v)))

    def add_event(self, level: str, text: str) -> None:
        self._x("INSERT INTO events(ts,level,text) VALUES(?,?,?)", (now_iso(), level, text))

    def events(self, n: int = 100, offset: int = 0) -> list[dict]:
        return _events(self._db, n, offset)

    # --- обслуживание БД: ежедневная копия -> чистка журналов -> усечение WAL (именно в таком порядке) ---
    @staticmethod
    def backup_file(backup_dir: Path | str, day: datetime) -> Path:
        return Path(backup_dir) / f"bot_{day:%Y%m%d}.db"

    def backup_due(self, backup_dir: Path | str = BACKUP_DIR, now: datetime | None = None) -> bool:
        return not self.backup_file(backup_dir, now or datetime.now(timezone.utc)).exists()

    def backup(self, backup_dir: Path | str = BACKUP_DIR, now: datetime | None = None, keep: int = BACKUP_KEEP) -> Path:
        """Копия bot.db за сегодня (sqlite3 backup API с ОТДЕЛЬНОГО read-only соединения: торговые записи не блокируются, копия
        согласована). Пишется во временный файл, проверяется quick_check и только потом атомарно переименовывается — полурабочая
        копия под настоящим именем не появится. Остаются последние `keep` копий. Исключение при любом сбое."""
        now = now or datetime.now(timezone.utc)
        d = Path(backup_dir)
        d.mkdir(parents=True, exist_ok=True)
        dest = self.backup_file(d, now)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.unlink(missing_ok=True)
        src = sqlite3.connect(f"file:{Path(self.path).as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)
                if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("quick_check копии не прошёл")
            finally:
                dst.close()
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            src.close()
        os.replace(tmp, dest)
        for old in sorted(d.glob("bot_????????.db"))[:-keep]:
            old.unlink(missing_ok=True)
        return dest

    def purge(self, now: datetime | None = None) -> dict:
        """Чистка журналов: signals старше SIGNALS_KEEP_DAYS (последняя строка по каждому key остаётся независимо от возраста —
        по ней считается «прежний» сигнал) и events старше EVENTS_KEEP_DAYS. trades/roundtrips/equity не трогаются.
        Вызывать только после успешной копии (см. maintenance)."""
        now = now or datetime.now(timezone.utc)
        cut_s = (now - timedelta(days=SIGNALS_KEEP_DAYS)).isoformat(timespec="seconds")
        cut_e = (now - timedelta(days=EVENTS_KEEP_DAYS)).isoformat(timespec="seconds")
        with self._lock:
            with self._txn():
                s = self._db.execute("DELETE FROM signals WHERE ts<? AND id NOT IN (SELECT MAX(id) FROM signals GROUP BY key)",
                                     (cut_s,)).rowcount
                e = self._db.execute("DELETE FROM events WHERE ts<?", (cut_e,)).rowcount
        return {"signals": s, "events": e}

    def checkpoint(self) -> None:
        """Усечь WAL-файл (TRUNCATE). Отдельное соединение с коротким ожиданием (1 с): ожидание читателей не должно держать
        блокировку Store и тормозить торговый поток. Если читатель (веб) держит снимок, SQLite усечёт сколько сможет — это
        не ошибка."""
        db = sqlite3.connect(self.path, timeout=1.0)
        try:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        finally:
            db.close()

    def maintenance(self, backup_dir: Path | str = BACKUP_DIR, now: datetime | None = None) -> dict:
        """Раз в сутки (если копии за сегодня ещё нет): копия -> чистка -> усечение WAL. ПОРЯДОК важен: чистка удаляет данные,
        поэтому выполняется только после УСПЕШНОЙ копии; усечение WAL — последним. Блокирующий вызов: запускать в потоке."""
        out: dict = {"ran": False}
        if not self.backup_due(backup_dir, now):
            return out
        out["ran"] = True
        try:
            out["backup"] = str(self.backup(backup_dir, now))
        except Exception as e:  # копия не удалась: чистку пропускаем, данные не теряем
            out["error"] = f"{type(e).__name__}: {e}"
        else:
            out["purged"] = self.purge(now)
        try:
            self.checkpoint()
        except Exception as e:
            out.setdefault("error", f"{type(e).__name__}: {e}")
        return out


def _proposal_row(r) -> dict:
    try:
        payload = json.loads(r[4])
    except (TypeError, ValueError):
        payload = {}
    return {"id": r[0], "ts": r[1], "source": r[2], "status": r[3], "payload": payload}


def _open_proposals(db) -> int:
    """Предложений, ждущих решения (new/announced) и не старше PROPOSAL_TTL_S."""
    since = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - PROPOSAL_TTL_S, timezone.utc).isoformat(timespec="seconds")
    return int(db.execute("SELECT COUNT(*) FROM proposals WHERE status IN ('new','announced') AND ts>=?", (since,)).fetchone()[0])


class ProposalError(Exception):
    """Предложение нельзя принять; code: no_bot (нет БД) | old_bot (нет таблицы) | too_many (очередь полна)."""

    def __init__(self, msg: str, code: str = "no_bot"):
        super().__init__(msg)
        self.code = code


def heartbeat_age(path: Path | str | None = None) -> float | None:
    """Секунд с последнего сигнала жизни бота (по БД, только чтение); None — БД нет или бот не писал пульс."""
    path = path or DB_PATH
    try:
        if not Path(path).exists():
            return None
        db = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=3)
        try:
            r = db.execute("SELECT v FROM kv WHERE k='heartbeat'").fetchone()
        finally:
            db.close()
        hb = datetime.fromisoformat(json.loads(r[0])) if r else None
    except (sqlite3.Error, ValueError, TypeError):
        return None
    if hb is None:
        return None
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - hb).total_seconds())


def submit_proposals(payloads: list[dict], path: Path | str | None = None, source: str = "web") -> list[int]:
    """Запись предложений из веб-потока (отдельное короткое соединение к БД бота, без DDL: таблицу создаёт Store при старте
    бота). БД нет — бот не запускался; таблицы нет — бот старой версии, перезапустите; ждущих решения больше
    MAX_OPEN_PROPOSALS — отказ. Настройки торговли не меняются: решает владелец в Telegram."""
    path = path or DB_PATH
    if not Path(path).exists():
        raise ProposalError("бот ещё не запускался: нет базы состояния", "no_bot")
    db = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proposals'").fetchone():
            raise ProposalError("бот работает на старой версии: перезапустите run.bat", "old_bot")
        db.execute("BEGIN IMMEDIATE")
        try:
            if _open_proposals(db) + len(payloads) > MAX_OPEN_PROPOSALS:
                raise ProposalError(f"в боте уже ждут решения предложения (максимум {MAX_OPEN_PROPOSALS}) — разберите их в Telegram", "too_many")
            ids = [int(db.execute("INSERT INTO proposals(ts,source,status,payload) VALUES(?,?,?,?)",
                                  (now_iso(), source, "new", json.dumps(p))).lastrowid) for p in payloads]
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        return ids
    finally:
        db.close()


def _latest_signals(db) -> dict:
    rows = db.execute("SELECT key,signal,ts FROM signals WHERE id IN (SELECT MAX(id) FROM signals GROUP BY key)").fetchall()
    return {k: (s, ts) for k, s, ts in rows}


def _trades(db, n, offset=0):
    rows = db.execute("SELECT ts,side,qty,price,reason,order_id FROM trades ORDER BY id DESC LIMIT ? OFFSET ?", (n, offset)).fetchall()
    return [dict(zip(("ts", "side", "qty", "price", "reason", "order_id"), r)) for r in rows]


def _events(db, n, offset=0):
    rows = db.execute("SELECT ts,level,text FROM events ORDER BY id DESC LIMIT ? OFFSET ?", (n, offset)).fetchall()
    return [dict(zip(("ts", "level", "text"), r)) for r in rows]


def _series(db, table, n):
    return [[ts, v] for ts, v in reversed(db.execute(f"SELECT ts,v FROM {table} ORDER BY rowid DESC LIMIT ?", (n,)).fetchall())]


def read_snapshot(path: Path | str = DB_PATH) -> dict:
    """Снимок состояния для веба. Синхронный, только чтение; без БД возвращает значения по умолчанию."""
    db = None
    try:
        if Path(path).exists():
            db = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        db = None

    def kv(k, d=None):
        if db is None:
            return d
        try:
            r = db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return json.loads(r[0]) if r else d
        except sqlite3.Error:
            return d

    s = {k: kv("s_" + k, d) for k, d in DEFAULTS.items()}
    ov = s["legs"] or {}
    cat = legs_catalog(s)
    eff, _ = resolve_weights(cat, ov)
    try:
        sigs = _latest_signals(db) if db is not None else {}
        out = {"trades": _trades(db, 50), "equity_history": _series(db, "equity", 2000), "events": _events(db, 100)} if db is not None else None
    except sqlite3.Error:
        sigs, out = {}, None
    out = out or {"trades": [], "equity_history": [], "events": []}
    legs = []
    for l in cat:
        sg = sigs.get(l.key)
        legs.append({"key": l.key, "strategy": l.strategy, "tf": l.tf, "params": l.params, "long_only": bool(l.long_only),
                     "weight": float(eff[l.key][0]), "enabled": bool(eff[l.key][1]),
                     "signal": sg[0] if sg else None, "updated": sg[1] if sg else None})
    snap = {
        "env": kv("env", "testnet"), "enabled": bool(s["enabled"]), "killed": bool(kv("killed", False)),
        "paper": bool(kv("paper", False)), "paper_plan": kv("paper_plan"), "kill_reason": kv("kill_reason"), "latch": bool(kv("latch", False)),
        "equity": kv("equity"), "peak_equity": kv("peak_equity"), "position": kv("position"),
        "target_exposure": kv("target_exposure"), "last_bar": kv("last_bar"), "heartbeat": kv("heartbeat"),
        "settings": {"leverage": float(s["leverage"]), "max_leverage": MAX_LEVERAGE, "stop_pct": float(s["stop_pct"]),
                     "kill_dd": float(s["kill_dd"]), "kill_daily": float(s["kill_daily"])},
        "legs": legs, **out,
    }
    if db is not None:
        db.close()
    return snap
