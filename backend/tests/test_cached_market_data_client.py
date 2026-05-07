from datetime import datetime, timedelta
from pathlib import Path

from app.data.cache_writer import CacheWriteMode, CacheWriter
from app.data.cached_market_data_client import CachedMarketDataClient, PriceSemantics
from app.data.market_cache import CacheKey, CachePayloadKind, CacheWrite, MarketCacheStore
from app.domain.errors import DataErrorCode, DataUnavailableError
from app.domain.models import AdjustmentFactor, ChipDistributionPoint, DailyPriceBar


class FakeProviderClient:
    def __init__(self) -> None:
        self.daily_calls: list[tuple[str, str, str]] = []
        self.chip_calls: list[tuple[str, str, str]] = []
        self.adjustment_calls: list[tuple[str, str, str]] = []
        self.trading_days = ["20260415", "20260416", "20260417"]

    def get_trading_days_between(self, start_date: str, end_date: str) -> list[str]:
        return [date for date in self.trading_days if start_date <= date <= end_date]

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        self.daily_calls.append((ts_code, start_date, end_date))
        return [
            DailyPriceBar(
                ts_code=ts_code,
                trade_date=trade_date,
                open=10.0,
                high=11.0,
                low=9.0,
                close=10.0 + index,
            )
            for index, trade_date in enumerate(self.get_trading_days_between(start_date, end_date))
        ]

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        self.chip_calls.append((ts_code, start_date, end_date))
        return [
            ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=10.0, percent=1.0)
            for trade_date in self.get_trading_days_between(start_date, end_date)
        ]

    def get_adjustment_factors(self, ts_code: str, start_date: str, end_date: str) -> list[AdjustmentFactor]:
        self.adjustment_calls.append((ts_code, start_date, end_date))
        factors = {
            "20260415": 1.0,
            "20260416": 1.5,
            "20260417": 2.0,
        }
        return [
            AdjustmentFactor(ts_code=ts_code, trade_date=trade_date, adj_factor=factors[trade_date])
            for trade_date in self.get_trading_days_between(start_date, end_date)
        ]


class CalendarProviderClient(FakeProviderClient):
    def __init__(self) -> None:
        super().__init__()
        self.calendar_calls: list[tuple[str, str]] = []

    def get_trade_calendar(self, start_date: str, end_date: str) -> list[dict[str, object]]:
        self.calendar_calls.append((start_date, end_date))
        start = datetime.strptime(start_date, "%Y%m%d").date()
        end = datetime.strptime(end_date, "%Y%m%d").date()
        return [
            {
                "cal_date": (start + timedelta(days=offset)).strftime("%Y%m%d"),
                "is_open": (start + timedelta(days=offset)).strftime("%Y%m%d") in self.trading_days,
            }
            for offset in range((end - start).days + 1)
        ]


class SparseProviderClient(FakeProviderClient):
    def __init__(self) -> None:
        super().__init__()
        self.trading_days = ["20200115", "20200116", "20200117"]

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        self.daily_calls.append((ts_code, start_date, end_date))
        return [
            DailyPriceBar(
                ts_code=ts_code,
                trade_date=trade_date,
                open=10.0,
                high=11.0,
                low=9.0,
                close=10.0 + index,
            )
            for index, trade_date in enumerate(self.get_trading_days_between(start_date, end_date))
            if trade_date != "20200116"
        ]


class EmptyDataProviderClient(FakeProviderClient):
    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        self.daily_calls.append((ts_code, start_date, end_date))
        raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "No daily price rows returned.")


def store_and_client(tmp_path: Path, provider: FakeProviderClient | None = None) -> tuple[MarketCacheStore, CachedMarketDataClient, FakeProviderClient]:
    provider = provider or FakeProviderClient()
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    writer = CacheWriter(store, mode=CacheWriteMode.SYNC)
    return store, CachedMarketDataClient(provider, store, writer), provider


def test_daily_prices_full_cache_hit_does_not_call_provider_daily(tmp_path: Path) -> None:
    store, client, provider = store_and_client(tmp_path)
    for date, close in [("20260415", 10.0), ("20260416", 11.0), ("20260417", 12.0)]:
        store.upsert(daily_write("600519.SH", date, close))

    rows = client.get_daily_prices("600519.SH", "20260415", "20260417")

    assert [row.trade_date for row in rows] == ["20260415", "20260416", "20260417"]
    assert [row.close for row in rows] == [10.0, 11.0, 12.0]
    assert provider.daily_calls == []


