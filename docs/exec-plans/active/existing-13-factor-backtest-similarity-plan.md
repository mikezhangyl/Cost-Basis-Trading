# Existing 13 Factor Backtest Similarity Plan

This plan focuses on the current 13 implemented chip factors. It does not introduce new factors and does not use deep learning.

## Goal

Answer this question:

> After factor redundancy cleanup, do the current factors show useful and distinct backtest behavior inside a selected portfolio?

Plain-language version:

> We want to know whether these factors actually help when checked against historical future returns, and whether multiple factors are secretly triggering the same kind of signal.

## Selected Portfolio For First E2E

Use the current completed core-5 batch:

```text
000001.SZ
600519.SH
300750.SZ
601318.SH
000858.SZ
```

Source artifact:

```text
docs/factor-batches/factor-batch-2026q1-core5-live-combined/factor-batch-summary.json
```

This is not the final research period. It is the first working E2E so we can see the process and generated reports.

## Final Research Split

After the E2E works, expand data:

```text
Discovery / research period: 2024-01-01 through 2025-12-31
Holdout / final test period: 2026-01-01 onward
```

If Tushare cost or data volume is too high, fallback:

```text
Discovery / research period: 2025-01-01 through 2025-12-31
Holdout / final test period: 2026-01-01 onward
```

The 2026 holdout must not be used to choose thresholds or rules.

## Current Capability

Already available:

- factor production,
- factor batch evaluation,
- factor redundancy review by per-instrument correlation,
- ECC artifact review for factor batches and factor-redundancy reviews.

Missing before this plan:

- compare factor signal-trigger similarity,
- compare factor backtest-behavior similarity,
- produce beginner-friendly reports explaining whether the backtest process is meaningful.

## What "Signal Similarity" Means

For every factor in each stock:

1. Sort historical daily factor values.
2. Define high-factor days as the top 20%.
3. Define low-factor days as the bottom 20%.
4. Compare whether two factors trigger on the same dates.

Example:

```text
factor A high-trigger dates: Jan 5, Jan 8, Jan 20
factor B high-trigger dates: Jan 5, Jan 8, Jan 21
```

These two are very similar because they trigger on mostly the same dates.

Metric:

```text
Jaccard overlap = intersection(trigger dates) / union(trigger dates)
```

Plain-language meaning:

> If two factors tell us to pay attention on the same days, they may be redundant in practice.

## What "Backtest Behavior Similarity" Means

For each factor:

- high-trigger average future return,
- low-trigger average future return,
- high-trigger win rate,
- low-trigger win rate,
- top-bottom spread.

Then compare whether two factors behave similarly across:

- N+1,
- N+3,
- N+5,
- each stock,
- portfolio summary.

Plain-language meaning:

> Even if two factors are not mathematically identical, they may still behave the same way during backtesting.

## First Implementation Artifact

Create a script:

```text
scripts/factor_signal_similarity_review.py
```

Input:

```text
--factor-batch-summary docs/factor-batches/<batch-id>/factor-batch-summary.json
--output-dir docs/factor-signal-similarity-reviews/<review-id>
--quantile 0.20
```

Output:

```text
docs/factor-signal-similarity-reviews/<review-id>/
  review-config.json
  source-data-manifest.json
  review-events.jsonl
  per-instrument/
    <ts_code>/
      factor-signal-stats.json
      factor-pair-signal-similarity.json
  cross-object-signal-similarity-summary.json
  factor-signal-similarity-report.md
```

## First E2E Steps

1. Run factor redundancy review on the current core-5 batch.
2. Run factor signal similarity review on the same batch.
3. Compare:
   - formula/value redundancy,
   - trigger overlap,
   - backtest behavior similarity.
4. Produce a beginner-friendly summary:
   - which factors look duplicated,
   - which factors behave similarly in backtest,
   - which factors remain distinct enough to keep studying.

## What Counts As "Meaningful"

The backtest process has some meaning if it can answer:

1. Are there enough observations?
2. Do high/low factor states have different future returns?
3. Are results stable across more than one stock?
4. Do redundant factors produce similar triggers?
5. Do supposedly distinct factors behave differently enough to justify keeping them?

It does not need to produce a profitable trading strategy yet.

## Non-Goals

Do not do these in this phase:

- do not search for new factors,
- do not train deep learning models,
- do not make live trading decisions,
- do not produce buy/sell advice,
- do not tune on 2026 holdout data.

