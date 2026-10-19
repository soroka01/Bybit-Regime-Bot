import httpx
import pytest
import requests


@pytest.fixture(autouse=True)
def _isolated_web_cache(tmp_path, monkeypatch):
    """Дисковый кэш веба в тестах — всегда во временной папке (не трогаем storage/results/cache)."""
    from tradebot.web import cache
    monkeypatch.setattr(cache, "DB_PATH", tmp_path / "cache" / "web_cache.sqlite3")
    yield


# ---------------------------------------------------------------------------------------------- запрет сети в тестах
class NetworkBlocked(RuntimeError):
    """Тест попытался выйти в реальную сеть."""


def _blocked(what):
    def deny(*args, **kwargs):
        raise NetworkBlocked(
            f"Сеть в тестах запрещена ({what}). Подмени транспорт (httpx.MockTransport / monkeypatch) "
            "или пометь тест @pytest.mark.network.")
    return deny


@pytest.fixture(autouse=True)
def _no_real_network(request, monkeypatch):
    """Блокирует реальные HTTP-запросы на уровне ТРАНСПОРТА, пока тест не помечен @pytest.mark.network.
    Патчим именно реальные сетевые транспорты: httpx.MockTransport и ASGI-транспорт TestClient (starlette) —
    отдельные классы и продолжают работать, поэтому тесты с моками и TestClient не затрагиваются."""
    if request.node.get_closest_marker("network"):
        yield
        return
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _blocked("httpx async"))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked("httpx sync"))
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _blocked("requests"))
    monkeypatch.setattr(requests.Session, "request", _blocked("requests"))   # тесты могут подменить сами — monkeypatch откатит
    try:
        import aiohttp                                   # aiogram ходит через aiohttp; если установлен — закрываем и его
    except ImportError:
        pass
    else:
        monkeypatch.setattr(aiohttp.ClientSession, "_request", _blocked("aiohttp"))
    yield
