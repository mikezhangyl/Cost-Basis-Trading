import sqlite3
from pathlib import Path

from app.data.market_cache import (
    CacheKey,
    CachePayloadKind,
    CacheWrite,
    MarketCacheStore,
    build_semantic_params_hash,
    endpoint_contract_for,
)


def cache_key(
    date_key: str = "20260415",
    semantic_params: dict[str, object] | None = None,
) -> CacheKey:
    return CacheKey(
        provider="tushare",
        endpoint="daily",
        instrument_id="600519.SH",
        date_key=date_key,
        date_key_role="trade_date",
        semantic_params=semantic_params
        or {
            "schema_version": 1,
            "fields": ["ts_code", "trade_date", "open", "close"],
            "price_adjustment": "none",
            "asset": "E",
            "freq": "D",
        },
    )


def cache_write(
    key: CacheKey,
    payload: object | None = None,
    payload_kind: CachePayloadKind = CachePayloadKind.ROWS,
    fetched_at: str = "2026-05-06T00:00:00+00:00",
) -> CacheWrite:
    return CacheWrite(
        key=key,
        payload_kind=payload_kind,
        payload=payload
        if payload is not None
        else {"ts_code": key.instrument_id, "trade_date": key.date_key, "close": 100.0},
        source_params={"ts_code": key.instrument_id, "trade_date": key.date_key},
        fetched_at=fetched_at,
        provider_updated_at=None,
        cache_schema_version=1,
    )


