# Factor Dry Run Summary: 603799 January 2024

This note records the first controlled live-data dry run for stock `603799.SH`.

It is a pipeline validation note, not a trading conclusion.

## Scope

Input stock:

```text
603799
```

Normalized stock:

```text
603799.SH
```

Factor output period:

```text
2024-01-01 through 2024-01-31
```

Forward-return windows:

```text
N+1
N+3
N+5
```

Rate-limit setting used for the live run:

```text
TUSHARE_RATE_LIMIT_PER_MINUTE=450
```

The code default is `500` calls per minute. This run used `450` as a conservative setting.

## Generated Artifacts

Factor batch:

```text
docs/factor-batches/factor-batch-dryrun-603799-202401-live/
```

Factor run:

```text
docs/factor-runs/factor-run-factor-batch-dryrun-603799-202401-live-603799-SH/
```

Factor redundancy review:

```text
docs/factor-redundancy-reviews/factor-redundancy-review-dryrun-603799-202401-live/
```

Factor signal similarity review:

```text
docs/factor-signal-similarity-reviews/factor-signal-similarity-review-dryrun-603799-202401-live/
```

Generated run artifact directories are ignored by git. This note records the durable summary.

## Run Result

Batch result:

```text
status: completed
stock_count: 1
success_count: 1
failed_count: 0
observation_count: 858
```

Factor run manifest:

```text
factor_date_count: 22
warmup_date_count: 20
dry_run: false
stock_outputs[0].ts_code: 603799.SH
```

Plain-language meaning:

> The script fetched enough warmup history for 20-day factors, but only generated official factor outputs for the requested January 2024 window.

## API Call Summary

Top-level API log:

```text
api-calls.jsonl: 3 lines
api-retry-events.jsonl: 0 lines
```

Logged top-level calls:

```text
trade_cal: ok, row_count=42
cyq_chips: ok, row_count=9072
daily: ok, row_count=42
```

Important implementation detail:

> The top-level `cyq_chips` log is one logical operation, but internally the Tushare client still queries chip details one trading day at a time. The new rate limiter applies inside each actual Tushare call attempt.

No retries were needed in this run.

## ECC Review Result

Factor batch ECC review:

```text
status: passed
findings_count: 0
```

Factor redundancy ECC review:

```text
status: passed
findings_count: 0
```

## Redundancy Review Result

The redundancy review completed:

```text
instrument_count: 1
factor_pair_count: 78
```

Because this dry run contains only one stock and one month, the report did not produce high-confidence cross-object redundancy candidates.

It produced many `Needs Review` items with `1/1 strong relationships`.

Plain-language meaning:

> Some factors moved very similarly inside this one stock during this one month, but that is not enough evidence to permanently remove factors. It only tells us which pairs to watch when we expand to more stocks and a longer discovery period.

## Signal Similarity Result

The signal similarity review completed:

```text
instrument_count: 1
pair_similarity_count: 234
```

The report found:

```text
No cross-object trigger or behavior similarity candidates.
No instrument-specific high-overlap pairs.
```

Plain-language meaning:

> In this small one-month sample, the trigger-date similarity check did not find strong same-day trigger overlap. That does not prove the factors are independent. The sample is too small.

## Interpretation

This dry run validates the pipeline mechanics:

- Bare code `603799` was normalized to `603799.SH`.
- Live Tushare data was fetched successfully.
- API calls were logged.
- Retry log was created and remained empty because no retry was needed.
- Factor artifacts include checksums.
- Factor batch ECC review passed.
- Factor redundancy review passed ECC review.
- Signal similarity review completed.

This dry run does not validate a trading strategy.

Reasons:

- It uses only one stock.
- It uses only one month.
- One-stock redundancy evidence cannot produce a cross-stock retention decision.
- The output is suitable for pipeline verification, not factor promotion.

## Next Step

Use this dry run as the template for a larger discovery run:

```text
2024-01-01 through 2025-12-31
```

Before expanding, decide the initial stock universe and whether to use the conservative `450` calls/minute limit or the full `500` calls/minute limit.
