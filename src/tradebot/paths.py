"""Единое место для путей проекта."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORAGE = ROOT / "storage"
MARKET = STORAGE / "market"      # кэш свечей/OI/фандинга (parquet)
RESULTS = STORAGE / "results"    # результаты исследований (walk-forward и т.п.)
STATE = STORAGE / "state"        # состояние бота (sqlite)
LOGS = STORAGE / "logs"
WEB_DIST = ROOT / "web" / "dist"

for _p in (MARKET, RESULTS, STATE, LOGS):
    _p.mkdir(parents=True, exist_ok=True)
