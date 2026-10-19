"""Текст «Копировать для бота» -> ноги: литеральный разбор (без eval), предупреждения из первой строки, строгая проверка параметров."""
import pytest

from tradebot.core.legspec import (LIVE_TFS, MAX_DRAFTS, MAX_TEXT, draft_payload, leg_from_payload, params_text, parse_leg_text,
                                   validate_leg)
from tradebot.core.portfolio import Leg
from tradebot.core.strategies import STRATEGIES

LAB = ('Стратегия: SMA cross | Таймфрейм: 4h | Параметры: fast=10, slow=100 | Символ: BTCUSDT | Рынок: Перп | Только лонг: нет | '
       'Плечо: 5x | Комиссия: 0.055% | Проскальзывание: 0.02% | Период: 365 дн. | Результат: доход +38%, макс. просадка 12%, сделок 52 | '
       'Оверлеи: нет\n'
       'Leg("SMA cross", "4h", {"fast": 10, "slow": 100}, weight=0.5, long_only=False)\n'
       '# бот ограничен 3× (плечо 5x в бэктесте недостижимо в боте)')


def test_parse_lab_text():
    (d,) = parse_leg_text(LAB)
    assert d.leg == Leg("SMA cross", "4h", {"fast": 10, "slow": 100}, 0.5, False)
    assert d.result == "доход +38%, макс. просадка 12%, сделок 52" and d.bt_leverage == 5.0
    assert d.notes == ["бот ограничен 3× (плечо 5x в бэктесте недостижимо в боте)"]


def test_positional_and_keyword_forms_and_crlf():
    a = parse_leg_text('Leg("EMA cross", "1h", {"fast": 12, "slow": 26}, 0.3, True)\r\n')[0].leg
    b = parse_leg_text('Leg(strategy="EMA cross", tf="1h", params={"fast": 12, "slow": 26}, weight=0.3, long_only=True)')[0].leg
    assert a == b == Leg("EMA cross", "1h", {"fast": 12, "slow": 26}, 0.3, True)
    assert parse_leg_text('Leg("EMA cross", "1h")')[0].leg.params == {}            # параметры по умолчанию подставит validate_leg


def test_portfolio_payload_has_many_legs_and_result_is_portfolio_wide():
    t = ('Портфель: BTCUSDT | Рынок: Перп | Плечо: 2x | Комиссия: 0.055% | Ноги: A@1h (x; вес 0.5) | Результат: доход +10%\n'
         'Leg("EMA cross", "1h", {"fast": 12, "slow": 26}, weight=0.5, long_only=False)\n'
         'Leg("SMA cross", "4h", {"fast": 10, "slow": 100}, weight=0.5, long_only=False)\n# плечо: 2x')
    ds = parse_leg_text(t)
    assert [d.leg.key for d in ds] == ["EMA cross@1h", "SMA cross@4h"]
    assert all(d.result == "" and any("по всему портфелю" in n for n in d.notes) for d in ds)


def test_overlays_hide_result_and_foreign_symbol_is_rejected():
    (d,) = parse_leg_text(LAB.replace("Оверлеи: нет", "Оверлеи: HTF EMA(50)"))
    assert d.result == "" and any("оверлеями" in n for n in d.notes)
    with pytest.raises(ValueError, match="только BTCUSDT"):
        parse_leg_text(LAB.replace("Символ: BTCUSDT", "Символ: ETHUSDT"))
    (s,) = parse_leg_text(LAB.replace("Рынок: Перп", "Рынок: Спот"))
    assert any("споте" in n for n in s.notes)


