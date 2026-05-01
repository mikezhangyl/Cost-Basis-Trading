# A-Share Chip Backfill Agent Design

## Status

Draft created on 2026-05-01. This is a design document only.

## Capability

Build a local, long-running ingestion agent that fetches Tushare `cyq_chips` data for all currently listed A-share stocks from a user-specified start date through the latest available trading day, stores the data durably, and can resume safely after process crashes, machine restarts, network failures, or Tushare rate limiting.

The first implementation covers A shares only. Hong Kong stocks and internal query APIs are explicitly out of scope for this phase.

## Fixed Scope And Constraints

- Source endpoint: Tushare `cyq_chips`.
- Market scope: currently listed A-share stocks only.
- Exclude delisted stocks.
- User's current Tushare tier: 5000 points.
- Documented `cyq_chips` limit: single request returns at most 2000 rows.
- Documented 5000-point `cyq_chips` quota: 20000 calls per day and 200 calls per minute.
- Data start: Tushare documents `cyq_chips` as available from 2018.
- Local deployment only for the first version.
- No internal read API in the first version.
- Never hardcode or log `TUSHARE_TOKEN`.

Reference:

- Tushare `cyq_chips`: https://tushare.pro/document/2?doc_id=294

## Architecture

```text
Planner
  -> sync active A-share symbols
  -> sync SSE trading calendar
  -> create missing backfill jobs

PostgreSQL control database
  -> symbols
  -> trade_calendar
  -> ingest_jobs
  -> job_attempts
  -> ingest_events
  -> rate_limit_state

Workers
  -> lease jobs with SKIP LOCKED
  -> acquire rate-limit tokens
  -> call Tushare
  -> validate response
  -> write idempotently to ClickHouse
  -> update job state

ClickHouse data database
  -> chip_distribution
  -> chip_distribution_ingest_versions
```

PostgreSQL acts as the mini Kafka-like mechanism for this local workload. It owns durable jobs, leases, retry timing, dead-letter records, and append-only events. This avoids running Kafka or Redpanda before there is a real multi-consumer streaming need.

ClickHouse owns the analytical chip-detail rows because historical A-share chip data can become very large and will later be queried by stock, date range, and factor scans.

## Job Granularity

The stored data is daily, but the fetch job should not always be one stock-day per request.

Use adaptive date-window jobs:

```text
endpoint = cyq_chips
ts_code = 600000.SH
start_date = 20240101
end_date = 20240112
```

Worker behavior:

1. Request `cyq_chips(ts_code, start_date, end_date)`.
2. If `row_count < 1800`, accept the window.
3. If `row_count >= 1800`, split the window into smaller jobs and do not mark the parent complete until child jobs succeed.
4. If a single trading day returns near the 2000-row limit, move it to dead-letter because the endpoint may have truncated data.

This keeps the output daily while reducing total API calls. With a one-day job model, all active A shares from 2018 onward could require many millions of calls. Adaptive windows should reduce the first backfill by an order of magnitude while staying below the documented row limit.

## PostgreSQL Control Schema

### `symbols`

```text
market text
ts_code text primary key
name text
exchange text
list_date date
list_status text
is_active boolean
source_endpoint text
updated_at timestamptz
```

Only `is_active = true` symbols are planned.

### `trade_calendar`

```text
market text
trade_date date
is_open boolean
updated_at timestamptz
primary key (market, trade_date)
```

Use the SSE calendar for A-share backfill planning unless a future requirement needs exchange-specific calendars.

### `ingest_jobs`

```text
job_id uuid primary key
endpoint text
market text
ts_code text
start_date date
end_date date
status text
priority int
attempt_count int
next_run_at timestamptz
lease_owner text null
lease_until timestamptz null
parent_job_id uuid null
input_hash text
last_error_code text null
last_error_message text null
created_at timestamptz
updated_at timestamptz
unique (endpoint, market, ts_code, start_date, end_date)
```

Allowed statuses:

- `pending`
- `leased`
- `succeeded`
- `split`
- `retryable_failed`
- `dead_letter`
- `skipped`

### `job_attempts`

```text
attempt_id uuid primary key
job_id uuid
attempt_no int
worker_id text
started_at timestamptz
finished_at timestamptz null
status text
request_params_hash text
row_count int null
error_code text null
error_message text null
```

### `ingest_events`

Append-only event log for audit and replay diagnostics:

```text
event_id bigserial primary key
job_id uuid null
event_type text
payload jsonb
created_at timestamptz
```

Events include `job_created`, `job_leased`, `job_split`, `tushare_called`, `rows_written`, `job_succeeded`, `job_failed`, `job_dead_lettered`, and `rate_limited`.

## ClickHouse Data Schema

### `chip_distribution`

