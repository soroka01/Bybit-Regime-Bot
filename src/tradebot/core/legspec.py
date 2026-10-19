"""Текст «Копировать для бота» -> описания ног + проверка допустимости для живого бота. Чистый модуль (без aiogram/fastapi):
им пользуются и Telegram-бот (сообщение с текстом), и веб-API («В бота» на сайте) — формат один и тот же.

Разбор строго литеральный: строка `Leg(...)` читается через ast.literal_eval по аргументам, eval/exec нет.
Проверка параметров ОТКЛОНЯЕТ, а не подрезает: тихая подмена значения изменила бы стратегию относительно бэктеста."""
from __future__ import annotations

import ast
import difflib
import math
import re
from dataclasses import dataclass, field, replace

from tradebot.core.portfolio import Leg
from tradebot.core.strategies import STRATEGIES, param_specs

LIVE_TFS = ("1h", "4h", "6h")        # живой бот умеет только эти таймфреймы (market_feed.TF_DELTA / FETCH_DAYS)
MAX_TEXT = 4000
MAX_DRAFTS = 8
DEFAULT_WEIGHT = 0.5                  # как в тексте с сайта (Лаборатория/Рейтинг) — это лишь подсказка, вес выбирают в боте


@dataclass
class LegDraft:
    leg: Leg                                                    # параметры приведены к типам стратегии (после validate_leg)
    result: str = ""                                            # «Результат: …» из строки сайта (как есть, обрезано)
    bt_leverage: float | None = None                            # плечо в бэктесте на сайте
    notes: list[str] = field(default_factory=list)              # предупреждения разбора (спот, оверлеи, плечо выше потолка бота…)


def leg_label(leg: Leg) -> str:
    return f"{leg.strategy} · {leg.tf}"


# ---------- разбор текста ----------
_LEV = re.compile(r"(\d+(?:\.\d+)?)\s*[xх×]", re.I)
_NUM = (int, float)


def _is_num(v) -> bool:
    return isinstance(v, _NUM) and not isinstance(v, bool)


def _fields(line: str) -> dict[str, str]:
    """«Ключ: значение | Ключ: значение» -> словарь (значения без пробелов по краям)."""
    out: dict[str, str] = {}
    for part in line.split(" | "):
        k, sep, v = part.partition(":")
        if sep and k.strip():
            out.setdefault(k.strip(), v.strip())
    return out


def _lit(node: ast.AST):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        raise ValueError("в Leg(...) допустимы только литералы (строки, числа, словарь параметров)") from None


def _call_args(node: ast.Call) -> tuple:
    if len(node.args) > 5:
        raise ValueError("в Leg(...) слишком много аргументов")
    names = ("strategy", "tf", "params", "weight", "long_only")
    vals: dict = {}
    for n, a in zip(names, node.args):
        vals[n] = _lit(a)
    for k in node.keywords:
        if k.arg not in names:
            raise ValueError(f"в Leg(...) неизвестный аргумент «{k.arg}»")
        if k.arg in vals:
            raise ValueError(f"в Leg(...) аргумент «{k.arg}» указан дважды")
        vals[k.arg] = _lit(k.value)
    if "strategy" not in vals or "tf" not in vals:
        raise ValueError("в Leg(...) нужны название стратегии и таймфрейм")
    return (vals["strategy"], vals["tf"], vals.get("params", {}), vals.get("weight", DEFAULT_WEIGHT),
            vals.get("long_only", False))


def _parse_leg_line(line: str) -> Leg:
    try:
        node = ast.parse(line.strip(), mode="eval").body
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        raise ValueError("не удалось разобрать строку Leg(...): синтаксическая ошибка") from None
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Leg"):
        raise ValueError("ожидалась строка вида Leg(\"название\", \"4h\", {...}, weight=0.5)")
    strategy, tf, params, weight, long_only = _call_args(node)
    if not isinstance(strategy, str) or not isinstance(tf, str):
        raise ValueError("название стратегии и таймфрейм в Leg(...) должны быть строками")
    if not isinstance(params, dict) or not all(isinstance(k, str) and _is_num(v) for k, v in params.items()):
        raise ValueError("параметры в Leg(...) должны быть словарём «имя: число»")
    if not _is_num(weight) or not math.isfinite(weight):
        raise ValueError("вес в Leg(...) должен быть числом")
    if not isinstance(long_only, bool):
        raise ValueError("long_only в Leg(...) должен быть True или False")
    return Leg(strategy.strip(), tf.strip(), dict(params), float(weight), long_only)


