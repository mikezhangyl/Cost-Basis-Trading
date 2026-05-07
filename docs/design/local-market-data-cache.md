# Local Market Data Cache Capability

## Status

Implemented in the `codex/local-market-data-cache` worktree on 2026-05-06. The first implementation includes the SQLite cache store, async write queue with background drain, Tushare cached client wrapper, price-adjustment handling, API-service factory integration, adjusted backtest returns, and research-run cache-event artifacts.

This document defines the local persistent cache layer for market data used by scans, backtests, research runs, and chip-factor production.

## Capability

The local researcher can repeatedly run factor experiments without repeatedly paying external API cost for the same market data. The system reads reusable raw market data from a durable local cache, fetches only missing slices from providers such as Tushare or 2Share, returns data to factor computation without waiting for slow cache maintenance, and incrementally grows the local data store as experiments discover new dates or instruments.

## Constraints

Fixed rules:

- The cache stores provider data and normalized market data, not factor results.
- Strategy modules and factor formulas must not call external providers directly.
- Cache lookup and provider fetch belong behind the market-data-client boundary.
- Cache keys must include the dimensions that change the meaning of the payload.
- Price data must preserve adjustment semantics. Raw prices, adjustment factors, and adjusted derived prices are different data products.
- A cache hit must never change the returned domain model compared with a provider fetch for the same semantic key.
- Cache writes must not block factor computation longer than bounded local enqueue work.
- Provider failures must not poison the cache.
- Partial cache hits are first-class: if 97 of 100 trading days are cached, only the 3 missing days should be fetched.
- Cached data is mutable only through idempotent upsert for the same semantic key and checksum-aware conflict detection.
- Local cache files and SQLite databases remain uncommitted.

Trust boundaries:

- External provider responses are untrusted input until normalized and schema-checked.
- Provider tokens and secrets must never be written to cache payloads, logs, or error messages.
- Cached payloads may be stale or provider-corrected; freshness policy must be explicit per endpoint.
- Local cache is an optimization and reproducibility aid, not the immutable evidence package. Factor-run artifacts remain the immutable audit output.

## Data Ownership

The cache owns reusable source data:

- trading calendars,
- stock metadata,
- daily raw price bars,
- price adjustment factors,
- chip distribution rows,
- future provider rows from 2Share or other data vendors.

The factor-run artifact owns experiment evidence:

- factor-run config,
- API/cache events,
- retry events,
- daily snapshots,
- factor values,
- factor quality summaries,
- checksums of generated artifacts.

## Cache Key Contract

Every cached entry belongs to a semantic key:

```text
provider + endpoint + instrument_id + date_key + semantic_params_hash
```

Where:

- `provider` is the external source, such as `tushare` or `2share`.
- `endpoint` is the source interface or normalized data family, such as `daily`, `adj_factor`, `cyq_chips`, or `trade_cal`.
- `instrument_id` is a normalized identifier, such as `600519.SH`, `SSE`, or `__market__` for provider-wide data.
- `date_key` is the partition date or snapshot key for this endpoint.
- `semantic_params_hash` captures meaning-changing parameters not already represented by the other columns.

Date key roles:

| Endpoint shape | `date_key` example | `date_key_role` |
| --- | --- | --- |
| trading-day stock data | `20260415` | `trade_date` |
| exchange calendar row | `20260415` | `calendar_date` |
| current metadata snapshot | `__latest__` | `latest_snapshot` |
| explicit metadata snapshot | `20260505` | `snapshot_date` |

For non-price daily data, the hash may represent:

```json
{
  "schema_version": 1,
  "fields": ["ts_code", "trade_date", "price", "percent"]
}
```

For price data, the hash must represent price semantics. Examples:

```json
{
  "schema_version": 1,
  "price_adjustment": "none",
  "asset": "E",
  "freq": "D"
}
```

```json
{
  "schema_version": 1,
  "price_adjustment": "qfq",
  "adjustment_anchor_date": "20260505",
  "asset": "E",
  "freq": "D"
}
```