Use `ReplacingMergeTree` so repeated ingestion can safely supersede older versions.

```sql
CREATE TABLE chip_distribution
(
    market LowCardinality(String),
    ts_code String,
    trade_date Date,
    price Float64,
    percent Float64,
    source LowCardinality(String),
    job_id UUID,
    ingest_version UInt64,
    fetched_at DateTime64(3, 'Asia/Shanghai')
)
ENGINE = ReplacingMergeTree(ingest_version)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (market, ts_code, trade_date, price);
```

Logical row identity:

```text
market + ts_code + trade_date + price
```

If the same row is fetched again, the newer `ingest_version` wins.

## Idempotency Rules

- Job creation is idempotent through the unique job key.
- Worker leasing is idempotent through `lease_until` and `FOR UPDATE SKIP LOCKED`.
- Tushare calls are not assumed idempotent, but their results are normalized before write.
- ClickHouse writes are idempotent at `(market, ts_code, trade_date, price)` through `ReplacingMergeTree`.
- A job is marked `succeeded` only after rows are written and post-write validation passes.
- Empty data for a listed stock on a valid trading day is not automatically success for recent dates; it should retry after the normal update window.

## Rate Limiting

Use a central token bucket in PostgreSQL:

```text
endpoint
minute_window_started_at
minute_calls_used
day_window_date
day_calls_used
minute_limit
day_limit
updated_at
```

Initial `cyq_chips` limits for 5000 points:

```text
minute_limit = 180
day_limit = 18000
```

These are intentionally below the documented 200/minute and 20000/day limits to leave margin for manual calls, retries, and clock drift.

If Tushare returns rate-limit errors, reduce the active minute limit temporarily and reschedule jobs with backoff instead of busy waiting.

## Failure Handling

Retryable:

- network timeout
- connection reset
- temporary Tushare server error
- rate limited

Non-retryable or pause-worthy:

- missing token
- no endpoint permission
- invalid symbol
- single-day response near the 2000-row limit

Backoff policy:

```text
attempt 1: immediate
attempt 2: +5 minutes
attempt 3: +30 minutes
attempt 4: +2 hours
attempt 5: +12 hours
attempt 6+: dead_letter
```

Rate-limit failures should use endpoint-level throttling in addition to job-level backoff.

## Planning Algorithm

Inputs:

```yaml
start_date: "20180101"
end_date: latest_available_trading_day
initial_window_trading_days: 10
market: CN_A
```

Steps:

1. Load active A-share symbols with `stock_basic`.
2. Load trading calendar with `trade_cal`.
3. Clamp each symbol's effective start date to `max(user_start_date, list_date, 20180101)`.
4. Resolve latest available trading day:
   - after 20:30 Asia/Shanghai on a trading day, try today's date;
   - before that, use the previous open trading day;
   - if today's job returns empty, reschedule instead of treating it as final.
5. For each symbol, partition trading days into initial windows of 10 trading days.
6. Insert missing jobs idempotently.

## Worker Algorithm

```text
loop:
  job = lease next eligible job
  if no job:
    sleep with jitter
    continue

  if cannot acquire rate token:
    release job with next_run_at
    continue

  call Tushare cyq_chips
  validate required columns: ts_code, trade_date, price, percent

  if row_count >= split_threshold and window has more than one trading day:
    create child jobs
    mark parent split
    continue

  write rows to ClickHouse with new ingest_version
  verify written row count for job window
  mark job succeeded
```

## Operations

First local deployment should use Docker Compose:

- `postgres`
- `clickhouse`
- `chip-backfill-planner`
- `chip-backfill-worker`

Recommended commands:

```text
plan-backfill --start-date 20180101 --window-days 10
run-worker --concurrency 4
reconcile-gaps --start-date 20180101
show-progress
```

Progress metrics:

- active symbols
- total planned jobs
- succeeded jobs
- split jobs
- retryable failures
- dead-letter jobs
- rows written
- calls used today
- estimated days remaining at current rate

## Non-Goals

- No Hong Kong stock ingestion in this phase.
- No internal query API in this phase.
- No trading execution.
- No strategy inference inside the ingestion worker.
- No fabricated or patched market data.
- No Kafka, Redpanda, or distributed queue unless a later deployment needs multiple machines or multiple independent consumers.

## Open Questions

- What default start date should the first production run use: `20180101` or a later research-specific date?
- Should daily price bars be ingested in the same system now, or should this phase stay strictly `cyq_chips` only?
- How much local disk space is available for ClickHouse data and backups?

## Handoff

This design is ready for an implementation plan. The next implementation phase should add Docker Compose services, database migrations, a planner command, a worker command, and tests for idempotent planning, job leasing, retry behavior, window splitting, and ClickHouse row identity.