def parse_leg_text(text: str) -> list[LegDraft]:
    """Все ноги из текста (одно сообщение / один POST). ValueError с понятным русским текстом, если ничего не разобрано.
    Ноги берутся ТОЛЬКО из строк `Leg(...)`; человекочитаемая первая строка нужна для предупреждений: символ, рынок, оверлеи,
    плечо и результат бэктеста. Параметры здесь только приведены к виду Leg; допустимость проверяет validate_leg."""
    if not isinstance(text, str):
        raise ValueError("нужен текст")
    if len(text) > MAX_TEXT:
        raise ValueError(f"слишком длинный текст ({len(text)} символов, максимум {MAX_TEXT})")
    lines = [ln for ln in text.replace("\r", "").split("\n") if ln.strip()]
    head: dict[str, str] = {}
    for ln in lines:
        if " | " in ln and any(k in ln for k in ("Таймфрейм:", "Ноги:", "Результат:", "Символ:")):
            head = _fields(ln)
            break
    legs = [_parse_leg_line(ln) for ln in lines if ln.lstrip().startswith("Leg")]
    if not legs:
        raise ValueError("не нашёл строку Leg(...) — скопируйте текст кнопкой «Копировать для бота» на сайте")
    if len(legs) > MAX_DRAFTS:
        raise ValueError(f"за раз можно не больше {MAX_DRAFTS} стратегий (в тексте {len(legs)})")
    symbol = (head.get("Символ") or head.get("Портфель") or "").strip().upper()
    if symbol and symbol != "BTCUSDT":
        raise ValueError(f"бот торгует только BTCUSDT, а в тексте {symbol}")
    notes = [ln.lstrip("#").strip() for ln in lines if ln.lstrip().startswith("#") and ln.lstrip("#").strip()]
    result = re.sub(r"\s+", " ", head.get("Результат", "")).strip()[:300]
    overlays = head.get("Оверлеи", "нет").strip()
    if overlays.lower() not in ("", "нет", "none", "-", "—"):
        notes.append(f"бэктест на сайте шёл с оверлеями ({overlays}); бот применяет только сигнал стратегии — результат другой")
        result = ""
    if head.get("Рынок", "").strip().lower() == "спот":
        notes.append("бэктест на споте (только лонг, без плеча): на перпе результат будет другим")
    if len(legs) > 1 and result:
        notes.append(f"результат на сайте — по всему портфелю: {result}")
        result = ""
    lev = None
    if (m := _LEV.search(head.get("Плечо", ""))):
        lev = float(m.group(1))
    return [LegDraft(leg, result, lev, list(notes)) for leg in legs]


def draft_payload(d: LegDraft) -> dict:
    """Что хранится в таблице proposals (JSON): описание ноги, подсказки с сайта и результат проверки окна (check заполняют позже)."""
    l = d.leg
    return {"leg": {"strategy": l.strategy, "tf": l.tf, "params": l.params, "long_only": l.long_only, "weight": l.weight},
            "result": d.result, "bt_leverage": d.bt_leverage, "notes": d.notes, "check": None}


def leg_from_payload(payload: dict) -> Leg:
    """Нога из payload предложения; warmup берётся из результата проверки окна (check.warmup), если она пройдена."""
    d = payload["leg"]
    check = payload.get("check") or {}
    return Leg(d["strategy"], d["tf"], dict(d["params"]), float(d.get("weight", DEFAULT_WEIGHT)), bool(d.get("long_only", False)),
               int(check.get("warmup", 0)) if check.get("ok") else 0)


# ---------- проверка допустимости ----------
def validate_leg(leg: Leg) -> Leg:
    """Нога, пригодная для живого бота: известная стратегия, поддерживаемый таймфрейм, параметры в границах слайдеров сайта
    (недостающие — по умолчанию, лишние/нечисловые/вне диапазона — ошибка), целые там, где параметр целый. Вес: 0..1."""
    st = STRATEGIES.get(leg.strategy)
    if st is None:
        close = difflib.get_close_matches(leg.strategy, list(STRATEGIES), n=1, cutoff=0.6)
        raise ValueError(f"неизвестная стратегия «{leg.strategy}»" + (f" (возможно, «{close[0]}»)" if close else ""))
    if leg.tf not in LIVE_TFS:
        raise ValueError(f"таймфрейм {leg.tf} не поддерживается живым ботом (только {', '.join(LIVE_TFS)})")
    specs = param_specs(st)
    extra = sorted(set(leg.params) - set(specs))
    if extra:
        raise ValueError(f"у «{leg.strategy}» нет параметров: {', '.join(extra)} (есть: {', '.join(specs)})")
    out: dict = {}
    for name, sp in specs.items():
        v = leg.params.get(name, sp["default"])
        if not _is_num(v) or not math.isfinite(v):
            raise ValueError(f"параметр {name} должен быть конечным числом")
        if sp["kind"] == "int":
            if v != int(v):
                raise ValueError(f"параметр {name} должен быть целым (получено {v:g})")
            v = int(v)
        else:
            v = float(v)
        if not (sp["min"] - 1e-9 <= v <= sp["max"] + 1e-9):
            raise ValueError(f"{name}={v:g} вне допустимого диапазона [{sp['min']:g}, {sp['max']:g}]")
        out[name] = v
    if not _is_num(leg.weight) or not math.isfinite(leg.weight):
        raise ValueError("вес должен быть числом")
    return replace(leg, params=out, weight=min(1.0, max(0.0, float(leg.weight))), long_only=bool(leg.long_only))


def params_text(params: dict) -> str:
    return ", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in params.items())