def test_same_key_same_checksum_is_idempotent(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market-cache" / "market_data.sqlite3")
    write = cache_write(cache_key())

    first = store.upsert(write)
    second = store.upsert(write)

    assert first.version_id == second.version_id
    assert store.count_versions() == 1
    assert store.count_conflicts() == 0


def test_expected_provider_correction_keeps_versions_and_updates_current_pointer(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    key = cache_key()
    first = store.upsert(cache_write(key, {"close": 100.0}))

    second = store.upsert(cache_write(key, {"close": 101.0}), allow_provider_correction=True)
    current = store.read_many([key]).hits[key.identity]

    assert first.version_id != second.version_id
    assert current.version_id == second.version_id
    assert current.payload == {"close": 101.0}
    assert store.count_versions() == 2
    assert store.count_conflicts() == 1
    superseded = store.version(first.version_id)
    assert superseded["superseded_at"] is not None


def test_unexpected_checksum_conflict_leaves_current_pointer_unchanged(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    key = cache_key()
    first = store.upsert(cache_write(key, {"close": 100.0}))

    result = store.upsert(cache_write(key, {"close": 101.0}), allow_provider_correction=False)
    current = store.read_many([key]).hits[key.identity]

    assert result.status == "conflict_rejected"
    assert current.version_id == first.version_id
    assert current.payload == {"close": 100.0}
    assert store.count_conflicts() == 1


def test_read_many_returns_hits_misses_and_stale_entries(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3", recent_refresh_days=10, provisional_no_data_ttl_seconds=3600)
    hit_key = cache_key("20260415")
    missing_key = cache_key("20260416")
    stale_key = cache_key("20260505")
    store.upsert(cache_write(hit_key, {"close": 100.0}))
    store.upsert(
        cache_write(
            stale_key,
            {"reason": "provider-lag"},
            CachePayloadKind.PROVISIONAL_NO_DATA,
            fetched_at="2026-05-06T00:00:00+00:00",
        )
    )

    result = store.read_many([hit_key, missing_key, stale_key], current_date="20260506", current_time="2026-05-06T02:00:00+00:00")

    assert set(result.hits) == {hit_key.identity}
    assert result.hits[hit_key.identity].payload == {"close": 100.0}
    assert result.misses == [missing_key.identity]
    assert result.stale == [stale_key.identity]


def test_permanent_no_data_is_a_stable_hit(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3", recent_refresh_days=10)
    key = cache_key("20260505")
    store.upsert(cache_write(key, {"reason": "before_listing"}, CachePayloadKind.PERMANENT_NO_DATA))

    result = store.read_many([key], current_date="20260506")

    assert result.hits[key.identity].payload_kind == CachePayloadKind.PERMANENT_NO_DATA
    assert result.stale == []
    assert result.misses == []


def test_provisional_no_data_is_fresh_until_ttl_even_inside_recent_window(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3", recent_refresh_days=10, provisional_no_data_ttl_seconds=3600)
    key = cache_key("20260505")
    store.upsert(
        cache_write(
            key,
            {"reason": "provider-lag"},
            CachePayloadKind.PROVISIONAL_NO_DATA,
            fetched_at="2026-05-06T00:30:00+00:00",
        )
    )

    result = store.read_many([key], current_date="20260506", current_time="2026-05-06T01:00:00+00:00")

    assert result.hits[key.identity].payload_kind == CachePayloadKind.PROVISIONAL_NO_DATA
    assert result.stale == []


def test_provisional_no_data_becomes_stale_after_ttl(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3", recent_refresh_days=10, provisional_no_data_ttl_seconds=3600)
    key = cache_key("20260505")
    store.upsert(
        cache_write(
            key,
            {"reason": "provider-lag"},
            CachePayloadKind.PROVISIONAL_NO_DATA,
            fetched_at="2026-05-06T00:00:00+00:00",
        )
    )

    result = store.read_many([key], current_date="20260506", current_time="2026-05-06T02:00:00+00:00")

    assert result.hits == {}
    assert result.stale == [key.identity]


def test_recent_rows_become_stale_when_fetched_before_current_date(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3", recent_refresh_days=10)
    key = cache_key("20260505")
    store.upsert(cache_write(key, {"close": 100.0}, fetched_at="2026-05-05T23:00:00+00:00"))

    result = store.read_many([key], current_date="20260506", current_time="2026-05-06T02:00:00+00:00")

    assert result.hits == {}
    assert result.stale == [key.identity]


def test_recent_rows_remain_fresh_when_fetched_on_current_date(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3", recent_refresh_days=10)
    key = cache_key("20260505")
    store.upsert(cache_write(key, {"close": 100.0}, fetched_at="2026-05-06T00:30:00+00:00"))

    result = store.read_many([key], current_date="20260506", current_time="2026-05-06T02:00:00+00:00")

    assert result.hits[key.identity].payload == {"close": 100.0}
    assert result.stale == []


def test_idempotent_provisional_no_data_refresh_updates_fetched_at(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3", provisional_no_data_ttl_seconds=3600)
    key = cache_key("20260505")
    store.upsert(
        cache_write(
            key,
            {"reason": "provider-lag"},
            CachePayloadKind.PROVISIONAL_NO_DATA,
            fetched_at="2026-05-06T00:00:00+00:00",
        )
    )

    result = store.upsert(
        cache_write(
            key,
            {"reason": "provider-lag"},
            CachePayloadKind.PROVISIONAL_NO_DATA,
            fetched_at="2026-05-06T02:00:00+00:00",
        )
    )
    entry = store.read_many([key], current_time="2026-05-06T02:30:00+00:00").hits[key.identity]

    assert result.status == "refreshed"
    assert result.version_id == entry.version_id
    assert entry.fetched_at == "2026-05-06T02:00:00+00:00"


def test_malformed_payload_does_not_return_as_cache_hit(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    key = cache_key()
    version = store.upsert(cache_write(key))
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE market_cache_entry_versions SET payload_json = ? WHERE version_id = ?",
            ("{malformed-json", version.version_id),
        )

    result = store.read_many([key])

    assert result.hits == {}
    assert result.misses == [key.identity]


def test_checksum_mismatch_does_not_return_as_cache_hit(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    key = cache_key()
    version = store.upsert(cache_write(key))
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE market_cache_entry_versions SET payload_json = ? WHERE version_id = ?",
            ('{"close":101.0}', version.version_id),
        )

    result = store.read_many([key])

    assert result.hits == {}
    assert result.misses == [key.identity]


def test_refetch_repairs_checksum_mismatched_current_version(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    key = cache_key()
    write = cache_write(key, {"close": 100.0})
    version = store.upsert(write)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE market_cache_entry_versions SET payload_json = ? WHERE version_id = ?",
            ('{"close":101.0}', version.version_id),
        )

    repair = store.upsert(write)
    result = store.read_many([key])

    assert repair.status == "repaired"
    assert result.hits[key.identity].payload == {"close": 100.0}
    assert store.count_versions() == 2


def test_schema_version_mismatch_does_not_return_as_cache_hit(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    key = cache_key()
    version = store.upsert(cache_write(key))
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE market_cache_entry_versions SET cache_schema_version = ? WHERE version_id = ?",
            (999, version.version_id),
        )

    result = store.read_many([key])

    assert result.hits == {}
    assert result.misses == [key.identity]


def test_refetch_repairs_schema_mismatched_current_version(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    key = cache_key()
    write = cache_write(key, {"close": 100.0})
    version = store.upsert(write)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE market_cache_entry_versions SET cache_schema_version = ? WHERE version_id = ?",
            (999, version.version_id),
        )

    repair = store.upsert(write)
    result = store.read_many([key])

    assert repair.status == "repaired"
    assert result.hits[key.identity].payload == {"close": 100.0}
    assert store.count_versions() == 2


def test_current_pointer_to_wrong_identity_does_not_return_as_cache_hit(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    key = cache_key("20260415")
    other_key = cache_key("20260416")
    store.upsert(cache_write(key, {"close": 100.0}))
    other_version = store.upsert(cache_write(other_key, {"close": 101.0}))
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE market_cache_current_entries
            SET current_version_id = ?
            WHERE provider = ?
              AND endpoint = ?
              AND instrument_id = ?
              AND date_key = ?
              AND date_key_role = ?
              AND semantic_params_hash = ?
            """,
            (
                other_version.version_id,
                key.provider,
                key.endpoint,
                key.instrument_id,
                key.date_key,
                key.date_key_role,
                key.semantic_params_hash,
            ),
        )

    result = store.read_many([key])

    assert result.hits == {}
    assert result.misses == [key.identity]


def test_refetch_repairs_current_pointer_to_wrong_identity(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    key = cache_key("20260415")
    other_key = cache_key("20260416")
    write = cache_write(key, {"close": 100.0})
    store.upsert(write)
    other_version = store.upsert(cache_write(other_key, {"close": 101.0}))
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE market_cache_current_entries
            SET current_version_id = ?
            WHERE provider = ?
              AND endpoint = ?
              AND instrument_id = ?
              AND date_key = ?
              AND date_key_role = ?
              AND semantic_params_hash = ?
            """,
            (
                other_version.version_id,
                key.provider,
                key.endpoint,
                key.instrument_id,
                key.date_key,
                key.date_key_role,
                key.semantic_params_hash,
            ),
        )

    repair = store.upsert(write)
    result = store.read_many([key])

    assert repair.status == "repaired"
    assert result.hits[key.identity].payload == {"close": 100.0}


def test_stock_basic_latest_snapshot_uses_non_trading_date_key_role() -> None:
    params = {
        "schema_version": 1,
        "fields": ["ts_code", "name"],
        "query_scope": {"ts_code": "600519.SH"},
        "exchange": None,
        "list_status": "L",
    }
    key = CacheKey(
        provider="tushare",
        endpoint="stock_basic",
        instrument_id="600519.SH",
        date_key="__latest__",
        date_key_role="latest_snapshot",
        semantic_params=params,
    )

    assert key.identity.date_key == "__latest__"
    assert key.identity.date_key_role == "latest_snapshot"
    assert key.semantic_params_hash == build_semantic_params_hash(params)


def test_date_key_role_is_part_of_current_entry_identity(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    params = {"schema_version": 1, "fields": ["value"], "query_scope": {"ts_code": "ABC"}}
    trade_date_key = CacheKey(
        provider="fixture",
        endpoint="stock_basic",
        instrument_id="ABC",
        date_key="20260415",
        date_key_role="snapshot_date",
        semantic_params=params,
    )
    snapshot_key = CacheKey(
        provider="fixture",
        endpoint="stock_basic",
        instrument_id="ABC",
        date_key="20260415",
        date_key_role="latest_snapshot",
        semantic_params=params,
    )

    store.upsert(cache_write(trade_date_key, {"value": "trade"}))
    store.upsert(cache_write(snapshot_key, {"value": "snapshot"}))
    result = store.read_many([trade_date_key, snapshot_key])

    assert result.hits[trade_date_key.identity].payload == {"value": "trade"}
    assert result.hits[snapshot_key.identity].payload == {"value": "snapshot"}
    assert store.count_versions() == 2


def test_endpoint_contracts_include_required_semantic_params() -> None:
    assert {"price_adjustment", "asset", "freq"} <= endpoint_contract_for("daily").required_semantic_params
    assert {"exchange", "fields"} <= endpoint_contract_for("trade_cal").required_semantic_params
    assert {"query_scope", "fields"} <= endpoint_contract_for("stock_basic").required_semantic_params
    assert "adjustment_anchor_date" in endpoint_contract_for("derived_adjusted_bar").required_semantic_params


def test_store_rejects_missing_required_semantic_params(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    invalid_key = cache_key(semantic_params={"schema_version": 1, "fields": ["close"]})

    try:
        store.upsert(cache_write(invalid_key))
    except ValueError as error:
        assert "Missing semantic params" in str(error)
    else:
        raise AssertionError("Expected invalid daily key to be rejected.")


def test_store_rejects_invalid_date_key_role(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    invalid_key = CacheKey(
        provider="tushare",
        endpoint="daily",
        instrument_id="600519.SH",
        date_key="20260415",
        date_key_role="snapshot_date",
        semantic_params={
            "schema_version": 1,
            "fields": ["ts_code", "trade_date", "open", "close"],
            "price_adjustment": "none",
            "asset": "E",
            "freq": "D",
        },
    )

    try:
        store.upsert(cache_write(invalid_key))
    except ValueError as error:
        assert "date_key_role" in str(error)
    else:
        raise AssertionError("Expected invalid date_key_role to be rejected.")


def test_store_rejects_no_data_for_endpoints_that_do_not_allow_it(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    trade_cal_key = CacheKey(
        provider="tushare",
        endpoint="trade_cal",
        instrument_id="SSE",
        date_key="20260415",
        date_key_role="calendar_date",
        semantic_params={
            "schema_version": 1,
            "exchange": "SSE",
            "fields": ["cal_date", "is_open"],
        },
    )

    try:
        store.upsert(cache_write(trade_cal_key, {"reason": "closed"}, CachePayloadKind.PERMANENT_NO_DATA))
    except ValueError as error:
        assert "does not allow no-data" in str(error)
    else:
        raise AssertionError("Expected no-data marker to be rejected for trade_cal.")
