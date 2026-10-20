"""Рефакторинг stats.py / api.py: все прежние публичные пути импорта продолжают работать (реэкспорт)."""
import pytest

STATS_NAMES = """TAKER_FEE_EST RANGE_SECONDS DEFAULT_RANGE COMPARE_MIN_S SETTLE_S BAR_S PRICE_TOL_S EPS_QTY EQ_POINTS_MAX GAP_S
MIN_DAYS_SHARPE MIN_DAYS_CALMAR FEW_TRADES MAX_TRADE_ROWS EQ_ENTRY_MAX_AGE_S EQ_LOOKBACK_S ANN DEFAULT_STOP_PCT STOP_NOTICE
EXACT_MATCH_S EXACT_QTY_TOL STOP_REASON SYMBOL TF CACHE_SIZE CACHE_TTL_S DEFINITIONS _num parse_ts _ts iso _day reason_group _pct
Market market_from_df funding_between _Acc _fill_rec _reconstruct_exit _finish _rt_exact _set_exact apply_exact clean_fills replay
state_at select_series _bucket_edges downsample equity_metrics _bucket_kind bucket_start _next_bucket pnl_buckets histogram
_group_row _streaks trade_metrics COMPARE_KEYS COMPARE_EQ_KEYS _cmp_row unavailable _warn _pos_dict compute_stats equity_at _row
_reconcile _compare _finish_compare _warnings _db_uri fingerprint read_db _eq_arrays load_market _market_sig _cache _cache_lock
_now get_stats""".split()
API_NAMES = """app run_heavy Lane _calc _net NET_KINDS HEAVY_TIMEOUT QUEUE_WAIT FETCH_TIMEOUT NET_QUEUE_WAIT QUEUE_SLICE WATCHDOG_FACTOR
_err _map_exc heavy_endpoint etagged _cache _cache_get _cache_put _encode _json SameOriginGuard CACHE_SIZE BOT_ALIVE_S""".split()


@pytest.mark.parametrize("name", STATS_NAMES)
def test_stats_public_names(name):
    from tradebot.web import stats
    assert hasattr(stats, name), name


@pytest.mark.parametrize("name", API_NAMES)
def test_api_public_names(name):
    from tradebot.web import api
    assert hasattr(api, name), name


def test_split_modules_define_what_stats_reexports():
    from tradebot.web import stats, stats_equity, stats_replay, stats_series, stats_trades, stats_io, stats_market, lanes, api
    assert stats.replay is stats_replay.replay and stats.equity_metrics is stats_equity.equity_metrics
    assert stats.pnl_buckets is stats_series.pnl_buckets and stats.trade_metrics is stats_trades.trade_metrics
    assert stats.read_db is stats_io.read_db and stats.Market is stats_market.Market
    assert api.Lane is lanes.Lane and api._calc is lanes._calc and api._err is lanes._err