def test_daily_prices_partial_hit_fetches_only_missing_dates(tmp_path: Path) -> None:
    store, client, provider = store_and_client(tmp_path)
    store.upsert(daily_write("600519.SH", "20260415", 10.0))
    store.upsert(daily_write("600519.SH", "20260417", 12.0))

    rows = client.get_daily_prices("600519.SH", "20260415", "20260417")

    assert [row.trade_date for row in rows] == ["20260415", "20260416", "20260417"]
    assert provider.daily_calls == [("600519.SH", "20260416", "20260416")]
    assert store.count_versions() == 3


def test_trade_calendar_is_cached_for_repeated_range_resolution(tmp_path: Path) -> None:
    provider = CalendarProviderClient()
    _store, client, _provider = store_and_client(tmp_path, provider)

    first = client.get_daily_prices("600519.SH", "20260415", "20260417")
    second = client.get_daily_prices("600519.SH", "20260415", "20260417")

    assert [row.trade_date for row in first] == ["20260415", "20260416", "20260417"]
    assert [row.trade_date for row in second] == ["20260415", "20260416", "20260417"]
    assert provider.calendar_calls == [("20260415", "20260417")]
    assert provider.daily_calls == [("600519.SH", "20260415", "20260417")]


def test_resolve_trading_days_uses_cached_trade_calendar(tmp_path: Path) -> None:
    provider = CalendarProviderClient()
    _store, client, _provider = store_and_client(tmp_path, provider)

    first = client.resolve_trading_days("20260417", 2)
    second = client.resolve_trading_days("20260417", 2)

    assert first == ["20260416", "20260417"]
    assert second == ["20260416", "20260417"]
    assert provider.calendar_calls == [("20260318", "20260417")]


def test_cached_client_emits_cache_events(tmp_path: Path) -> None:
    store, client, provider = store_and_client(tmp_path)
    store.upsert(daily_write("600519.SH", "20260415", 10.0))
    events: list[dict] = []
    client.set_cache_event_handler(events.append)

    rows = client.get_daily_prices("600519.SH", "20260415", "20260417")

    assert [row.trade_date for row in rows] == ["20260415", "20260416", "20260417"]
    assert provider.daily_calls == [("600519.SH", "20260416", "20260417")]
    assert events == [
        {
            "provider": "tushare",
            "endpoint": "daily",
            "ts_code": "600519.SH",
            "start_date": "20260415",
            "end_date": "20260417",
            "requested_date_count": 3,
            "hit_count": 1,
            "miss_count": 2,
            "stale_count": 0,
            "suppressed_no_data_count": 0,
            "fetched_date_count": 2,
            "returned_row_count": 3,
            "write_status_counts": {"written": 2},
        }
    ]


def test_chip_distribution_partial_hit_fetches_only_missing_dates(tmp_path: Path) -> None:
    store, client, provider = store_and_client(tmp_path)
    store.upsert(chip_write("600519.SH", "20260415"))
    store.upsert(chip_write("600519.SH", "20260417"))

    rows = client.get_chip_distribution("600519.SH", "20260415", "20260417")

    assert [row.trade_date for row in rows] == ["20260415", "20260416", "20260417"]
    assert provider.chip_calls == [("600519.SH", "20260416", "20260416")]
    assert store.count_versions() == 3


def test_permanent_no_data_marker_suppresses_provider_fetch(tmp_path: Path) -> None:
    store, client, provider = store_and_client(tmp_path)
    store.upsert(daily_write("600519.SH", "20260415", 10.0))
    store.upsert(
        daily_write(
            "600519.SH",
            "20260416",
            payload={"reason": "suspended"},
            payload_kind=CachePayloadKind.PERMANENT_NO_DATA,
        )
    )
    store.upsert(daily_write("600519.SH", "20260417", 12.0))

    rows = client.get_daily_prices("600519.SH", "20260415", "20260417")

    assert [row.trade_date for row in rows] == ["20260415", "20260417"]
    assert provider.daily_calls == []


def test_provider_missing_dates_are_cached_as_provisional_no_data(tmp_path: Path) -> None:
    provider = SparseProviderClient()
    store, client, _ = store_and_client(tmp_path, provider)

    first_rows = client.get_daily_prices("600519.SH", "20200115", "20200117")
    second_rows = client.get_daily_prices("600519.SH", "20200115", "20200117")

    no_data_key = daily_key("600519.SH", "20200116")
    no_data_entry = store.read_many([no_data_key]).hits[no_data_key.identity]
    assert [row.trade_date for row in first_rows] == ["20200115", "20200117"]
    assert [row.trade_date for row in second_rows] == ["20200115", "20200117"]
    assert no_data_entry.payload_kind == CachePayloadKind.PROVISIONAL_NO_DATA
    assert provider.daily_calls == [("600519.SH", "20200115", "20200117")]


