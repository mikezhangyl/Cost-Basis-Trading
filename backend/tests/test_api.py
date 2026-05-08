from fastapi.testclient import TestClient

from app.data.market_cache import CacheKey, CachePayloadKind, CacheWrite, MarketCacheStore
from app.main import create_app


def test_health_does_not_expose_token_value(monkeypatch) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-token-value")
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["tushare_token_configured"] is True
    assert "secret-token-value" not in response.text


def test_market_cache_summary_reports_read_only_health(monkeypatch, tmp_path) -> None:
    cache_path = tmp_path / "market_data.sqlite3"
    store = MarketCacheStore(cache_path)
    store.upsert(_cache_write(_cache_key("daily", "000001.SZ", "20260401")))
    store.upsert(_cache_write(_cache_key("cyq_chips", "000001.SZ", "20260401"), payload=[{"price": 10.0, "percent": 1.0}]))
    monkeypatch.setenv("MARKET_DATA_CACHE_PATH", str(cache_path))
    client = TestClient(create_app())

    response = client.get("/api/market-cache/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["exists"] is True
    assert payload["data"]["totals"]["current_entries"] == 2
    assert payload["data"]["totals"]["entry_versions"] == 2
    assert payload["data"]["jobs"] == {}
    assert {row["endpoint"] for row in payload["data"]["by_endpoint"]} == {"cyq_chips", "daily"}
    assert "payload_json" not in response.text


def test_market_cache_summary_handles_missing_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MARKET_DATA_CACHE_PATH", str(tmp_path / "missing.sqlite3"))
    client = TestClient(create_app())

    response = client.get("/api/market-cache/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["exists"] is False
    assert payload["data"]["totals"]["current_entries"] == 0


def test_scan_validates_empty_stock_list() -> None:
    client = TestClient(create_app())

    response = client.post("/api/scans", json={"stock_codes": [], "n_days": 10})

    assert response.status_code == 422


def test_backtest_validates_date_format() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/backtests",
        json={
            "stock_code": "600519",
            "start_date": "2026-01-01",
            "window_days": 10,
        },
    )

    assert response.status_code == 422


def test_research_run_validates_start_date_format() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/research-runs",
        json={
            "stock_code": "000001",
            "start_dates": ["2026-01-01"],
            "window_days": 10,
        },
    )

    assert response.status_code == 422


def test_ecc_artifact_reviewer_is_not_a_product_backend_surface() -> None:
    client = TestClient(create_app())

    response = client.get("/api/ecc-artifact-reviews/review-does-not-exist")

    assert response.status_code == 404


def _cache_key(endpoint: str, instrument_id: str, date_key: str) -> CacheKey:
    return CacheKey(
        provider="tushare",
        endpoint=endpoint,
        instrument_id=instrument_id,
        date_key=date_key,
        date_key_role="trade_date",
        semantic_params=_semantic_params_for(endpoint),
    )


def _semantic_params_for(endpoint: str) -> dict[str, object]:
    if endpoint == "cyq_chips":
        return {"schema_version": 1, "fields": ["ts_code", "trade_date", "price", "percent"]}
    return {
        "schema_version": 1,
        "fields": ["ts_code", "trade_date", "open", "close"],
        "price_adjustment": "none",
        "asset": "E",
        "freq": "D",
    }


def _cache_write(
    key: CacheKey,
    payload: object | None = None,
    payload_kind: CachePayloadKind = CachePayloadKind.ROWS,
) -> CacheWrite:
    return CacheWrite(
        key=key,
        payload_kind=payload_kind,
        payload=payload if payload is not None else {"ts_code": key.instrument_id, "trade_date": key.date_key, "close": 100.0},
        source_params={"ts_code": key.instrument_id, "trade_date": key.date_key},
        fetched_at="2026-05-06T01:02:03+00:00",
        provider_updated_at=None,
        cache_schema_version=1,
    )
