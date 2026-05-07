# Execution Plan: Local Market Data Cache

## Goal

Build a durable local read-through cache for provider market data so repeated factor experiments reuse local data, fetch only missing date slices, and do not block factor computation on slow cache writes.

Design source:

- [../../design/local-market-data-cache.md](../../design/local-market-data-cache.md)

## Scope

In scope:

- SQLite-backed local market-data cache,
- cache key contract for provider, endpoint, instrument, date, and semantic params,
- date-key roles for trading-day rows, calendar rows, and metadata snapshots,
- raw daily price bars,
- price adjustment factors,
- adjusted price derivation policy,
- chip distribution rows,
- trading calendar rows,
- async write-behind queue,
- cache event logging into factor-run and research-run artifacts,
- tests for partial hits, async write failure, checksum conflict, and price adjustment semantics.

Out of scope:

- caching factor values,
- Redis/Kafka/external database deployment,
- cloud sync,
- multi-user permission model,
- UI cache management,
- automatic provider arbitration across vendors.

## Implementation Phases

### Phase 1: Cache Store Foundation

Add:

```text
backend/app/data/market_cache.py
backend/tests/test_market_cache.py
```

Required behavior:

- initialize SQLite schema idempotently,
- enable WAL mode,
- upsert cache entries by full semantic key,
- read date ranges by semantic key set,
- detect checksum conflicts,
- record provider-correction conflicts before superseding entries,
- classify missing keys,
- sanitize errors before writing cache job failures.

Acceptance tests:

- same key and checksum upserts idempotently,
- same key and different checksum records a conflict instead of silently overwriting,
- expected provider correction records old and new checksums before superseding,
- provider correction keeps old and new immutable versions and updates only the current pointer,
- unexpected checksum conflict leaves current pointer unchanged,
- range read returns hits and misses separately,
- malformed payload does not return as a cache hit,
- `stock_basic` latest-snapshot cache does not masquerade as historical metadata,
- endpoint contracts include required semantic params for `daily`, `adj_factor`, `cyq_chips`, `trade_cal`, `stock_basic`, and derived adjusted bars,
- `PROVISIONAL_NO_DATA` and `PERMANENT_NO_DATA` are recognized only for endpoint-approved authoritative empty results,
- provisional no-data entries become stale according to TTL and recent-refresh policy,
- permanent no-data entries are allowed only for provable permanent absence,
- cache database path is created under `data/market-cache/`.

### Phase 2: Async Write Queue

Add:

```text
backend/app/data/cache_writer.py
backend/tests/test_cache_writer.py
```

Required behavior:

- support `sync`, `async`, `read_only`, and `disabled` modes,
- enqueue fetched payloads without waiting for full maintenance work,
- durably enqueue fetched payloads in the job table or outbox before returning,
- run one background writer drain per process for default async application paths,
- retry retryable SQLite errors with bounded attempts,
- record permanent failures without interrupting factor calculation,
- expose `flush()` for tests and CLI shutdown.

Acceptance tests:

- async mode returns before writer flush,
- async mode does not return before durable job-table insert or outbox append,
- async replay uses persisted `fetched_at`, `provider_updated_at`, and `cache_schema_version` instead of inventing new values,
- sync write and async replay of the same job materialize equivalent cache versions,
- default async writer auto-flush materializes pending jobs after returning,
- process restart drains durable pending jobs or outbox lines when a runner/CLI startup hook is added,
- sync mode writes before returning,
- read-only mode skips writes,
- disabled mode skips reads and writes,
- writer failure is logged and does not alter returned market data.

### Phase 3: Cached Client Wrapper

Add:

```text
backend/app/data/cached_market_data_client.py
backend/tests/test_cached_market_data_client.py
```

Required behavior:

- wrap `TushareMarketDataClient`,
- implement existing market-data-client methods,
- resolve expected trading dates,
- use cached rows for hits,
- fetch only missing dates for `cyq_chips`,
- fetch only missing or stale dates for `daily`,
- cache `adj_factor` separately,
- merge cached and fetched rows deterministically.

Acceptance tests:

- full hit makes no provider call,
- partial hit fetches only missing trading dates,
- non-dated metadata endpoints use snapshot keys and TTL instead of fake trade dates,
- authorized permanent no-data markers prevent repeated provider calls for confirmed not-yet-listed or delisted dates,
- provisional no-data markers prevent repeated calls only until TTL or recent-refresh policy requires a refresh,
- empty provider response is not cached as successful chip data unless endpoint policy allows an explicit empty marker,
- returned rows are sorted by `trade_date`,
- provider errors are raised normally and do not create cache entries.

### Phase 4: Price Adjustment Semantics

Add domain support for adjustment factors and explicit price mode.

Required behavior:

- introduce `PriceSemantics` or equivalent explicit request object,
- require `data_horizon` for all price requests,
- require `adjustment_anchor_date` for qfq/hfq derived prices,
- raw price bars remain available for chip-price comparison,
- adjusted return series can be derived from raw daily plus adjustment factors,
- qfq-derived prices include `adjustment_anchor_date` if cached as derived payloads,
- backtest return calculation can opt into adjusted prices without changing chip factor comparisons.

Acceptance tests:

- raw close drop caused by adjustment factor does not create false negative adjusted return,
- raw chip comparison still uses unadjusted close,
- qfq/hfq/cache keys do not collide,
- raw and return-adjusted semantics do not require qfq/hfq anchor dates,
- changing anchor date changes qfq semantic key,
- historical qfq/hfq requests with anchor date after the declared data horizon are rejected,
- successful qfq/hfq/return_adjusted bar requests do not silently return raw bars before derived bar materialization is implemented,
- default backtest return calculation uses raw close plus adjustment-factor ratio rather than qfq anchored to latest date.

