import pytest


@pytest.fixture(autouse=True)
def _isolated_web_cache(tmp_path, monkeypatch):
    """Дисковый кэш веба в тестах — всегда во временной папке (не трогаем storage/results/cache)."""
    from tradebot.web import cache
    monkeypatch.setattr(cache, "DB_PATH", tmp_path / "cache" / "web_cache.sqlite3")
    yield
