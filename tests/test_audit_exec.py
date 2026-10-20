"""Аудит торгового ядра: исполнитель и биржа (T1 reduce-only без setup, set_leverage с откатом, T2 equity только UTA,
T3a stop_armed при закрытии ботом, T4 неподтверждённый ордер, T7 повтор закрытия при kill)."""
import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from tradebot.trading.exchange import BybitClient, ExchangeError
from fakes.exchange import DryRunExchange
from tradebot.trading.executor import Executor
from tradebot.trading.state import Store

BAR = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


class Sink:
    def __init__(self):
        self.msgs = []

    def __call__(self, level, text, *data):
        self.msgs.append((level, text))

    def has(self, s, level=None):
        return any(s in t and (level is None or lv == level) for lv, t in self.msgs)

    def count(self, level):
        return sum(1 for lv, _ in self.msgs if lv == level)


def mk(tmp_path, equity=1100.0, lev=2, mark=100_000.0):
    st = Store(tmp_path / "a.db")
    st.set_setting("enabled", True)
    st.set_setting("leverage", lev)
    ex = DryRunExchange(equity=equity, mark=mark)
    sink = Sink()
    exe = Executor(ex, st, sink)
    exe.poll_delay = 0
    exe.retry_wait = 0
    run(exe.setup())
    return st, ex, exe, sink


def break_setup(ex, exe):
    """Биржа не подтверждает настройки (плечо стоит чужое) -> setup() вернёт False."""
    ex.leverage_override = 7.0
    exe.trade_ok = False


# ---------- T2: equity только UTA ----------
def _client(handler):
    return BybitClient("https://x", "K", "S", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_equity_uta_only_no_contract_fallback():
    paths = []

    def handler(req: httpx.Request):
        paths.append(dict(req.url.params))
        if req.url.params.get("accountType") == "UNIFIED":
            return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"coin": []}]}})      # нет totalEquity
        return httpx.Response(200, json={"retCode": 0, "result": {"list": [{"coin": [{"equity": "5.0"}]}]}})
    c = _client(handler)
    c._synced = 1e18
    with pytest.raises(ExchangeError, match="totalEquity"):
        run(c.equity())
    assert [p["accountType"] for p in paths] == ["UNIFIED"]          # CONTRACT-аккаунт не запрашивался


@pytest.mark.parametrize("body", [{"list": []}, {}, {"list": [{"totalEquity": ""}]}, {"list": [{"totalEquity": "nan"}]},
                                  {"list": [{"totalEquity": None}]}])
def test_equity_raises_on_missing_or_garbage(body):
    c = _client(lambda req: httpx.Response(200, json={"retCode": 0, "result": body}))
    c._synced = 1e18
    with pytest.raises(ExchangeError):
        run(c.equity())


def test_equity_error_does_not_fall_back():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.params.get("accountType"))
        return httpx.Response(200, json={"retCode": 10001, "retMsg": "boom"})
    c = _client(handler)
    c._synced = 1e18
    with pytest.raises(ExchangeError):
        run(c.equity())
    assert calls == ["UNIFIED"]


def test_equity_ok():
    c = _client(lambda req: httpx.Response(200, json={"retCode": 0, "result": {"list": [{"totalEquity": "155.5"}]}}))
    c._synced = 1e18
    assert run(c.equity()) == 155.5


# ---------- T1: reduce-only ноги не зависят от setup ----------
def test_gated_close_still_goes_out(tmp_path):
    st, ex, exe, sink = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    assert ex.qty == pytest.approx(0.010)
    break_setup(ex, exe)
    assert run(exe.reconcile(0.0, BAR)) == "выполнено"
    assert ex.qty == 0 and exe.trade_ok is False                        # позиция закрыта, хотя setup по-прежнему не проходит
    assert st.get("acted_exposure") == 0.0 and sink.has("не совпали", "error")


