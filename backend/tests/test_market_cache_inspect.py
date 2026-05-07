import json
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.data.cache_writer import CacheWriteMode, CacheWriter
from app.data.market_cache import CacheKey, CachePayloadKind, CacheWrite, MarketCacheStore
from scripts.market_cache_inspect import inspect_entries, inspect_jobs, inspect_summary


def test_market_cache_inspect_summary_reports_counts_without_payloads(tmp_path: Path) -> None:
    cache_path = tmp_path / "market_data.sqlite3"
    store = MarketCacheStore(cache_path)
    store.upsert(cache_write(cache_key("daily", "600519.SH", "20260415"), payload={"close": 100.0}))
    store.upsert(cache_write(cache_key("cyq_chips", "600519.SH", "20260415"), payload=[{"price": 10.0, "percent": 1.0}]))
    writer = CacheWriter(store, mode=CacheWriteMode.ASYNC, max_attempts=1)
    writer.write(cache_write(cache_key("daily", "600519.SH", "20260416"), payload={"token": "must-not-leak"}))
    writer.flush()

    summary = inspect_summary(cache_path)

    assert summary["exists"] is True
    assert summary["totals"]["current_entries"] == 3
    assert summary["totals"]["entry_versions"] == 3
    assert summary["jobs"]["SUCCEEDED"] == 1
    assert {row["endpoint"] for row in summary["by_endpoint"]} == {"cyq_chips", "daily"}
    assert "must-not-leak" not in json.dumps(summary)


def test_market_cache_inspect_entries_supports_filters_and_omits_payloads(tmp_path: Path) -> None:
    cache_path = tmp_path / "market_data.sqlite3"
    store = MarketCacheStore(cache_path)
    store.upsert(cache_write(cache_key("daily", "600519.SH", "20260415"), payload={"close": 100.0}))
    store.upsert(cache_write(cache_key("daily", "000001.SZ", "20260415"), payload={"close": 10.0}))

    entries = inspect_entries(cache_path, endpoint="daily", instrument_id="600519.SH", limit=10)

    assert len(entries) == 1
    assert entries[0]["instrument_id"] == "600519.SH"
    assert entries[0]["endpoint"] == "daily"
    assert "payload_json" not in entries[0]
    assert "source_params_json" not in entries[0]


def test_market_cache_inspect_jobs_reports_failed_errors_without_payloads(tmp_path: Path, monkeypatch) -> None:
    cache_path = tmp_path / "market_data.sqlite3"
    store = MarketCacheStore(cache_path)
    writer = CacheWriter(store, mode=CacheWriteMode.ASYNC, max_attempts=1)
    writer.write(cache_write(cache_key("daily", "600519.SH", "20260415"), payload={"close": 100.0}))

    monkeypatch.setattr(store, "upsert", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("failed token=abc123")))
    writer.flush()

    jobs = inspect_jobs(cache_path, status="FAILED_PERMANENT", limit=10)

    assert len(jobs) == 1
    assert jobs[0]["status"] == "FAILED_PERMANENT"
    assert jobs[0]["last_error"] == "failed token=[REDACTED]"
    assert "payload_json" not in jobs[0]


def test_market_cache_inspect_cli_prints_json_summary(tmp_path: Path) -> None:
    cache_path = tmp_path / "market_data.sqlite3"
    MarketCacheStore(cache_path).upsert(cache_write(cache_key("daily", "600519.SH", "20260415")))

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/market_cache_inspect.py",
            "summary",
            "--cache-path",
            str(cache_path),
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["totals"]["current_entries"] == 1
    assert payload["by_endpoint"][0]["endpoint"] == "daily"


def cache_key(endpoint: str, instrument_id: str, date_key: str) -> CacheKey:
    return CacheKey(
        provider="tushare",
        endpoint=endpoint,
        instrument_id=instrument_id,
        date_key=date_key,
        date_key_role="trade_date",
        semantic_params=semantic_params_for(endpoint),
    )


def semantic_params_for(endpoint: str) -> dict[str, object]:
    if endpoint == "cyq_chips":
        return {"schema_version": 1, "fields": ["ts_code", "trade_date", "price", "percent"]}
    return {
        "schema_version": 1,
        "fields": ["ts_code", "trade_date", "open", "close"],
        "price_adjustment": "none",
        "asset": "E",
        "freq": "D",
    }


def cache_write(
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
