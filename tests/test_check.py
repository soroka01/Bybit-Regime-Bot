"""`python -m tradebot.app --check`: только чтение. Все запросы к Bybit - на httpx.MockTransport (сети нет), bot.db/bot.lock/bot.log
и Telegram не затрагиваются, ордера и смена настроек невозможны (проверяется по методам запросов)."""
import ast
import asyncio
import time
from pathlib import Path

import httpx
import pytest

from tradebot import app, check
from tradebot.trading import exchange
from tradebot.trading.config import load_env
from tradebot.trading.exchange import BybitClient

TOKEN = "123456789:AAFakeTokenForTestsOnly_0123456789abc"
KEY, SECRET = "AKEYabc123456", "ASECRETdef7890123"
GOOD_ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_OWNER_ID": "42", "BYBIT_API_KEY": KEY, "BYBIT_API_SECRET": SECRET}
ALLOWED = {"/v5/market/time", "/v5/user/query-api", "/v5/account/info", "/v5/position/list", "/v5/account/wallet-balance"}
WALLET = {"list": [{"totalEquity": "163.44", "coin": [{"coin": "USDT", "walletBalance": "163.44", "equity": "163.44"}]}]}
FLAT = {"list": [{"leverage": "2", "tradeMode": 1, "positionIdx": 0, "size": "0"}]}


class Lines:
    def __init__(self):
        self.lines = []

    def __call__(self, s):
        self.lines.append(s)

    @property
    def text(self):
        return "\n".join(self.lines)


