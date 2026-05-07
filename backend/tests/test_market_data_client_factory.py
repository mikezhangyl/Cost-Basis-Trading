from pathlib import Path

from app.data.cached_market_data_client import CachedMarketDataClient
from app.data.market_data_client_factory import build_market_data_client


class FakeProviderClient:
    def resolve_trading_days(self, end_date: str | None, n_days: int) -> list[str]:
        return ["20260415"]


def test_factory_wraps_provider_with_cache_when_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MARKET_DATA_CACHE_ENABLED", "true")
    monkeypatch.setenv("MARKET_DATA_CACHE_PATH", str(tmp_path / "market_data.sqlite3"))
    monkeypatch.setenv("MARKET_DATA_CACHE_WRITE_MODE", "sync")

    client = build_market_data_client(provider_client=FakeProviderClient())

    assert isinstance(client, CachedMarketDataClient)
    assert client.provider_client.__class__ is FakeProviderClient
    assert client.cache_writer.auto_flush is False


def test_factory_enables_auto_flush_for_default_async_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MARKET_DATA_CACHE_ENABLED", "true")
    monkeypatch.setenv("MARKET_DATA_CACHE_PATH", str(tmp_path / "market_data.sqlite3"))
    monkeypatch.delenv("MARKET_DATA_CACHE_WRITE_MODE", raising=False)

    client = build_market_data_client(provider_client=FakeProviderClient())

    assert isinstance(client, CachedMarketDataClient)
    assert client.cache_writer.auto_flush is True


def test_factory_returns_provider_when_cache_disabled(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_DATA_CACHE_ENABLED", "false")
    provider = FakeProviderClient()

    client = build_market_data_client(provider_client=provider)

    assert client is provider
