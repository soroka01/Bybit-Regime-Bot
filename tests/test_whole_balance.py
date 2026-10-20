"""Торговля на ВЕСЬ баланс счёта (без «выделенного капитала»): размер от sizing_balance, удержание acted_qty, переворот,
миграция, сбой чтения баланса, маржинальная модель DryRunExchange и разбор wallet-balance у BybitClient."""
import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import httpx
import pytest

from tradebot.trading.exchange import BybitClient, ExchangeError
from fakes.exchange import DryRunExchange
from tradebot.trading.executor import SIZING_EQUITY_FRACTION, Executor
from tradebot.trading.state import Store, read_snapshot

BAR = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


class Sink:
    def __init__(self):
        self.msgs = []

    def __call__(self, level, text):
        self.msgs.append((level, text))

    def has(self, s, level=None):
        return any(s in t and (level is None or lv == level) for lv, t in self.msgs)

    def count(self, level):
        return sum(1 for lv, _ in self.msgs if lv == level)


def mk(tmp_path, equity=1100.0, lev=2, mark=100_000.0, **kw):
    st = Store(tmp_path / "w.db")
    st.set_setting("enabled", True)
    st.set_setting("leverage", lev)
    ex = DryRunExchange(equity=equity, mark=mark, **kw)
    sink = Sink()
    exe = Executor(ex, st, sink)
    exe.poll_delay = 0
    exe.retry_wait = 0
    run(exe.setup())
    return st, ex, exe, sink


# ---------- размер от баланса ----------
def test_entry_sized_from_fresh_balance_without_cached_equity(tmp_path):
    """Первый цикл идёт ДО monitor(): kv 'equity' пуст — баланс берётся прямо с биржи, а не из кэша."""
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    assert st.get("equity") is None and SIZING_EQUITY_FRACTION == 0.97
    assert run(exe.reconcile(0.5, BAR)) == "выполнено"
    assert ex.qty == pytest.approx(0.010)                      # 0.5 * 1100 * 0.97 * 2 / 1e5 = 0.01067 -> вниз
    assert st.get("acted_exposure") == 0.5 and st.get("acted_qty") == pytest.approx(0.010)
    assert ex.sizing_calls == 1 and not ex.rejected


