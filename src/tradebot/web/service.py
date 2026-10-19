"""ФАСАД вычислительного ядра веб-API. Реализация разнесена по модулям:
  common.py     Deadline
  data.py       файлы, кэш DataFrame, статус/докачка/загрузка, отпечатки данных, data_version
  meta.py       константы, границы слайдеров, очистка параметров, /api/meta
  backtests.py  бэктест, развёртка, портфель, упаковка (даунсэмплинг, маркеры, сделки)
  research.py   optimize, рейтинг (leaderboard)
  wf.py         walk-forward
Здесь — только реэкспорт всех имён (`from tradebot.web import service` работает как раньше). Подмодули читают
подменяемые имена (MARKET, RESULTS, _optimize, _info_cache, invalidate_data, …) через этот модуль в момент вызова,
поэтому monkeypatch.setattr(service, "...", ...) в тестах продолжает действовать.
Сеть не трогается никогда, кроме явного fetch_data()/update_data()."""
from __future__ import annotations

# реэкспорт зависимостей, которые раньше были видны как service.<имя>
from tradebot.core import optimize as opt  # noqa: F401
from tradebot.core import overlays as ov  # noqa: F401
from tradebot.core.engine import backtest, monthly_returns  # noqa: F401
from tradebot.core.portfolio import BASE_TF, DEFAULT_LEGS, Leg, exposure  # noqa: F401
from tradebot.core.strategies import STRATEGIES, Strategy  # noqa: F401
from tradebot.market import derivs, klines  # noqa: F401
from tradebot.paths import MARKET, RESULTS  # noqa: F401  (подменяются в тестах: читаются подмодулями через фасад)
from tradebot.web import cache  # noqa: F401
from tradebot.web import serialize as S  # noqa: F401
from tradebot.web.schemas import (SYMBOLS, BacktestReq, CachedReq, LbItemReq, OptimizeReq, Overlays,  # noqa: F401
                                  PortfolioReq, SweepReq)

from tradebot.web.common import Deadline  # noqa: F401,E402
from tradebot.web.meta import (DERIV_STRATEGIES, MAX_SWEEP_CELLS, PORTFOLIO_TFS, TIMEFRAMES, WF_LEVS, wf_levs,  # noqa: F401,E402
                               _nice_step, clean_params, get_strategy, meta, param_specs)
from tradebot.web.data import (DF_CACHE_SIZE, DataEmpty, DataMissing, _df_cache, _df_lock, _df_put, _file_info,  # noqa: F401,E402
                               _files, _fp_part, _info_cache, _last_ts, available, data_fingerprint, data_sig,
                               data_status, data_version, fetch_data, fetch_estimate, get_df, get_df_lite,
                               invalidate_data, klines_available, purge_cache, update_data)
from tradebot.web.backtests import (_markers, _package, _signal, _slice, _trade_geometry, _trade_rows,  # noqa: F401,E402
                                    eff_days, run_backtest, run_portfolio, run_sweep)
from tradebot.web.wf import _cached_file, _wf_cache, _wf_symbol, _wf_tag, wf_curve, wf_table, wf_windows  # noqa: F401,E402
from tradebot.web.research import (_compute_lb_item, _optimize, lb_cached, lb_key, lb_plan, run_lb_item,  # noqa: F401,E402
                                   run_optimize)