## Price Adjustment Policy

The cache must avoid mixing unadjusted, forward-adjusted, and backward-adjusted prices.

Canonical storage:

- Store raw `daily` bars as provider source data.
- Store `adj_factor` rows as provider source data.
- Derive adjusted prices locally when the caller asks for adjusted series.

Default usage policy:

- Chip cost comparison uses raw unadjusted prices, because Tushare `cyq_chips.price` and current market close must be compared in the same transaction-price scale.
- Return and backtest calculations use adjusted price series or adjustment-factor-derived returns, because dividends, splits, and ex-right/ex-dividend events create false raw close jumps.
- Trend factors must declare their requested price mode: `raw`, `qfq`, `hfq`, or `return_adjusted`.

Forward-adjusted prices are anchor-sensitive. The key must include `adjustment_anchor_date`, or the system should avoid caching derived qfq price rows and cache only raw bars plus adjustment factors.

Future-data rule:

- Historical factor extraction and backtests must not default qfq or hfq anchors to "today" or the provider's latest available trading day.
- Historical return calculations should prefer `return_adjusted`, computed from raw close and adjustment-factor ratios inside the requested observation window.
- If a caller explicitly asks for qfq or hfq derived bars, it must pass `adjustment_anchor_date`.
- For historical runs, `adjustment_anchor_date` must be less than or equal to the request's data horizon, such as `signal_date`, `observation_date`, or `request_end_date`.
- Cache lookup must reject derived adjusted-price keys whose anchor date is later than the caller's declared horizon.

Recommended adjusted return formula:

```text
adjusted_return(start_date, end_date) =
  (raw_close_end * adj_factor_end) / (raw_close_start * adj_factor_start) - 1
```

This avoids storing qfq rows for ordinary backtest scoring and keeps the calculation anchored to the observed window.

## Storage Model

Use local SQLite first:

```text
data/market-cache/market_data.sqlite3
```

Required tables:

`market_cache_current_entries` is the read-optimized current pointer table. It contains one current version per semantic key.

```sql
CREATE TABLE market_cache_current_entries (
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  date_key TEXT NOT NULL,
  date_key_role TEXT NOT NULL,
  semantic_params_hash TEXT NOT NULL,
  current_version_id TEXT NOT NULL,
  current_payload_checksum TEXT NOT NULL,
  current_fetched_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (
    provider,
    endpoint,
    instrument_id,
    date_key,
    semantic_params_hash
  )
);
```

`market_cache_entry_versions` stores immutable payload versions. Provider corrections create a new version instead of overwriting the old payload.

```sql
CREATE TABLE market_cache_entry_versions (
  version_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  date_key TEXT NOT NULL,
  date_key_role TEXT NOT NULL,
  semantic_params_hash TEXT NOT NULL,
  semantic_params_json TEXT NOT NULL,
  payload_kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_checksum TEXT NOT NULL,
  source_params_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  provider_updated_at TEXT,
  cache_schema_version INTEGER NOT NULL,
  supersedes_version_id TEXT,
  superseded_at TEXT,
  created_at TEXT NOT NULL
);
```