def mock_client(*, key=None, account=None, wallet=WALLET, position=FLAT, fail=None, seen=None):
    """BybitClient на MockTransport. fail(path) -> Response|Exception|None - подмена ответа для отдельного пути."""
    key = {"readOnly": 0, "permissions": {"Wallet": ["AccountTransfer"]}, "ips": ["*"]} if key is None else key
    account = {"unifiedMarginStatus": 5, "marginMode": "ISOLATED_MARGIN"} if account is None else account

    def handler(req: httpx.Request):
        if seen is not None:
            seen.append((req.method, req.url.path))
        if fail is not None:
            r = fail(req.url.path)
            if isinstance(r, Exception):
                raise r
            if r is not None:
                return r
        now = time.time()
        res = {"/v5/market/time": {"timeNano": str(int(now * 1e9))}, "/v5/user/query-api": key, "/v5/account/info": account,
               "/v5/position/list": position, "/v5/account/wallet-balance": wallet}.get(req.url.path, {})
        return httpx.Response(200, json={"retCode": 0, "retMsg": "OK", "result": res, "time": int(now * 1000)})
    return BybitClient("https://x", KEY, SECRET, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def run_check(client):
    out = Lines()
    code = asyncio.run(check.run_check(client, out))
    return code, out.text


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    async def instant(_s):
        return None
    monkeypatch.setattr(exchange.asyncio, "sleep", instant)       # повторы GET при сбое сети без реальных пауз


# ---------------------------------------------------------------------------------------------------- всё хорошо
def test_ok_report_exit_0_only_get_requests_and_no_secrets():
    seen = []
    code, text = run_check(mock_client(seen=seen))
    assert code == 0 and "всё в порядке" in text and "[ОШИБКА]" not in text
    assert "плечо 2x, изолированная маржа, односторонний режим" in text and "equity 163.44 $" in text and "позиция: нет" in text
    assert seen and all(m == "GET" for m, _ in seen) and {p for _, p in seen} <= ALLOWED          # ни ордеров, ни setup_account
    assert KEY not in text and SECRET not in text and TOKEN not in text


def test_open_position_is_reported_and_missing_stop_is_only_a_warning():
    pos = {"list": [{"leverage": "2", "tradeMode": 1, "positionIdx": 0, "size": "0.003", "side": "Buy", "avgPrice": "81550.1",
                     "unrealisedPnl": "1.0", "markPrice": "82000", "stopLoss": "75000"}]}
    code, text = run_check(mock_client(position=pos))
    assert code == 0 and "лонг 0.003 BTC" in text and "биржевой стоп 75000" in text
    code, text = run_check(mock_client(position={"list": [{**pos["list"][0], "stopLoss": "0"}]}))
    assert code == 0 and "[ВНИМАНИЕ]" in text and "БЕЗ биржевого стопа" in text


# ---------------------------------------------------------------------------------------------------- права ключа
def test_read_only_key_exit_4():
    code, text = run_check(mock_client(key={"readOnly": 1, "permissions": {"Wallet": []}, "ips": []}))
    assert code == 4 and "только на чтение" in text and "[ОШИБКА]" in text and "всё в порядке" not in text


def test_withdraw_key_exit_4():
    code, text = run_check(mock_client(key={"readOnly": 0, "permissions": {"Wallet": ["AccountTransfer", "Withdraw"]}, "ips": []}))
    assert code == 4 and "Withdraw" in text and "[ОШИБКА]" in text


def test_key_rejected_by_exchange_exit_4():
    def fail(path):
        if path == "/v5/user/query-api":
            return httpx.Response(200, json={"retCode": 10003, "retMsg": "API key is invalid.", "result": {}})
    code, text = run_check(mock_client(fail=fail))
    assert code == 4 and "ключ API отклонён" in text and "API key is invalid" in text


# ---------------------------------------------------------------------------------------------------- настройки аккаунта
@pytest.mark.parametrize("account,position,needle", [
    ({"unifiedMarginStatus": 5, "marginMode": "REGULAR_MARGIN"}, FLAT, "НЕ изолированная"),
    (None, {"list": [{"leverage": "2", "tradeMode": 1, "positionIdx": 1, "size": "0"}]}, "НЕ односторонний"),
    (None, {"list": [{"leverage": "10", "tradeMode": 1, "positionIdx": 0, "size": "0"}]}, "выше потолка бота"),
])
def test_wrong_account_settings_exit_4(account, position, needle):
    code, text = run_check(mock_client(account=account, position=position))
    assert code == 4 and needle in text and "[ОШИБКА]" in text


def test_not_unified_account_wallet_exit_4():
    code, text = run_check(mock_client(wallet={"list": [{"totalEquity": "10", "coin": []}]}))
    assert code == 4 and "баланс не прочитан" in text


# ---------------------------------------------------------------------------------------------------- сеть
@pytest.mark.parametrize("fail", [
    lambda path: httpx.ConnectError("нет сети"),
    lambda path: httpx.ReadTimeout("таймаут"),
    lambda path: httpx.Response(503, json={}),
    lambda path: httpx.Response(200, content=b"<html>captive portal</html>"),
])
def test_network_failure_exit_1(fail):
    seen = []
    code, text = run_check(mock_client(fail=fail, seen=seen))
    assert code == 1 and "биржа недоступна" in text and "[ОШИБКА]" in text
    assert all(m == "GET" for m, _ in seen)


def test_network_failure_midway_reports_what_was_checked():
    def fail(path):
        if path == "/v5/account/wallet-balance":
            return httpx.ConnectError("обрыв")
    code, text = run_check(mock_client(fail=fail))
    assert code == 1 and "ключ API: торговать можно" in text and "биржа недоступна" in text


def test_garbage_json_shape_exit_1():
    def fail(path):
        if path == "/v5/market/time":
            return httpx.Response(200, json={"retCode": 0, "result": []})
    code, text = run_check(mock_client(fail=fail))
    assert code == 1 and "ответ биржи не разобран" in text


# ---------------------------------------------------------------------------------------------------- .env и точка входа
@pytest.mark.parametrize("bad_env,needle", [
    ({k: v for k, v in GOOD_ENV.items() if k != "BYBIT_API_SECRET"}, "BYBIT_API_KEY"),
    ({**GOOD_ENV, "PAPER": "1"}, "PAPER больше не поддерживается"),
    ({**GOOD_ENV, "PAPER": "true"}, "PAPER больше не поддерживается"),
    ({**GOOD_ENV, "BYBIT_ENV": "testnet"}, "testnet"),
    ({**GOOD_ENV, "TELEGRAM_BOT_TOKEN": "bad"}, "TELEGRAM_BOT_TOKEN"),
    ({**GOOD_ENV, "TELEGRAM_OWNER_ID": ""}, "TELEGRAM_OWNER_ID"),
])
def test_bad_env_exit_4_without_any_request(bad_env, needle):
    out = Lines()

    def boom(*a):
        raise AssertionError("клиент не должен создаваться при плохом .env")
    assert check.check_main(bad_env, client_factory=boom, out=out) == 4
    assert "Ошибка конфигурации" in out.text and needle in out.text
    assert KEY not in out.text and SECRET not in out.text and TOKEN not in out.text


def test_legacy_values_that_only_disable_are_ignored():
    out = Lines()
    env = {**GOOD_ENV, "PAPER": "0", "BYBIT_ENV": "mainnet", "I_UNDERSTAND_MAINNET": "1"}
    assert check.check_main(env, client_factory=lambda u, k, s: mock_client(), out=out) == 0


def test_check_main_uses_mainnet_url_and_keys_from_env():
    got = {}

    def factory(url, k, s):
        got.update(url=url, key=k, secret=s)
        return mock_client()
    assert check.check_main(GOOD_ENV, client_factory=factory, out=Lines()) == 0
    assert got == {"url": "https://api.bybit.com", "key": KEY, "secret": SECRET}


def test_app_main_check_flag_touches_no_db_lock_log_or_telegram(monkeypatch, tmp_path):
    from tradebot.trading import state

    def forbidden(*a, **k):
        raise AssertionError("--check не должен этого делать")
    monkeypatch.setattr(app, "acquire_lock", forbidden)
    monkeypatch.setattr(app, "setup_logging", forbidden)
    monkeypatch.setattr(app, "disable_quickedit", forbidden)
    monkeypatch.setattr(state.Store, "__init__", forbidden)
    monkeypatch.setattr(app, "amain", forbidden)
    monkeypatch.setattr(check, "load_env", lambda environ=None: load_env(GOOD_ENV))          # настоящий .env не читаем
    monkeypatch.setattr(check, "BybitClient", lambda url, k, s: mock_client())
    assert app.main(["--check"]) == 0


def test_check_module_does_not_import_state_telegram_or_runner():
    tree = ast.parse(Path(check.__file__).read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
    banned = ("aiogram", "tradebot.telegram", "tradebot.trading.state", "tradebot.trading.executor", "tradebot.trading.runner")
    assert not [m for m in mods if m.startswith(banned)], mods
    called = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not called & {"setup_account", "market", "set_stop", "cancel_order", "set_leverage", "_call"}      # методов записи нет