def test_exposure_change_resizes_from_balance_after_fees(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    run(exe.reconcile(1.0, BAR))                               # цель сменилась -> пересчёт: (1100-1) * 0.97 * 2 / 1e5 = 0.0213
    assert ex.qty == pytest.approx(0.021) and st.get("acted_qty") == pytest.approx(0.021) and not ex.rejected


def test_below_min_qty_stays_flat_and_records_zero(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=50)                   # 0.5 * 50 * 0.97 * 2 / 1e5 = 0.0005 < minOrderQty
    assert run(exe.reconcile(0.5, BAR)) == "выполнено"
    assert ex.qty == 0 and not ex.orders
    assert st.get("acted_exposure") == 0.5 and st.get("acted_qty") == 0.0


def test_balance_growth_and_shrink_change_new_entries(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    assert ex.qty == pytest.approx(0.010)
    run(exe.reconcile(0.0, BAR))
    assert ex.qty == 0
    ex.cash = 2200.0                                           # счёт вырос: новый вход крупнее
    run(exe.reconcile(0.5, BAR))
    assert ex.qty == pytest.approx(0.021)                      # 0.5 * 2200 * 0.97 * 2 / 1e5 = 0.02134
    run(exe.reconcile(0.0, BAR))
    ex.cash = 550.0                                            # счёт похудел: меньше
    run(exe.reconcile(0.5, BAR))
    assert ex.qty == pytest.approx(0.005)                      # 0.5 * 550 * 0.97 * 2 / 1e5 = 0.005335
    assert not ex.rejected


def test_losing_flip_opens_leg_from_balance_fetched_after_close(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(1.0, BAR))
    assert ex.qty == pytest.approx(0.021)
    ex.mark = 95_000.0                                         # лонг в убытке -105
    real, calls = ex.sizing_balance, []

    async def seq():                                           # реальный баланс записываем, а ногу считаем от «подменённого»
        calls.append(await real())
        return 400.0
    ex.sizing_balance = seq
    assert run(exe.reconcile(-1.0, BAR)) == "выполнено"
    assert len(calls) == 1                                     # баланс читается один раз — ПОСЛЕ reduce-only закрытия
    assert calls[0] == pytest.approx(990.905, rel=1e-4)        # кошелёк после реализованного убытка и комиссии
    assert ex.qty == pytest.approx(-0.008)                     # от 400: 400 * 0.97 * 2 / 95000 = 0.00817
    assert [o["reduce_only"] for o in ex.orders[-2:]] == [True, False] and not ex.rejected
    assert st.get("acted_qty") == pytest.approx(-0.008)


def test_losing_flip_real_numbers_accepted_by_margin_check(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(1.0, BAR))
    ex.mark = 95_000.0
    assert run(exe.reconcile(-1.0, BAR)) == "выполнено"
    assert ex.qty < 0 and not ex.rejected and ex.sizing_calls == 2          # открытие и одно чтение между ногами переворота
    assert ex.free_balance() >= 0 and ex.stop is not None and ex.stop > 95_000       # шорт под стопом


def test_profitable_addon_is_sized_from_wallet_not_equity(tmp_path):
    """Цель 0.5 -> 1.0 при большой нереализованной прибыли: от equity (1599) добор не прошёл бы по марже, от
    min(walletBalance, equity) = 1099 — проходит."""
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    ex.mark = 150_000.0                                        # uPnL +500, equity 1599, wallet 1099
    assert run(ex.sizing_balance()) == pytest.approx(1099.0) and run(ex.equity()) == pytest.approx(1599.0)
    with pytest.raises(ExchangeError) as e:                    # контроль: размер от equity (0.020) биржа бы отклонила
        run(ex.market("buy", 0.010, False, "ctl"))
    assert e.value.code == 110007
    assert run(exe.reconcile(1.0, BAR)) == "выполнено"
    assert ex.qty == pytest.approx(0.014) and len(ex.rejected) == 1      # 1099 * 0.97 * 2 / 150000 = 0.01421; отказ только контрольный


def test_losing_position_sizes_from_equity(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    ex.mark = 80_000.0                                         # uPnL -200: equity 899 < wallet 1099
    assert run(ex.sizing_balance()) == pytest.approx(899.0)


# ---------- сбой чтения баланса ----------
@pytest.mark.parametrize("how", ["raise", "zero", "negative", "generic"])
def test_sizing_balance_failure_means_no_orders_and_alert(tmp_path, how):
    st, ex, exe, sink = mk(tmp_path, equity=1100)
    st.set("equity", 5000.0)                                   # устаревшее значение в kv использовать НЕЛЬЗЯ
    if how == "raise":
        ex.sizing_error = ExchangeError("сеть", -1)
    elif how == "generic":
        ex.sizing_error = RuntimeError("boom")
    else:
        async def bad():
            return 0.0 if how == "zero" else -5.0
        ex.sizing_balance = bad
    res = run(exe.reconcile(0.5, BAR))
    assert res.startswith("баланс недоступен") and not ex.orders and ex.qty == 0
    assert st.get("acted_exposure") is None and st.get("acted_qty") is None          # acted_* не тронуты
    assert sink.has("Баланс счёта недоступен", "warn") and sink.count("warn") == 1
    ex.sizing_error = None
    ex.sizing_balance = DryRunExchange.sizing_balance.__get__(ex)
    assert run(exe.reconcile(0.5, BAR)) == "выполнено" and ex.qty == pytest.approx(0.010)       # следующий цикл — повтор


def test_balance_not_needed_while_exposure_unchanged(tmp_path):
    st, ex, exe, sink = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    n = ex.sizing_calls
    ex.sizing_error = ExchangeError("сеть", -1)
    assert run(exe.reconcile(0.5, BAR)) == "без изменений"      # баланс даже не запрашивается
    assert ex.sizing_calls == n and not sink.count("warn")


# ---------- удержание размера ----------
def test_unchanged_exposure_holds_qty_despite_price_and_equity_drift(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    n_orders, n_calls = len(ex.orders), ex.sizing_calls
    for mark, bump in ((130_000.0, 300.0), (60_000.0, -200.0), (100_000.0, 900.0)):
        ex.mark, ex.cash = mark, ex.cash + bump
        assert run(exe.reconcile(0.5, BAR)) == "без изменений"
    assert len(ex.orders) == n_orders and ex.sizing_calls == n_calls
    assert st.get("acted_qty") == pytest.approx(0.010)


def test_leverage_change_resizes_only_after_acted_reset(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    st.set_setting("leverage", 3)
    assert run(exe.setup()) is True                            # плечо применено на бирже
    assert run(exe.reconcile(0.5, BAR)) == "без изменений" and ex.qty == pytest.approx(0.010)     # без сброса — удержание
    st.clear_acted()                                           # так делает обработчик lv
    assert run(exe.reconcile(0.5, BAR)) == "выполнено"
    assert ex.qty == pytest.approx(0.015) and not ex.rejected  # 0.5 * 1099 * 0.97 * 3 / 1e5 = 0.01599


def test_enable_reset_resizes_from_current_balance(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    ex.cash = 2200.0
    assert run(exe.reconcile(0.5, BAR)) == "без изменений"
    st.clear_acted()                                           # tr:on
    run(exe.reconcile(0.5, BAR))
    assert ex.qty == pytest.approx(0.021)


# ---------- миграция ----------
def test_migration_adopts_existing_position_without_orders(tmp_path):
    st, ex, exe, sink = mk(tmp_path, equity=1100)
    ex.external_trade(0.003)                                   # живая позиция старой версии: 0.003 BTC
    st.set("acted_exposure", 0.5)                              # acted_qty ещё нет
    assert st.get("acted_qty") is None
    assert run(exe.reconcile(0.5, BAR)) == "без изменений"
    assert not ex.orders and ex.sizing_calls == 0 and ex.qty == pytest.approx(0.003)
    assert st.get("acted_qty") == pytest.approx(0.003) and not sink.has("принят")                  # служебная строка: в Telegram не шлём
    assert ex.stop is not None                                 # стоп проверен/поставлен
    assert run(exe.reconcile(0.5, BAR)) == "без изменений" and not ex.orders      # дальше — обычное удержание


def test_migration_does_not_adopt_opposite_or_flat_position(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    ex.external_trade(-0.003)                                  # шорт при цели лонг: принимать нельзя, пересчёт и переворот
    st.set("acted_exposure", 0.5)
    run(exe.reconcile(0.5, BAR))
    assert ex.qty == pytest.approx(0.010) and st.get("acted_qty") == pytest.approx(0.010)


def test_migration_target_zero_closes_leftover(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    ex.external_trade(0.003)
    st.set("acted_exposure", 0.0)
    run(exe.reconcile(0.0, BAR))
    assert ex.qty == 0


# ---------- внешние изменения ----------
def test_manual_partial_close_detected_by_band_against_acted_qty(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=5000)
    run(exe.reconcile(0.5, BAR))
    assert ex.qty == pytest.approx(0.048)
    n = len(ex.orders)
    ex.external_trade(-0.001)                                  # -2%: внутри мёртвой зоны 10% относительно acted_qty
    assert run(exe.reconcile(0.5, BAR)) == "без изменений" and len(ex.orders) == n
    ex.external_trade(-0.030)                                  # владелец сократил позицию вручную до 0.017
    assert ex.qty == pytest.approx(0.017)
    assert run(exe.reconcile(0.5, BAR)) == "выполнено"
    assert ex.qty == pytest.approx(0.048) and len(ex.orders) == n + 1              # восстановлено от свежего баланса
    assert st.get("acted_qty") == pytest.approx(0.048)


def test_manual_full_close_latches_like_before(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    ex.external_trade(-0.010)
    assert run(exe.reconcile(0.5, BAR)).startswith("защёлка") and ex.qty == 0


# ---------- переворот: сбой на открывающей ноге ----------
@pytest.mark.parametrize("fail", ["balance", "margin"])
def test_flip_open_leg_failure_leaves_flat_alerts_once_keeps_acted(tmp_path, fail):
    st, ex, exe, sink = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    assert st.get("acted_exposure") == 0.5 and st.get("acted_qty") == pytest.approx(0.010)
    if fail == "balance":
        real, n = ex.sizing_balance, {"c": 0}

        async def flaky():
            n["c"] += 1
            if n["c"] >= 1:                                    # первый вызов после входа — между ногами переворота
                raise ExchangeError("сеть", -1)
            return await real()
        ex.sizing_balance = flaky
    else:
        real_market = ex.market

        async def reject_open(side, qty, ro, link):
            if not ro:
                raise ExchangeError("ab not enough for new order", 110007)
            return await real_market(side, qty, ro, link)
        ex.market = reject_open
    sink.msgs.clear()
    res = run(exe.reconcile(-0.5, BAR))
    assert res.startswith("ошибка")
    assert ex.qty == 0 and [o["reduce_only"] for o in ex.orders[-1:]] == [True]      # закрыли, открыть не смогли
    assert st.get("acted_exposure") == 0.5 and st.get("acted_qty") == pytest.approx(0.010)    # acted_* не обновлены
    assert sink.count("error") == 1 and sink.has("Ошибка биржи при сверке", "error")  # тревога ровно одна за бар
    assert not st.get("latch") and not st.get("stop_armed")
    if fail == "balance":
        ex.sizing_balance = real
    else:
        ex.market = real_market
    assert run(exe.reconcile(-0.5, BAR)) == "выполнено" and ex.qty < 0              # следующая сверка довершает вход


# ---------- старая БД с s_allocated_capital ----------
def test_old_db_allocated_capital_is_ignored_and_untouched(tmp_path):
    p = tmp_path / "old.db"
    st = Store(p)
    st.set("s_allocated_capital", 160.0)                       # осталась от прежней версии
    assert "allocated_capital" not in st.settings()
    assert "allocated_capital" not in read_snapshot(p)["settings"]
    with pytest.raises(ValueError):
        st.set_setting("allocated_capital", 200)
    assert st.get("s_allocated_capital") == 160.0              # ключ в БД никто не трогает и не мигрирует
    db = sqlite3.connect(str(p))
    assert json.loads(db.execute("SELECT v FROM kv WHERE k='s_allocated_capital'").fetchone()[0]) == 160.0
    db.close()
    st2 = Store(p)                                             # повторное открытие/старт: по-прежнему игнорируется
    assert "allocated_capital" not in st2.settings() and st2.get("s_allocated_capital") == 160.0


# ---------- DryRunExchange: маржа, комиссии, wallet vs equity ----------
def test_dryrun_rejects_order_exceeding_free_margin_with_110007():
    ex = DryRunExchange(equity=100, mark=100_000)               # плечо 1: 0.001 BTC = 100 USDT + комиссии 0.2
    with pytest.raises(ExchangeError) as e:
        run(ex.market("buy", 0.001, False, "a"))
    assert e.value.code == 110007 and ex.qty == 0 and ex.rejected
    ex2 = DryRunExchange(equity=101, mark=100_000)
    run(ex2.market("buy", 0.001, False, "a"))
    assert ex2.qty == pytest.approx(0.001)
    ex3 = DryRunExchange(equity=100, mark=100_000)
    run(ex3.setup_account(2))
    run(ex3.market("buy", 0.001, False, "a"))                   # плечо 2: нужно 50 + 0.2
    assert ex3.margin_used() == pytest.approx(50.0)


def test_dryrun_reduce_only_skips_margin_and_fee_is_configurable():
    ex = DryRunExchange(equity=1000, mark=100_000, fee_rate=0.001)
    run(ex.setup_account(2))
    run(ex.market("buy", 0.010, False, "a"))
    assert ex.cash == pytest.approx(999.0) and ex.fees_paid == pytest.approx(1.0)       # 1000 * 0.1%
    ex.cash = 1.0                                              # маржи почти нет, но закрыть можно
    run(ex.market("sell", 0.010, True, "b"))
    assert ex.qty == 0
    ex0 = DryRunExchange(equity=1000, mark=100_000, fee_rate=0.0)
    run(ex0.setup_account(2))
    run(ex0.market("buy", 0.010, False, "a"))
    assert ex0.cash == 1000.0 and ex0.fees_paid == 0.0


def test_dryrun_wallet_vs_equity_and_unrealised_pnl_is_not_free_margin():
    ex = DryRunExchange(equity=1000, mark=100_000, fee_rate=0.0)
    run(ex.setup_account(2))
    run(ex.market("buy", 0.010, False, "a"))                    # маржа 500
    ex.mark = 150_000.0                                        # uPnL +500
    assert run(ex.equity()) == pytest.approx(1500.0) and ex.cash == pytest.approx(1000.0)
    assert ex.free_balance() == pytest.approx(500.0)           # прибыль тратить нельзя
    assert run(ex.sizing_balance()) == pytest.approx(1000.0)
    ex.mark = 80_000.0                                         # uPnL -200
    assert run(ex.sizing_balance()) == pytest.approx(800.0)


# ---------- BybitClient.sizing_balance ----------
def _client(handler):
    c = BybitClient("https://x", "K", "S", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    c._synced = 1e18
    return c


def _wb(coins, req_log=None):
    def handler(req: httpx.Request):
        if req_log is not None:
            req_log.append((req.method, req.url.path, dict(req.url.params)))
        return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"totalEquity": "999", "coin": coins}]}})
    return handler


def test_bybit_sizing_balance_is_min_of_wallet_and_equity_of_usdt():
    log = []
    coins = [{"coin": "BTC", "walletBalance": "5", "equity": "5"}, {"coin": "USDT", "walletBalance": "160.5", "equity": "158.2"}]
    assert run(_client(_wb(coins, log)).sizing_balance()) == pytest.approx(158.2)       # убыток: equity < wallet
    assert log == [("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})]   # один запрос, без CONTRACT-запасного
    coins[1].update(walletBalance="160.5", equity="190.0")                              # прибыль: wallet < equity
    assert run(_client(_wb(coins)).sizing_balance()) == pytest.approx(160.5)


@pytest.mark.parametrize("coins", [[], [{"coin": "BTC", "walletBalance": "1", "equity": "1"}],
                                    [{"coin": "USDT", "walletBalance": "", "equity": "10"}],
                                    [{"coin": "USDT", "walletBalance": "10"}], [{"coin": "USDT", "walletBalance": "x", "equity": "1"}]])
def test_bybit_sizing_balance_requires_usdt_numbers(coins):
    with pytest.raises(ExchangeError):
        run(_client(_wb(coins)).sizing_balance())


def test_bybit_sizing_balance_error_does_not_fall_back_to_contract_account():
    log = []

    def handler(req: httpx.Request):
        log.append(dict(req.url.params).get("accountType"))
        return httpx.Response(200, json={"retCode": 10001, "retMsg": "accountType error", "result": {}})
    with pytest.raises(ExchangeError):
        run(_client(handler).sizing_balance())
    assert log == ["UNIFIED"]


# ---------- итоговое ревью: закрытия не зависят от баланса, рестарт, kill, повторные отказы ----------
def _fresh_executor(ex, st):
    """Новый Executor на тех же бирже и БД: рестарт процесса бота."""
    sink = Sink()
    exe = Executor(ex, st, sink)
    exe.poll_delay = 0
    exe.retry_wait = 0
    run(exe.setup())
    return exe, sink


def test_full_close_does_not_need_balance(tmp_path):
    st, ex, exe, sink = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    n = ex.sizing_calls
    ex.sizing_error = ExchangeError("сеть", -1)                 # чтение баланса сломано
    assert run(exe.reconcile(0.0, BAR)) == "выполнено"          # позицию против нулевой цели не держим
    assert ex.qty == 0 and ex.sizing_calls == n
    assert st.get("acted_exposure") == 0.0 and st.get("acted_qty") == 0.0 and not sink.count("warn")


def test_flip_close_goes_out_when_balance_is_broken_open_leg_fails_flat(tmp_path):
    st, ex, exe, sink = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    ex.sizing_error = ExchangeError("сеть", -1)
    sink.msgs.clear()
    res = run(exe.reconcile(-0.5, BAR))
    assert res.startswith("ошибка") and ex.qty == 0 and ex.orders[-1]["reduce_only"] is True   # reduce-only ушёл
    assert sink.count("error") == 1                             # одна тревога
    assert st.get("acted_exposure") == 0.5 and st.get("acted_qty") == pytest.approx(0.010)
    ex.sizing_error = None
    assert run(exe.reconcile(-0.5, BAR)) == "выполнено" and ex.qty == pytest.approx(-0.010)     # следующий цикл открывает


def test_partial_reduction_with_nonzero_target_still_needs_balance_and_holds(tmp_path):
    st, ex, exe, sink = mk(tmp_path, equity=1100)
    run(exe.reconcile(1.0, BAR))
    ex.sizing_error = ExchangeError("сеть", -1)
    sink.msgs.clear()
    assert run(exe.reconcile(0.5, BAR)).startswith("баланс недоступен")
    assert ex.qty == pytest.approx(0.021) and sink.count("warn") == 1
    assert st.get("acted_exposure") == 1.0 and st.get("acted_qty") == pytest.approx(0.021)


def test_restart_with_changed_target_resizes_from_wallet_size(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    assert st.get("acted_qty") == pytest.approx(0.010)
    exe2, sink2 = _fresh_executor(ex, st)                       # рестарт: цель за время простоя сменилась
    assert run(exe2.reconcile(1.0, BAR)) == "выполнено"
    assert ex.qty == pytest.approx(0.021) and st.get("acted_qty") == pytest.approx(0.021) and not sink2.has("принят")


def test_stopout_while_bot_is_down_latches_after_restart(tmp_path):
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    assert st.get("stop_armed") and st.get("acted_qty") == pytest.approx(0.010)
    ex.stop_out()                                               # биржа закрыла по стопу, пока бот не работал
    n = len(ex.orders)
    exe2, sink2 = _fresh_executor(ex, st)
    assert run(exe2.reconcile(0.5, BAR)).startswith("защёлка")
    assert ex.qty == 0 and len(ex.orders) == n and st.get("latch")
    assert st.get("acted_qty") == pytest.approx(0.010)          # acted_qty не трогали, повторного входа нет
    assert run(exe2.reconcile(0.5, BAR)).startswith("защёлка") and len(ex.orders) == n


def test_flip_second_leg_ambiguous_timeout_leaves_flat_one_error_then_opens(tmp_path):
    st, ex, exe, sink = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    real_market = ex.market

    async def timeout_on_open(side, qty, ro, link):            # открывающая нога не доходит ни с первой попытки, ни с повтора
        if not ro:
            from tradebot.trading.exchange import AmbiguousOrder
            raise AmbiguousOrder("timeout")
        return await real_market(side, qty, ro, link)
    ex.market = timeout_on_open
    sink.msgs.clear()
    res = run(exe.reconcile(-0.5, BAR))
    assert res.startswith("ошибка") and ex.qty == 0 and ex.orders[-1]["reduce_only"] is True
    assert sink.count("error") == 1
    assert st.get("acted_exposure") == 0.5 and st.get("acted_qty") == pytest.approx(0.010)
    ex.market = real_market
    assert run(exe.reconcile(-0.5, BAR)) == "выполнено" and ex.qty == pytest.approx(-0.010)


def test_killed_with_live_position_sends_nothing_and_adopts_nothing(tmp_path):
    st, ex, exe, sink = mk(tmp_path, equity=1100)
    ex.external_trade(0.003)
    st.set("acted_exposure", 0.5)
    st.set("killed", True)
    assert run(exe.reconcile(0.5, BAR)) == "kill-switch"
    assert run(exe.reconcile(0.0, BAR)) == "kill-switch"
    assert not ex.orders and ex.sizing_calls == 0 and st.get("acted_qty") is None and not sink.has("принят")


def test_addon_rejected_110007_retries_every_bar_without_writing_acted(tmp_path):
    """Документирует поведение: отказ добора не пишет acted_*, каждый следующий бар пробует снова и тревожит один раз."""
    st, ex, exe, sink = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    real_market = ex.market

    async def no_margin(side, qty, ro, link):
        if not ro:
            raise ExchangeError("ab not enough for new order", 110007)
        return await real_market(side, qty, ro, link)
    ex.market = no_margin
    for _ in range(2):
        sink.msgs.clear()
        n = len(ex.orders)
        res = run(exe.reconcile(1.0, BAR))
        assert res.startswith("ошибка") and "ab not enough" in res
        assert sink.count("error") == 1 and sink.has("Ошибка биржи при сверке", "error")
        assert len(ex.orders) == n and ex.qty == pytest.approx(0.010)
        assert st.get("acted_exposure") == 0.5 and st.get("acted_qty") == pytest.approx(0.010)
    ex.market = real_market
    assert run(exe.reconcile(1.0, BAR)) == "выполнено" and ex.qty == pytest.approx(0.021)


def test_addon_while_underwater_is_sized_from_equity_and_accepted(tmp_path):
    """Цель 0.5 -> 1.0 при нереализованном убытке: от кошелька (1099) добор не прошёл бы по марже, от equity (999) проходит.
    Свободный баланс DryRun консервативно вычитает нереализованный убыток; как реальная UTA считает его — не проверено."""
    st, ex, exe, _ = mk(tmp_path, equity=1100)
    run(exe.reconcile(0.5, BAR))
    ex.mark = 90_000.0                                         # uPnL -100: wallet 1099, equity 999
    assert run(ex.sizing_balance()) == pytest.approx(999.0) and ex.free_balance() == pytest.approx(499.0)
    with pytest.raises(ExchangeError) as e:                    # контроль: размер от кошелька (0.023) -> добор 0.013 отклонён
        run(ex.market("buy", 0.013, False, "ctl"))
    assert e.value.code == 110007
    assert run(exe.reconcile(1.0, BAR)) == "выполнено"
    assert ex.qty == pytest.approx(0.021) and len(ex.rejected) == 1                  # отказ только контрольный
