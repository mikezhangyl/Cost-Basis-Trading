# Existing 13 Factor Core5 E2E Summary

This note records the first end-to-end check for the current 13 implemented chip factors.

It is a process validation note, not a trading conclusion.

## Purpose

The question for this E2E was:

> Can the current pipeline show whether existing factors are duplicated, mirror-related, or backtest-similar inside a selected stock group?

Plain-language version:

> Before searching for new factors, we need to know whether the factors we already have are saying different things or mostly repeating each other.

## Input

Factor batch summary:

```text
docs/factor-batches/factor-batch-2026q1-core5-live-combined/factor-batch-summary.json
```

Stocks:

```text
000001.SZ
600519.SH
300750.SZ
601318.SH
000858.SZ
```

Observation period in the source artifacts:

```text
2026-01-05 through 2026-04-30
```

Forward-return windows:

```text
N+1
N+3
N+5
```

This uses 2026 data only as an existing small E2E sample. It must not be used to choose final strategy rules.

## Generated Artifacts

Redundancy review artifact:

```text
docs/factor-redundancy-reviews/factor-redundancy-review-core5-2026q1-e2e/
```

Signal similarity review artifact:

```text
docs/factor-signal-similarity-reviews/factor-signal-similarity-review-core5-2026q1-e2e-v2/
```

The artifact directories are ignored by git because they are generated run output. This summary records the durable findings.

## What Was Checked

### Factor Value Redundancy

This checks whether two factors produce highly correlated daily values inside each stock.

Plain-language version:

> If two factors move almost exactly together, they are probably not two independent signals.

### Signal Trigger Similarity

This checks whether two factors reach their high or low zones on the same dates.

Plain-language version:

> If two factors tell us to pay attention on the same days, they may behave like the same rule in practice.

### Backtest Behavior Similarity

This checks whether high-factor and low-factor days have similar future-return behavior.

Plain-language version:

> Even if two factors are not mathematically identical, they may still produce similar backtest results.

## Strong Redundancy Findings

The redundancy review found these high-confidence candidates:

```text
cyq_cgo_asof vs weighted_chip_cost_gap_asof
loss_ratio_asof vs profit_ratio_asof
loss_ratio_delta_20d vs profit_ratio_delta_20d
```

Interpretation:

- `loss_ratio_asof` and `profit_ratio_asof` are mirror-style factors.
- `loss_ratio_delta_20d` and `profit_ratio_delta_20d` are mirror-style change factors.
- `cyq_cgo_asof` and `weighted_chip_cost_gap_asof` are very close in value behavior across the core5 sample.

Plain-language takeaway:

> These pairs should not be counted as independent evidence in a later strategy. We can keep one, or keep both only with an explicit downweighting rule.

## Strong Signal Similarity Findings

The signal similarity review found these strongest cross-stock candidates:

```text
cyq_cgo_asof vs weighted_chip_cost_gap_asof
  N+1: trigger_similar=5/5, behavior_similar=5/5
  N+3: trigger_similar=5/5, behavior_similar=5/5
  N+5: trigger_similar=5/5, behavior_similar=5/5

loss_ratio_asof vs profit_ratio_asof
  N+1: trigger_similar=5/5, behavior_similar=5/5
  N+3: trigger_similar=5/5, behavior_similar=4/5
  N+5: trigger_similar=5/5, behavior_similar=5/5

loss_ratio_delta_20d vs profit_ratio_delta_20d
  N+1: trigger_similar=5/5, behavior_similar=5/5
  N+3: trigger_similar=5/5, behavior_similar=5/5
  N+5: trigger_similar=5/5, behavior_similar=5/5
```

Interpretation:

> The trigger and backtest checks agree with the value redundancy review on the most obvious duplicate or mirror pairs.

This is important because it means the review process is not only finding formula-level relationships. It is also finding practical backtest-level similarity.

## Is The Backtest Process Meaningful?

For this small E2E, yes, but only as a process check.

Evidence:

- The process identified known mirror pairs such as `loss_ratio_asof` vs `profit_ratio_asof`.
- The process identified value-similar pairs such as `cyq_cgo_asof` vs `weighted_chip_cost_gap_asof`.
- The same relationships appeared across multiple stocks and multiple future-return windows.
- The output is traceable to source artifacts and per-instrument observation files.

Limitations:

- The sample is only five stocks.
- The date range is short.
- The sample is from 2026, which should be reserved as future holdout data in the real research split.
- The output does not yet prove any factor can make money.

Plain-language conclusion:

> The backtest process is useful for detecting duplicate signals and checking whether factors behave differently. It is not yet enough to select an investment strategy.

## Next Step

Run the same workflow on the discovery period:

```text
2024-01-01 through 2025-12-31
```

Then reserve 2026 for holdout testing after factor retention and rule design decisions are fixed.