def test_gated_reduction_goes_out_but_increase_does_not(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    break_setup(ex, exe)
    n = len(ex.orders)
    assert run(exe.reconcile(1.0, BAR)) == "аккаунт не настроен"       # увеличение — это открывающая нога: заблокирована
    assert len(ex.orders) == n and ex.qty == pytest.approx(0.010) and st.get("acted_exposure") == 0.5
    assert run(exe.reconcile(0.25, BAR)) == "выполнено"                # сокращение — reduce-only: уходит
    assert ex.qty == pytest.approx(0.005) and st.get("acted_exposure") == 0.25
    assert ex.orders[-1]["reduce_only"] is True


def test_gated_flip_closes_but_never_opens(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    break_setup(ex, exe)
    assert run(exe.reconcile(-0.5, BAR)) == "аккаунт не настроен"
    assert ex.qty == 0                                                   # закрывающая нога ушла
    assert len(ex.orders) == 2 and ex.orders[-1]["reduce_only"] is True  # открывающей (не reduce-only) не было
    assert st.get("acted_exposure") == 0.5 and st.get("acted_qty") == pytest.approx(0.010)    # acted не обновлён: цель не достигнута
    # настройки починились -> следующая сверка открывает шорт штатно
    ex.leverage_override = None
    assert run(exe.reconcile(-0.5, BAR)) == "выполнено" and ex.qty < 0


def test_gated_without_position_does_nothing(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    break_setup(ex, exe)
    assert run(exe.reconcile(0.5, BAR)) == "аккаунт не настроен" and not ex.orders
    assert st.get("acted_exposure") is None


def test_gated_unchanged_target_is_untouched(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    break_setup(ex, exe)
    ex.stop = None                                                       # стоп не трогаем: прежнее поведение — «ничего не делаем»
    n = len(ex.orders)
    assert run(exe.reconcile(0.5, BAR)) == "аккаунт не настроен" and len(ex.orders) == n and ex.stop is None


# ---------- set_leverage ----------
def test_set_leverage_success_clears_acted_and_applies(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    assert run(exe.set_leverage(3)) is True
    assert st.settings()["leverage"] == 3.0 and ex.account["leverage"] == 3.0
    assert st.get("acted_exposure") is None and st.get("acted_qty") is None
    assert run(exe.reconcile(0.5, BAR)) == "выполнено" and ex.qty == pytest.approx(0.015)      # пересчёт от нового плеча


def test_set_leverage_decrease_allowed(tmp_path):
    st, ex, exe, _ = mk(tmp_path, lev=3)
    assert run(exe.set_leverage(1)) is True and st.settings()["leverage"] == 1.0 and ex.account["leverage"] == 1.0


def test_set_leverage_failure_rolls_back_and_notifies(tmp_path):
    st, ex, exe, sink = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    ex.leverage_override = 1.0                                           # биржа плечо не принимает
    assert run(exe.set_leverage(3)) is False
    assert st.settings()["leverage"] == 2.0                              # сохранённое плечо откатано
    assert st.get("acted_exposure") == 0.5 and st.get("acted_qty") == pytest.approx(0.010)    # и удерживаемый размер тоже
    assert sink.has("не применилось", "error") and sink.has("оставлено прежнее 2x")


def test_set_leverage_failure_then_old_leverage_restored_on_exchange(tmp_path):
    st, ex, exe, sink = mk(tmp_path)
    orig = ex.setup_account
    calls = {"n": 0}

    async def flaky(lev):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExchangeError("сеть")                                  # первая попытка (новое плечо) падает
        return await orig(lev)
    ex.setup_account = flaky
    assert run(exe.set_leverage(3)) is False
    assert calls["n"] == 2 and ex.account["leverage"] == 2.0 and exe.trade_ok is True      # прежнее плечо вернули на бирже
    assert st.settings()["leverage"] == 2.0 and sink.has("оставлено прежнее 2x.")


def test_set_leverage_out_of_range_changes_nothing(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    with pytest.raises(ValueError):
        run(exe.set_leverage(4))
    assert st.settings()["leverage"] == 2.0 and st.get("acted_exposure") == 0.5


def test_set_leverage_waits_for_inflight_reconcile(tmp_path):
    """Сброс acted_* идёт под lock ПОСЛЕ идущей сверки: её запись acted_* больше не перебивает сброс (гонка T5/G4)."""
    st, ex, exe, _ = mk(tmp_path)
    orig = ex.position

    async def slow():
        await asyncio.sleep(0.03)
        return await orig()
    ex.position = slow

    async def go():
        t1 = asyncio.create_task(exe.reconcile(0.5, BAR))
        await asyncio.sleep(0.005)                                       # сверка уже держит lock
        t2 = asyncio.create_task(exe.set_leverage(3))
        return await asyncio.gather(t1, t2)
    r1, ok = run(go())
    assert r1 == "выполнено" and ok is True
    assert st.get("acted_exposure") is None and st.get("acted_qty") is None and st.settings()["leverage"] == 3.0


# ---------- T3a: закрытие ботом гасит stop_armed ----------
def test_bot_close_clears_stop_armed_and_pos_last(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    assert st.get("stop_armed") is True
    # закрытие напрямую через _send: «падение» до rearm_quietly/_flatten.set(stop_armed=False)
    run(exe._send("sell", 0.010, True, BAR, "сигнал", 0.010))
    assert ex.qty == 0 and st.get("stop_armed") is False and st.get("pos_last") is None
    # рестарт: новый исполнитель на той же БД не объявляет ложный «стоп-аут»
    exe2 = Executor(ex, st, Sink())
    assert exe2.detect_stopout(None, 0.5) is False and exe2._stopouts == []


def test_real_stopout_still_detected_after_fix(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    st.set("target_exposure", 0.5)
    run(exe.reconcile(0.5, BAR))
    ex.stop_out()
    run(exe.check_stopout())
    assert st.get("latch") is True and not st.get("stop_armed")


# ---------- T4: неподтверждённый ордер ----------
def _noop_market(ex):
    sent = []

    async def market(side, qty, ro, link):
        sent.append((side, qty, ro))
        return {"orderId": "ghost", "orderLinkId": link}
    ex.market = market
    return sent


def test_never_filled_aborts_and_keeps_acted(tmp_path):
    st, ex, exe, sink = mk(tmp_path)
    sent = _noop_market(ex)
    n0 = exe.sent
    r = run(exe.reconcile(0.5, BAR))
    assert r.startswith("ошибка") and len(sent) == 1
    assert st.get("acted_exposure") is None and st.get("acted_qty") is None        # acted_* не записаны
    assert exe.sent == n0 and st.count("trades") == 0 and ex.qty == 0
    assert sink.has("не подтвердилось", "warn") and sink.has("Ошибка биржи", "error")


def test_never_filled_does_not_overwrite_previous_acted(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    _noop_market(ex)
    assert run(exe.reconcile(0.0, BAR)).startswith("ошибка")                         # закрытие не подтвердилось
    assert st.get("acted_exposure") == 0.5 and st.get("acted_qty") == pytest.approx(0.010) and ex.qty == pytest.approx(0.010)


def test_flip_with_unconfirmed_reduce_leg_never_opens_new_side(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    sent = _noop_market(ex)
    assert run(exe.reconcile(-0.5, BAR)).startswith("ошибка")
    assert sent == [("sell", 0.010, True)]                                           # открывающая нога не отправлена
    assert ex.qty == pytest.approx(0.010) and st.get("acted_exposure") == 0.5


def test_late_fill_is_booked_by_position_delta(tmp_path):
    st, ex, exe, sink = mk(tmp_path)

    async def hidden(link):
        return None                                                                  # биржа ордер «не показывает»
    ex.order_by_link = hidden
    assert run(exe.reconcile(0.5, BAR)) == "выполнено"                               # ордер реально исполнился
    assert ex.qty == pytest.approx(0.010) and exe.sent == 1
    tr = st.all_trades()
    assert len(tr) == 1 and tr[0]["side"] == "buy" and tr[0]["qty"] == pytest.approx(0.010) and tr[0]["price"] == 100_000.0
    assert st.get("acted_exposure") == 0.5 and st.get("stop_armed") is True
    assert sink.has("не подтвердилось", "warn")


def test_ambiguous_then_changed_books_trade(tmp_path):
    st, ex, exe, sink = mk(tmp_path)

    async def hidden(link):
        return None
    ex.order_by_link = hidden
    ex.fail_next = "after"                                                           # исполнился, ответ потерян
    sent0 = exe.sent
    run(exe._send("buy", 0.003, False, BAR, "бар", 0.0))
    assert ex.qty == pytest.approx(0.003) and exe.sent == sent0 + 1 and len(ex.orders) == 1
    assert st.count("trades") == 1 and sink.has("Ответ на ордер не получен")         # раньше сделка в ленту не попадала
    assert exe._fills and exe._fills[0]["kind"] == "open"


def test_ambiguous_unchanged_position_retries_then_confirms(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    ex.fail_next = "before"                                                          # ордер не дошёл: повтор с тем же link
    run(exe._send("buy", 0.003, False, BAR, "бар", 0.0))
    assert ex.qty == pytest.approx(0.003) and len(ex.orders) == 1 and st.count("trades") == 1


def test_position_moved_other_way_is_not_our_fill(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    n_tr = st.count("trades")

    async def market(side, qty, ro, link):
        ex.external_trade(-0.004)                                                    # за это время владелец сократил позицию вручную
        return {}
    ex.market = market
    with pytest.raises(ExchangeError, match="другую сторону"):
        run(exe._send("buy", 0.003, False, BAR, "бар", 0.010))
    assert st.count("trades") == n_tr


# ---------- T7: повтор закрытия при kill ----------
def test_retry_kill_close_when_position_remains(tmp_path):
    st, ex, exe, sink = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    orig = ex.market

    async def broken(*a, **k):
        raise ExchangeError("биржа отказала", 10001)
    ex.market = broken
    run(exe.kill("тест"))
    assert st.get("killed") is True and ex.qty == pytest.approx(0.010)                # kill не смог закрыть
    assert run(exe.retry_kill_close()) == "не закрыто" and ex.qty == pytest.approx(0.010) and ex.stop is not None
    ex.market = orig
    sink.msgs.clear()
    assert run(exe.retry_kill_close()) == "закрыто"
    assert ex.qty == 0 and st.get("stop_armed") is False and st.get("acted_exposure") is None
    assert sink.msgs                                                                   # закрытие объявлено владельцу
    assert run(exe.retry_kill_close()) == "позиции нет"


def test_retry_kill_close_noop_when_not_killed(tmp_path):
    st, ex, exe, _ = mk(tmp_path)
    run(exe.reconcile(0.5, BAR))
    assert run(exe.retry_kill_close()) == "позиции нет" and ex.qty == pytest.approx(0.010)