```sql
CREATE TABLE cache_write_jobs (
  job_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  date_key TEXT NOT NULL,
  date_key_role TEXT NOT NULL,
  semantic_params_hash TEXT NOT NULL,
  payload_checksum TEXT NOT NULL,
  payload_kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  semantic_params_json TEXT NOT NULL,
  source_params_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  provider_updated_at TEXT,
  cache_schema_version INTEGER NOT NULL,
  attempt_count INTEGER NOT NULL,
  next_attempt_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

```sql
CREATE TABLE market_cache_conflicts (
  conflict_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  instrument_id TEXT NOT NULL,
  date_key TEXT NOT NULL,
  semantic_params_hash TEXT NOT NULL,
  previous_payload_checksum TEXT NOT NULL,
  incoming_payload_checksum TEXT NOT NULL,
  previous_version_id TEXT NOT NULL,
  incoming_version_id TEXT,
  resolution TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

Use SQLite WAL mode and one writer worker per process. Readers may run concurrently.

Endpoint contract and payload shape policy:

| Endpoint | `instrument_id` | `date_key_role` | Required semantic params | Payload shape | Empty payload policy |
| --- | --- | --- | --- | --- | --- |
| `cyq_chips` | `ts_code` | `trade_date` | `schema_version`, `fields`, provider endpoint version when known | one entry per stock/trade_date containing all chip buckets for that date | use `PROVISIONAL_NO_DATA` for recent listed-but-no-row dates; use `PERMANENT_NO_DATA` only when permanent absence is provable |
| `daily` | `ts_code` | `trade_date` | `schema_version`, `fields`, `price_adjustment=none`, `asset`, `freq` | one entry per stock/trade_date containing one raw daily bar | use `PROVISIONAL_NO_DATA` for recent suspension/provider-latency dates; use `PERMANENT_NO_DATA` before listing or after delisting |
| `adj_factor` | `ts_code` | `trade_date` | `schema_version`, `fields`, `asset` | one entry per stock/trade_date containing one adjustment factor | mirror the corresponding daily bar no-data classification |
| `trade_cal` | exchange such as `SSE` | `calendar_date` | `schema_version`, `exchange`, `fields`; do not include `is_open` in the key if both open and closed rows are stored | one entry per exchange/calendar date | cache open and closed days when intentionally requested |
| `stock_basic` | `ts_code` or query-scope id | `latest_snapshot` or `snapshot_date` | `schema_version`, `fields`, query scope, exchange/list_status filters | one latest or snapshot metadata entry per stock or query scope | empty latest queries use `PROVISIONAL_NO_DATA` with TTL; explicit historical snapshots may use `PERMANENT_NO_DATA` only when proven |
| derived `qfq`/`hfq` bars | `ts_code` | `trade_date` | `schema_version`, `price_adjustment`, `adjustment_anchor_date`, `asset`, `freq`, raw price checksum, adj-factor checksum | one derived bar | disabled by default; prefer deriving on demand |

`payload_kind` must be one of:

```text
ROWS
PROVISIONAL_NO_DATA
PERMANENT_NO_DATA
```

`PROVISIONAL_NO_DATA` and `PERMANENT_NO_DATA` are negative cache entries, not errors. They may be returned as cache hits only when the endpoint policy permits authoritative no-data markers. Provider transport errors, permission failures, rate limits, malformed payloads, and timeout responses must never become a no-data payload.

Negative cache freshness:

- `PROVISIONAL_NO_DATA` is for recent dates, provider-latency windows, and metadata snapshots. It must include a TTL through endpoint policy metadata and becomes stale when that TTL expires. This prevents tight retry loops while still refreshing recent negative cache entries.
- `PERMANENT_NO_DATA` is only for provably permanent empty cases, such as dates before listing, dates after delisting, intentionally cached closed calendar days, or query scopes confirmed to have no matching instrument.
- `PERMANENT_NO_DATA` may bypass repeated provider calls until manual invalidation or schema/provider-version changes.
- `PROVISIONAL_NO_DATA` must be refreshed before it can suppress a provider call outside its TTL.
- Read-through classification must treat stale no-data entries as misses or refresh-required keys, not as stable hits.

Optional outbox:

```text
data/market-cache/outbox/cache-writes-YYYYMMDD.jsonl
```

The outbox is a crash-recovery fallback for async writes. The caller may return after the fetched payload is durably enqueued in either `cache_write_jobs` or the append-only outbox. Pure in-memory enqueue is not sufficient for provider-fetched payloads because a process crash would lose expensive data and create unnecessary future provider calls.

Every job or outbox line must contain the complete materialized version payload:

- semantic key fields,
- `date_key_role`,
- `semantic_params_json`,
- `payload_kind`,
- `payload_json`,
- `payload_checksum`,
- `source_params_json`,
- `fetched_at`,
- `provider_updated_at`,
- `cache_schema_version`.

The async worker must not invent these values during replay. Sync writes and async replay of the same fetched payload must create equivalent `market_cache_entry_versions` rows. In local application paths, async mode durably enqueues first and then uses a background drain to materialize pending jobs without blocking the request on SQLite upsert completion.

## Read-Through Flow

For a range request:

1. Normalize stock code, date range, endpoint, and price mode.
2. Resolve expected market dates.
3. Build semantic keys for each expected date.
4. Read cached entries.
5. Validate checksums and schema version.
6. Classify missing or stale keys.
7. Fetch only missing or refresh-required keys from the provider.
8. Normalize provider rows into domain payloads.
9. Enqueue cache writes for fetched rows.
10. Merge cached and fetched rows.
11. Return sorted domain models to the caller.

Cache events should distinguish:

```text
CACHE_HIT
CACHE_MISS
CACHE_PARTIAL_HIT
CACHE_STALE_REFRESH
PROVIDER_FETCH
ASYNC_WRITE_ENQUEUED
ASYNC_WRITE_SUCCEEDED
ASYNC_WRITE_FAILED_RETRYABLE
ASYNC_WRITE_FAILED_PERMANENT
```

## Async Write Policy

Default mode:

```text
read-through + async write-behind
```

The caller may wait only for durable enqueue:

- SQLite job-table insert, or
- outbox append if the job table is unavailable.

The caller must not wait for:

- full SQLite upsert of large payloads,
- retry loops,
- compaction,
- vacuum,
- cache health scans.

The in-memory queue is only a wake-up mechanism for the writer. It must reference durable jobs instead of owning the only copy of fetched payloads.

Configurable modes:

```text
sync      - used in tests and debugging
async     - default for local factor runs
disabled  - bypass cache completely
read_only - use cache but do not write fetched rows
```

## Freshness And Corrections

Endpoint freshness policy:

| Endpoint | Default freshness | Refresh rule |
| --- | --- | --- |
| `trade_cal` | long-lived | refresh manually or when date range reaches future dates |
| `stock_basic` | daily | refresh after configured TTL |
| `daily` raw bars | stable after market close, but provider corrections possible | refresh recent N trading days |
| `adj_factor` | can change after corporate actions | refresh recent N trading days and around corporate action windows |
| `cyq_chips` | daily provider estimate | refresh recent N trading days; never forward-fill missing chip rows |

The first implementation should use conservative recent-window refresh:

```text
MARKET_CACHE_RECENT_REFRESH_DAYS=10
```

Older data is treated as stable unless explicitly refreshed. In the first implementation, `ROWS` entries inside the recent refresh window are stale when they were fetched before the current date; entries fetched on the current date remain fresh.

Provider correction policy:

- If a refresh returns the same semantic key with the same checksum, treat it as idempotent.
- If a refresh returns the same semantic key with a different checksum, record `market_cache_conflicts`.
- For endpoints where provider corrections are expected, write a new immutable version, record previous and incoming checksums, mark the previous version's `superseded_at`, and update `market_cache_current_entries` to the new version.
- For unexpected conflicts, write the incoming version only if configured for quarantine, keep the current pointer unchanged, mark the write as failed-permanent or quarantined, and require manual review or an explicit refresh command.
- Factor-run artifacts that already used an old cache version remain immutable. Later provider corrections affect only future runs unless a user intentionally reruns the experiment.

## Interface Contract

Introduce a wrapper client:

```python
class PriceSemantics:
    price_mode: Literal["raw", "return_adjusted", "qfq", "hfq"]
    data_horizon: str
    adjustment_anchor_date: str | None = None


class CachedMarketDataClient:
    def __init__(self, provider_client, cache_store, write_mode="async"):
        ...

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        ...

    def get_daily_prices(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        semantics: PriceSemantics | None = None,
    ) -> list[DailyPriceBar]:
        ...

    def calculate_adjusted_return(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        data_horizon: str,
    ) -> float:
        ...

    def get_adjustment_factors(self, ts_code: str, start_date: str, end_date: str) -> list[AdjustmentFactor]:
        ...
```

`PriceSemantics` validation:

| `price_mode` | Required fields | Rejected fields or states | Intended use |
| --- | --- | --- | --- |
| `raw` | `data_horizon` | none | chip-price comparison and raw market display |
| `return_adjusted` | `data_horizon` | `adjustment_anchor_date` is ignored or rejected | historical returns using raw close and adjustment-factor ratios |
| `qfq` | `data_horizon`, `adjustment_anchor_date` | `adjustment_anchor_date > data_horizon` | explicit derived price display only; not materialized by the first implementation |
| `hfq` | `data_horizon`, `adjustment_anchor_date` | `adjustment_anchor_date > data_horizon` | explicit derived price display only; not materialized by the first implementation |

The wrapper must construct a default `PriceSemantics(price_mode="raw", data_horizon=end_date)` only for backward-compatible callers. Backtest and factor-production code must pass explicit semantics:

- chip snapshot close: `raw`, `data_horizon=factor_date` or request end date,
- backtest return: `return_adjusted`, `data_horizon=observation_date`,
- explicit qfq/hfq chart display: caller-provided anchor with `anchor <= data_horizon`.

Backward compatibility:

- Existing services may continue calling `get_daily_prices(ts_code, start_date, end_date)` and receive raw prices.
- The first implementation rejects successful `qfq`, `hfq`, and `return_adjusted` bar materialization requests instead of silently returning raw bars.
- Backtest and factor-production runners should be updated to request adjusted prices for return calculations and raw prices for chip-price comparisons.

## Observability

Every factor run or research run should record cache behavior in its artifact logs:

```json
{
  "timestamp": "2026-05-05T00:00:00Z",
  "source": "market_data_cache",
  "endpoint": "daily",
  "ts_code": "600519.SH",
  "start_date": "20260101",
  "end_date": "20260430",
  "cache_hit_count": 78,
  "cache_miss_count": 3,
  "provider_fetch_count": 3,
  "write_mode": "async"
}
```

Do not log provider tokens, raw secret-bearing errors, or full payloads in run artifacts.

## Non-Goals

This cache layer does not:

- cache factor outputs,
- decide trading actions,
- replace immutable factor-run artifacts,
- provide multi-user access control,
- synchronize to cloud storage,
- solve distributed cache consistency,
- guarantee provider data is economically correct,
- introduce Kafka, Redis, or an external database in the first implementation.

## Open Questions

- Which provider will be the first non-Tushare implementation: 2Share or another data vendor?
- Should `DailyPriceBar` grow explicit `price_mode` and `adjustment_anchor_date` fields, or should adjusted prices use a separate model?
- How many recent trading days should be refreshed by default for each endpoint?
- Should manual cache invalidation be exposed as a CLI command in the first implementation?

Resolved in the first implementation:

- Backtests compute adjusted returns from raw close plus adjustment-factor ratios, not from latest-date-anchored qfq prices.

## Handoff

Implemented pieces:

1. Cache store schema, immutable versions, current pointers, conflict records, recent-row refresh, read checksum/schema validation, and durable async write jobs with background drain.
2. Cached client wrapper with partial-hit fetches, trading-calendar caching, provisional no-data markers, adjustment-factor caching, and cache events.
3. Price adjustment tests around dividend/ex-right-like raw close drops.
4. Scan, backtest, and research-run construction through `build_market_data_client()`.
5. Research-run `cache-events.jsonl` and run-manifest cache-event summaries.
6. Factor runner construction through `build_market_data_client()`, with runner-level `cache-events.jsonl`, `cache_event_summary`, and `cache_flush_summary`.

Remaining pieces:

1. Run a tiny live provider smoke test from the runner integration branch and inspect the generated SQLite cache.
2. Add manual cache-inspection CLI commands after the core path has live-run evidence.
