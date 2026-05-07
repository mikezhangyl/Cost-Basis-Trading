import time
import threading
from pathlib import Path

from app.data.cache_writer import CacheWriteMode, CacheWriter
from app.data.market_cache import CacheKey, CachePayloadKind, CacheWrite, MarketCacheStore


def cache_key(date_key: str = "20260415") -> CacheKey:
    return CacheKey(
        provider="tushare",
        endpoint="daily",
        instrument_id="600519.SH",
        date_key=date_key,
        date_key_role="trade_date",
        semantic_params={
            "schema_version": 1,
            "fields": ["ts_code", "trade_date", "close"],
            "price_adjustment": "none",
            "asset": "E",
            "freq": "D",
        },
    )


def cache_write(date_key: str = "20260415", close: float = 100.0) -> CacheWrite:
    key = cache_key(date_key)
    return CacheWrite(
        key=key,
        payload_kind=CachePayloadKind.ROWS,
        payload={"ts_code": key.instrument_id, "trade_date": date_key, "close": close},
        source_params={"ts_code": key.instrument_id, "trade_date": date_key},
        fetched_at="2026-05-06T01:02:03+00:00",
        provider_updated_at="2026-05-06T00:00:00+00:00",
        cache_schema_version=1,
    )


def test_sync_mode_writes_before_returning(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    writer = CacheWriter(store, mode=CacheWriteMode.SYNC)

    result = writer.write(cache_write())

    assert result.status == "written"
    assert store.count_versions() == 1
    assert store.count_pending_jobs() == 0


def test_sync_mode_applies_provider_corrections_by_default(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    writer = CacheWriter(store, mode=CacheWriteMode.SYNC)
    key = cache_key()

    writer.write(cache_write(close=100.0))
    result = writer.write(cache_write(close=101.0))
    current = store.read_many([key]).hits[key.identity]

    assert result.status == "written"
    assert current.payload["close"] == 101.0
    assert store.count_versions() == 2
    assert store.count_conflicts() == 1


def test_async_mode_durably_enqueues_before_flush(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    writer = CacheWriter(store, mode=CacheWriteMode.ASYNC)

    result = writer.write(cache_write())

    assert result.status == "enqueued"
    assert store.count_versions() == 0
    assert store.count_pending_jobs() == 1

    flushed = writer.flush()

    assert flushed.succeeded == 1
    assert store.count_versions() == 1
    assert store.count_pending_jobs() == 0


def test_async_auto_flush_materializes_enqueued_writes(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    writer = CacheWriter(store, mode=CacheWriteMode.ASYNC, auto_flush=True)

    result = writer.write(cache_write())

    assert result.status == "enqueued"
    for _ in range(50):
        if store.count_versions() == 1 and store.count_pending_jobs() == 0:
            break
        time.sleep(0.01)

    assert store.count_versions() == 1
    assert store.count_pending_jobs() == 0


def test_async_auto_flush_does_not_lose_job_enqueued_during_thread_exit(tmp_path: Path) -> None:
    class ExitGateStore(MarketCacheStore):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.exit_check_started = threading.Event()
            self.allow_exit = threading.Event()
            self.gated_once = False

        def count_pending_jobs(self) -> int:
            if threading.current_thread() is not threading.main_thread() and not self.gated_once:
                self.gated_once = True
                self.exit_check_started.set()
                self.allow_exit.wait(timeout=2)
            return super().count_pending_jobs()

    store = ExitGateStore(tmp_path / "market_data.sqlite3")
    writer = CacheWriter(store, mode=CacheWriteMode.ASYNC, auto_flush=True)
    writer.write(cache_write("20260415", 100.0))
    assert store.exit_check_started.wait(timeout=2)

    second_write = threading.Thread(target=lambda: writer.write(cache_write("20260416", 101.0)))
    second_write.start()
    time.sleep(0.01)
    store.allow_exit.set()
    second_write.join(timeout=2)

    for _ in range(50):
        if store.count_versions() == 2 and store.count_pending_jobs() == 0:
            break
        time.sleep(0.01)

    assert store.count_versions() == 2
    assert store.count_pending_jobs() == 0


def test_async_replay_preserves_materialized_metadata(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    writer = CacheWriter(store, mode=CacheWriteMode.ASYNC)
    write = cache_write()

    writer.write(write)
    writer.flush()
    entry = store.read_many([write.key]).hits[write.key.identity]

    assert entry.fetched_at == "2026-05-06T01:02:03+00:00"
    assert entry.provider_updated_at == "2026-05-06T00:00:00+00:00"
    assert entry.cache_schema_version == 1


def test_async_flush_does_not_mark_rejected_conflict_as_success(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    CacheWriter(store, mode=CacheWriteMode.SYNC).write(cache_write(close=100.0))
    writer = CacheWriter(store, mode=CacheWriteMode.ASYNC, allow_provider_correction=False)

    writer.write(cache_write(close=101.0))
    flushed = writer.flush()

    assert flushed.succeeded == 0
    assert flushed.failed == 1
    assert store.failed_jobs()[0]["status"] == "FAILED_PERMANENT"
    assert store.read_many([cache_key()]).hits[cache_key().identity].payload["close"] == 100.0


def test_async_flush_retries_transient_failures(tmp_path: Path, monkeypatch) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    writer = CacheWriter(store, mode=CacheWriteMode.ASYNC, max_attempts=2)
    original_upsert = store.upsert
    failures_remaining = {"count": 1}

    def fail_once(*args, **kwargs):
        if failures_remaining["count"] > 0:
            failures_remaining["count"] -= 1
            raise RuntimeError("database is locked")
        return original_upsert(*args, **kwargs)

    writer.write(cache_write())
    monkeypatch.setattr(store, "upsert", fail_once)

    result = writer.flush()

    assert result.succeeded == 1
    assert result.failed == 0
    assert store.count_versions() == 1
    assert store.count_pending_jobs() == 0


def test_read_only_and_disabled_modes_skip_writes(tmp_path: Path) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")

    read_only = CacheWriter(store, mode=CacheWriteMode.READ_ONLY)
    disabled = CacheWriter(store, mode=CacheWriteMode.DISABLED)

    assert read_only.write(cache_write("20260415")).status == "skipped_read_only"
    assert disabled.write(cache_write("20260416")).status == "skipped_disabled"
    assert store.count_versions() == 0
    assert store.count_pending_jobs() == 0


def test_writer_failure_is_logged_and_sanitized(tmp_path: Path, monkeypatch) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    writer = CacheWriter(store, mode=CacheWriteMode.ASYNC, max_attempts=1)

    def fail_upsert(*args, **kwargs):
        raise RuntimeError("failed token=abc123 secret: hidden")

    writer.write(cache_write())
    monkeypatch.setattr(store, "upsert", fail_upsert)

    result = writer.flush()
    failed_job = store.failed_jobs()[0]

    assert result.failed == 1
    assert "abc123" not in failed_job["last_error"]
    assert "hidden" not in failed_job["last_error"]
    assert "[REDACTED]" in failed_job["last_error"]
    assert store.count_versions() == 0


def test_writer_redacts_common_authorization_secret_formats(tmp_path: Path, monkeypatch) -> None:
    store = MarketCacheStore(tmp_path / "market_data.sqlite3")
    writer = CacheWriter(store, mode=CacheWriteMode.ASYNC, max_attempts=1)

    def fail_upsert(*args, **kwargs):
        raise RuntimeError(
            "TUSHARE_TOKEN=ts-secret OPENAI_API_KEY=openai-secret "
            "Authorization: Bearer bearer-secret"
        )

    writer.write(cache_write())
    monkeypatch.setattr(store, "upsert", fail_upsert)
    writer.flush()
    error = store.failed_jobs()[0]["last_error"]

    assert "ts-secret" not in error
    assert "openai-secret" not in error
    assert "bearer-secret" not in error
    assert "[REDACTED]" in error
