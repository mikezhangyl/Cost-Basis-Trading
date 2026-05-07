from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.core.config import load_environment
from app.data.cache_writer import CacheWriteMode, CacheWriter
from app.data.cached_market_data_client import CachedMarketDataClient
from app.data.market_cache import MarketCacheStore
from app.data.tushare_client import TushareMarketDataClient


def build_market_data_client(provider_client: Any | None = None) -> Any:
    load_environment()
    provider = provider_client or TushareMarketDataClient()
    if not _cache_enabled():
        return provider

    cache_store = MarketCacheStore(
        _cache_path(),
        recent_refresh_days=_int_env("MARKET_DATA_CACHE_RECENT_REFRESH_DAYS", 10),
        provisional_no_data_ttl_seconds=_int_env("MARKET_DATA_CACHE_PROVISIONAL_NO_DATA_TTL_SECONDS", 24 * 60 * 60),
    )
    write_mode = CacheWriteMode(_cache_write_mode())
    cache_writer = CacheWriter(cache_store, mode=write_mode, auto_flush=write_mode == CacheWriteMode.ASYNC)
    return CachedMarketDataClient(provider, cache_store, cache_writer)


def _cache_enabled() -> bool:
    raw_value = os.getenv("MARKET_DATA_CACHE_ENABLED", "true").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _cache_path() -> Path:
    raw_value = os.getenv("MARKET_DATA_CACHE_PATH", "data/market-cache/market_data.sqlite3")
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parents[3] / path


def _cache_write_mode() -> str:
    return os.getenv("MARKET_DATA_CACHE_WRITE_MODE", CacheWriteMode.ASYNC.value)


def _int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default