def test_provider_errors_do_not_create_cache_entries(tmp_path: Path) -> None:
    class FailingProvider(FakeProviderClient):
        def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
            self.daily_calls.append((ts_code, start_date, end_date))
            raise DataUnavailableError(DataErrorCode.NETWORK_ERROR, "provider down")

    store, client, provider = store_and_client(tmp_path, FailingProvider())

    try:
        client.get_daily_prices("600519.SH", "20260415", "20260417")
    except DataUnavailableError as error:
        assert error.code == DataErrorCode.NETWORK_ERROR
    else:
        raise AssertionError("Expected provider error to propagate.")

    assert provider.daily_calls == [("600519.SH", "20260415", "20260417")]
    assert store.count_versions() == 0


def test_provider_empty_data_writes_provisional_no_data_for_requested_dates(tmp_path: Path) -> None:
    provider = EmptyDataProviderClient()
    store = MarketCacheStore(tmp_path / "market_data.sqlite3", recent_refresh_days=10, provisional_no_data_ttl_seconds=3600)
    writer = CacheWriter(store, mode=CacheWriteMode.SYNC)
    client = CachedMarketDataClient(provider, store, writer)

    first_rows = client.get_daily_prices("600519.SH", "20260415", "20260417")
    second_rows = client.get_daily_prices("600519.SH", "20260415", "20260417")

    keys = [daily_key("600519.SH", date) for date in ["20260415", "20260416", "20260417"]]
    entries = store.read_many(keys).hits
    assert first_rows == []
    assert second_rows == []
    assert all(entries[key.identity].payload_kind == CachePayloadKind.PROVISIONAL_NO_DATA for key in keys)
    assert provider.daily_calls == [("600519.SH", "20260415", "20260417")]


def test_qfq_price_request_rejects_future_anchor_date(tmp_path: Path) -> None:
    _store, client, _provider = store_and_client(tmp_path)

    try:
        client.get_daily_prices(
            "600519.SH",
            "20260415",
            "20260417",
            semantics=PriceSemantics(price_mode="qfq", data_horizon="20260417", adjustment_anchor_date="20260418"),
        )
    except ValueError as error:
        assert "adjustment_anchor_date" in str(error)
    else:
        raise AssertionError("Expected future qfq anchor to be rejected.")


def test_return_adjusted_price_request_rejects_anchor_date(tmp_path: Path) -> None:
    _store, client, _provider = store_and_client(tmp_path)

    try:
        client.get_daily_prices(
            "600519.SH",
            "20260415",
            "20260417",
            semantics=PriceSemantics(
                price_mode="return_adjusted",
                data_horizon="20260417",
                adjustment_anchor_date="20260417",
            ),
        )
    except ValueError as error:
        assert "adjustment_anchor_date" in str(error)
    else:
        raise AssertionError("Expected return_adjusted anchor to be rejected.")


def test_qfq_price_request_with_valid_anchor_does_not_return_raw_bars(tmp_path: Path) -> None:
    _store, client, provider = store_and_client(tmp_path)

    try:
        client.get_daily_prices(
            "600519.SH",
            "20260415",
            "20260417",
            semantics=PriceSemantics(price_mode="qfq", data_horizon="20260417", adjustment_anchor_date="20260417"),
        )
    except NotImplementedError as error:
        assert "qfq" in str(error)
    else:
        raise AssertionError("Expected qfq bars to be rejected until materialized explicitly.")

    assert provider.daily_calls == []


def test_return_adjusted_price_request_does_not_return_raw_bars(tmp_path: Path) -> None:
    _store, client, provider = store_and_client(tmp_path)

    try:
        client.get_daily_prices(
            "600519.SH",
            "20260415",
            "20260417",
            semantics=PriceSemantics(price_mode="return_adjusted", data_horizon="20260417"),
        )
    except NotImplementedError as error:
        assert "return_adjusted" in str(error)
    else:
        raise AssertionError("Expected return_adjusted bars to be rejected until materialized explicitly.")

    assert provider.daily_calls == []