@pytest.mark.parametrize("bad", [
    'Leg(__import__("os").system("x"), "4h", {})',                       # вызов в аргументе
    'Leg("SMA cross", "4h", {"fast": nan})',                             # имя вместо литерала
    'Leg("SMA cross", "4h", {"fast": float("inf")})',
    'Leg("a", "4h", **{})',
    'Leg("a", "4h", {}, 1, True, 6)',                                    # лишний аргумент
    'Leg("a", "4h", {}, weight=1, weight=2)',
    'Leg("a")',
    'Leg(1, "4h", {})',
    'Leg("a", "4h", {"p": "x"})',                                        # не число
    'Leg("a", "4h", {"p": True})',                                       # bool — не число
    'Leg("a", "4h", [1])',
    'Leg("a", "4h", {}, weight="x")',
    'Leg("a", "4h", {}, long_only=1)',                                   # long_only — только True/False
    'Leg(' + '(' * 400 + ')',
    "ничего похожего",
    "",
])
def test_garbage_is_rejected_with_clean_message(bad):
    with pytest.raises(ValueError) as e:
        parse_leg_text(bad)
    assert "malformed node" not in str(e.value) and "Call(" not in str(e.value)       # репр AST пользователю не показываем


def test_size_limits():
    with pytest.raises(ValueError, match="слишком длинный"):
        parse_leg_text("x" * (MAX_TEXT + 1))
    many = "\n".join(['Leg("EMA cross", "1h", {})'] * (MAX_DRAFTS + 1))
    with pytest.raises(ValueError, match="не больше"):
        parse_leg_text(many)
    with pytest.raises(ValueError):
        parse_leg_text(None)  # type: ignore[arg-type]


# ---------- validate_leg ----------
def test_validate_normalizes_types_and_fills_defaults():
    v = validate_leg(Leg("SMA cross", "4h", {"fast": 10.0, "slow": 100}, 7.0))
    assert v.params == {"fast": 10, "slow": 100} and all(isinstance(x, int) for x in v.params.values())
    assert v.weight == 1.0                                                 # вес — лишь подсказка: зажимается, а не отклоняется
    d = validate_leg(Leg("SMA cross", "4h", {}, -1))                       # недостающие параметры — по умолчанию стратегии
    assert d.params == STRATEGIES["SMA cross"].default and d.weight == 0.0
    r = validate_leg(Leg("Regime switch (CHOP)", "6h", {"n": 28.0, "chop_lo": 45, "chop_hi": 55, "fast": 12, "slow": 100}, 0.5))
    assert r.params["n"] == 28 and isinstance(r.params["n"], int) and isinstance(r.params["chop_lo"], float)


@pytest.mark.parametrize("leg, msg", [
    (Leg("Nope", "4h", {}, 0.5), "неизвестная стратегия"),
    (Leg("SMA cros", "4h", {}, 0.5), "возможно, «SMA cross»"),
    (Leg("SMA cross", "15m", {}, 0.5), "не поддерживается живым ботом"),
    (Leg("SMA cross", "1d", {}, 0.5), "не поддерживается живым ботом"),
    (Leg("SMA cross", "4h", {"zzz": 1}, 0.5), "нет параметров: zzz"),
    (Leg("SMA cross", "4h", {"fast": 10.5, "slow": 100}, 0.5), "целым"),
    (Leg("SMA cross", "4h", {"fast": 500, "slow": 100}, 0.5), "вне допустимого диапазона"),
    (Leg("SMA cross", "4h", {"fast": float("nan"), "slow": 100}, 0.5), "конечным числом"),
    (Leg("SMA cross", "4h", {"fast": True, "slow": 100}, 0.5), "конечным числом"),
    (Leg("SMA cross", "4h", {"fast": 10, "slow": 100}, float("nan")), "вес"),
])
def test_validate_rejects(leg, msg):
    with pytest.raises(ValueError, match=msg):
        validate_leg(leg)


def test_live_tfs_match_the_feed():
    from tradebot.trading.market_feed import FETCH_DAYS, TF_DELTA
    assert set(LIVE_TFS) == set(TF_DELTA) == set(FETCH_DAYS)


def test_payload_roundtrip_takes_warmup_only_from_a_passed_check():
    (d,) = parse_leg_text(LAB)
    p = draft_payload(d)
    assert p["check"] is None and leg_from_payload(p).warmup == 0
    p["check"] = {"ok": True, "warmup": 480}
    assert leg_from_payload(p).warmup == 480
    p["check"] = {"ok": False, "warmup": 480}
    assert leg_from_payload(p).warmup == 0
    assert params_text({"fast": 10, "x": 0.5}) == "fast=10, x=0.5"