### Phase 5: Application Integration

Modify:

```text
backend/app/data/market_data_client_factory.py
backend/app/api/routes.py
backend/app/services/backtest_service.py
backend/app/services/research_run_service.py
```

Required behavior:

- `build_market_data_client()` returns a cached wrapper when cache is enabled,
- API routes construct scan, backtest, and research-run services through the factory,
- scan uses cached raw price and chip data through the existing market-data-client boundary,
- backtest period returns use raw close plus adjustment-factor ratios when the client supports `adj_factor`,
- raw close remains available for chip-price comparisons and signal feature construction,
- research runs keep existing `api-calls.jsonl` service-level logs,
- research runs add `cache-events.jsonl` and a cache-event summary to `run-manifest.json`,
- cache disabled mode preserves direct provider behavior.

Acceptance tests:

- cache-enabled factory wraps a provider,
- cache-disabled factory returns the provider unchanged,
- scan API construction remains backward compatible,
- backtest uses adjustment factors for ex-right/dividend-like forward returns,
- research-run artifact logs include cache summary,
- repeated cached client calls over same fake data avoid repeated provider calls.

### Phase 6: Factor Runner Integration

Modify:

```text
scripts/chip_factor_runner.py
```

Required behavior:

- `_build_tushare_client()` or equivalent runner construction uses `build_market_data_client()`,
- existing immutable factor-run artifacts remain unchanged unless cache-event logging is explicitly added,
- `api-calls.jsonl` continues to record service-level calls,
- cache event summaries appear in runner artifacts when the runner has an artifact surface,
- preserve `--dry-run`,
- preserve immutable factor-run output behavior.

Acceptance tests:

- dry run does not initialize external provider cache,
- live runner with fake cached client writes the same artifact shape,
- cache hit/miss summary appears in worker or cache event logs,
- repeated run over same fake data avoids repeated provider calls.

Status: implemented in the runner integration branch. The runner now uses `build_market_data_client()` for live runs, writes `cache-events.jsonl`, records `cache_event_summary` and `cache_flush_summary`, surfaces cache persistence failures as `completed_with_cache_warnings`, and has a default async integration test covering repeated runs over the same SQLite cache.

Live smoke: `000001.SZ` from 2024-04-15 to 2024-04-17 was run twice against the same local cache root. The first run fetched `trade_cal`, `daily`, and `cyq_chips`; the second run reported `miss_count=0`, `fetched_date_count=0`, and `cache_flush_summary.failed=0`.

## Configuration

Add to `.env.example`:

```text
MARKET_DATA_CACHE_ENABLED=true
MARKET_DATA_CACHE_PATH=data/market-cache/market_data.sqlite3
MARKET_DATA_CACHE_WRITE_MODE=async
MARKET_DATA_CACHE_RECENT_REFRESH_DAYS=10
MARKET_DATA_CACHE_PROVISIONAL_NO_DATA_TTL_SECONDS=86400
```

Defaults should be conservative:

- cache enabled for local scripts,
- async writes for factor production,
- sync writes for tests,
- adjusted returns for backtests whenever the market-data client exposes `adj_factor`,
- raw prices for chip comparisons.

## Correctness Gate

Before trusting live provider runs:

- `pytest -v backend/tests/test_market_cache.py`
- `pytest -v backend/tests/test_cache_writer.py`
- `pytest -v backend/tests/test_cached_market_data_client.py`
- `pytest -v backend/tests/test_tushare_client.py`
- `pytest -v backend/tests/test_backtest_service.py`
- `pytest -v backend/tests/test_research_run_service.py`
- `pytest -v backend/tests/test_market_data_client_factory.py`
- `python scripts/ecc_quality_workflow.py quality-gate`

## Rollout

1. Implement cache store behind tests.
2. Implement cached client wrapper with fake provider tests.
3. Wire application services through the market-data-client factory.
4. Wire adjusted return calculation into backtests.
5. Add cache-event artifact logging to research runs.
6. Wire factor runner when the runner script is available in this worktree.
7. Run a tiny live smoke test on one stock and short date range.
8. Inspect cache DB and artifact cache-event logs.
9. Add manual CLI inspection commands only after the core cache path is stable.

Status: rollout steps 1-9 are implemented in the runner integration branch. Use:

```text
python scripts/market_cache_inspect.py summary --cache-path data/market-cache/market_data.sqlite3
python scripts/market_cache_inspect.py entries --cache-path data/market-cache/market_data.sqlite3 --endpoint daily --instrument-id 000001.SZ
python scripts/market_cache_inspect.py jobs --cache-path data/market-cache/market_data.sqlite3 --status FAILED_PERMANENT
```

The inspection CLI is intentionally read-only and omits cached payload/source-param JSON from output.

## Acceptance Criteria

- Repeating the same factor run date range does not call the provider for already cached daily/chip dates.
- Extending a date range fetches only new or refresh-required dates.
- Cache writes can fail without corrupting factor output.
- Raw and adjusted price semantics cannot collide in cache keys.
- Backtest returns are not distorted by dividend/ex-right raw price jumps.
- Cache events make hit/miss/fetch/write behavior visible in artifacts.
- No secrets are written to cache, logs, or artifacts.
