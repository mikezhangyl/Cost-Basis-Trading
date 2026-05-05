# Factor Dry Run Summary: 603799 January 2024 After Formula Pruning

This note records the first controlled live-data dry run after removing the most obvious duplicated factors.

It is a pipeline validation note, not a trading conclusion.

## What Changed

The factor production layer now removes formula-level duplicate or mirror factors before writing `factors.jsonl`.

Excluded factors:

```text
cyq_cgo_asof -> weighted_chip_cost_gap_asof
profit_ratio_asof -> loss_ratio_asof
profit_ratio_delta_20d -> loss_ratio_delta_20d
```

Plain-language meaning:

> We keep one representative from each obvious duplicate pair so later strategy research does not count the same evidence twice.

## Active Factor Set

The active factor count is now:

```text
10 factors
```

Active factors:

```text
chip_weighted_std_asof
concentration_width_70_asof
concentration_width_70_delta_20d
concentration_width_90_asof
dominant_peak_price_delta_20d
dominant_peak_strength_asof
loss_ratio_asof
loss_ratio_delta_20d
weighted_chip_cost_delta_20d
weighted_chip_cost_gap_asof
```

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

Rate-limit setting:

```text
TUSHARE_RATE_LIMIT_PER_MINUTE=450
```

## Generated Artifacts

Factor batch:

```text
docs/factor-batches/factor-batch-dryrun-603799-202401-pruned-live/
```

Factor run:

```text
docs/factor-runs/factor-run-factor-batch-dryrun-603799-202401-pruned-live-603799-SH/
```

Factor redundancy review:

```text
docs/factor-redundancy-reviews/factor-redundancy-review-dryrun-603799-202401-pruned-live/
```

Factor signal similarity review:

```text
docs/factor-signal-similarity-reviews/factor-signal-similarity-review-dryrun-603799-202401-pruned-live/
```

Generated run artifact directories are ignored by git. This note records the durable summary.

## Run Result

Batch result:

```text
status: completed
stock_count: 1
success_count: 1
failed_count: 0
summary_factor_count: 10
observation_count: 660
```

Factor run manifest:

```text
factor_date_count: 22
warmup_date_count: 20
dry_run: false
stock_outputs[0].ts_code: 603799.SH
retention_policy_ref: stocks/603799.SH/factor-retention-policy.json
```

Raw factor row count:

```text
factors.jsonl: 220 rows
```

Plain-language meaning:

> There are 22 output trading days. Each day now has 10 factor values, so the factor file has 220 rows.

## API Call Summary

Top-level API log:

```text
api-calls.jsonl: 3 lines
api-retry-events.jsonl: 0 lines
```

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
factor_pair_count: 45
```

The previous obvious formula-level pairs are no longer present because they were removed before factor evaluation.

Remaining `Needs Review` items include pairs such as:

```text
chip_weighted_std_asof vs concentration_width_70_asof
chip_weighted_std_asof vs weighted_chip_cost_gap_asof
concentration_width_70_asof vs concentration_width_90_asof
loss_ratio_asof vs weighted_chip_cost_gap_asof
```

Plain-language meaning:

> Some remaining factors still moved similarly inside this one stock during this one month. That is not enough to delete them. They should be checked across more stocks and a longer discovery period.

## Signal Similarity Result

The signal similarity review completed:

```text
instrument_count: 1
pair_similarity_count: 135
```

The report found:

```text
No cross-object trigger or behavior similarity candidates.
No instrument-specific high-overlap pairs.
```

## Interpretation

This dry run validates the pruned factor pipeline:

- Obvious duplicated factors were removed from generated factor rows.
- The retained factor set is traceable through `factor-retention-policy.json`.
- Live data fetch still completed successfully.
- ECC reviews passed.
- The one-stock one-month sample still should not drive final factor removal decisions beyond the formula-level pruning already applied.

This dry run does not validate a trading strategy.

## Next Step

Use the 10-factor active set for the next larger discovery run.

Recommended next expansion:

```text
multiple stocks
2024-01-01 through at least 2024-03-31
```

After that passes, expand toward the full discovery period:

```text
2024-01-01 through 2025-12-31
```