def test_adjustment_factors_are_cached_after_first_fetch(tmp_path: Path) -> None:
    _store, client, provider = store_and_client(tmp_path)

    first = client.get_adjustment_factors("600519.SH", "20260415", "20260417")
    second = client.get_adjustment_factors("600519.SH", "20260415", "20260417")

    assert [factor.adj_factor for factor in first] == [1.0, 1.5, 2.0]
    assert [factor.adj_factor for factor in second] == [1.0, 1.5, 2.0]
    assert provider.adjustment_calls == [("600519.SH", "20260415", "20260417")]


def test_adjusted_return_uses_raw_close_times_adjustment_factor(tmp_path: Path) -> None:
    class SplitLikeProvider(FakeProviderClient):
        def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
            self.daily_calls.append((ts_code, start_date, end_date))
            bars = {
                "20260415": DailyPriceBar(ts_code=ts_code, trade_date="20260415", open=100, high=100, low=100, close=100),
                "20260417": DailyPriceBar(ts_code=ts_code, trade_date="20260417", open=50, high=50, low=50, close=50),
            }
            return [bars[date] for date in self.get_trading_days_between(start_date, end_date) if date in bars]

        def get_adjustment_factors(self, ts_code: str, start_date: str, end_date: str) -> list[AdjustmentFactor]:
            self.adjustment_calls.append((ts_code, start_date, end_date))
            factors = {
                "20260415": AdjustmentFactor(ts_code=ts_code, trade_date="20260415", adj_factor=1.0),
                "20260417": AdjustmentFactor(ts_code=ts_code, trade_date="20260417", adj_factor=2.0),
            }
            return [factors[date] for date in self.get_trading_days_between(start_date, end_date) if date in factors]

    _store, client, _provider = store_and_client(tmp_path, SplitLikeProvider())

    adjusted_return = client.calculate_adjusted_return("600519.SH", "20260415", "20260417", data_horizon="20260417")

    assert adjusted_return == 0


def test_cached_client_passes_through_metadata_and_trading_day_methods(tmp_path: Path) -> None:
    class MetadataProvider(FakeProviderClient):
        def resolve_trading_days(self, end_date: str | None, n_days: int) -> list[str]:
            return ["20260417"]

        def resolve_trading_days_from(self, start_date: str, n_days: int) -> list[str]:
            return ["20260415", "20260416"]

        def get_stock_name(self, ts_code: str) -> str:
            return "贵州茅台"

    _store, client, _provider = store_and_client(tmp_path, MetadataProvider())

    assert client.resolve_trading_days(None, 1) == ["20260417"]
    assert client.resolve_trading_days_from("20260415", 2) == ["20260415", "20260416"]
    assert client.get_stock_name("600519.SH") == "贵州茅台"


def daily_write(
    ts_code: str,
    trade_date: str,
    close: float | None = None,
    payload: object | None = None,
    payload_kind: CachePayloadKind = CachePayloadKind.ROWS,
) -> CacheWrite:
    return CacheWrite(
        key=daily_key(ts_code, trade_date),
        payload_kind=payload_kind,
        payload=payload
        if payload is not None
        else {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "pre_close": None,
            "pct_chg": None,
            "vol": None,
            "amount": None,
        },
        source_params={"ts_code": ts_code, "trade_date": trade_date},
        fetched_at="2026-05-06T00:00:00+00:00",
        provider_updated_at=None,
        cache_schema_version=1,
    )


def chip_write(ts_code: str, trade_date: str) -> CacheWrite:
    return CacheWrite(
        key=chip_key(ts_code, trade_date),
        payload_kind=CachePayloadKind.ROWS,
        payload=[{"ts_code": ts_code, "trade_date": trade_date, "price": 10.0, "percent": 1.0}],
        source_params={"ts_code": ts_code, "trade_date": trade_date},
        fetched_at="2026-05-06T00:00:00+00:00",
        provider_updated_at=None,
        cache_schema_version=1,
    )


def daily_key(ts_code: str, trade_date: str) -> CacheKey:
    return CacheKey(
        provider="tushare",
        endpoint="daily",
        instrument_id=ts_code,
        date_key=trade_date,
        date_key_role="trade_date",
        semantic_params={
            "schema_version": 1,
            "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"],
            "price_adjustment": "none",
            "asset": "E",
            "freq": "D",
        },
    )


def chip_key(ts_code: str, trade_date: str) -> CacheKey:
    return CacheKey(
        provider="tushare",
        endpoint="cyq_chips",
        instrument_id=ts_code,
        date_key=trade_date,
        date_key_role="trade_date",
        semantic_params={
            "schema_version": 1,
            "fields": ["ts_code", "trade_date", "price", "percent"],
        },
    )
