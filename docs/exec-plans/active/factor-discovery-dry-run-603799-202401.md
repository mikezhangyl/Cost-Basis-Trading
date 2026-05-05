# Factor Discovery Dry Run Plan: 603799 January 2024

This plan covers the first small live-data dry run for the existing factor research workflow.

## Goal

Run one stock through the factor-production and factor-review workflow on a one-month sample before expanding to the full 2024-2025 discovery period.

Plain-language goal:

> Use one stock and one month to verify that the pipeline can safely fetch data, compute factors, remove duplicated factor evidence, and produce traceable artifacts without overusing the Tushare API.

## Important Terminology

In this plan, "dry run" means a small controlled live-data trial.

It does **not** mean passing the current script's `--dry-run` flag, because that flag uses deterministic local fixture data and does not call Tushare.

## Scope

Stock:

```text
603799
```

Normalized Tushare code:

```text
603799.SH
```

Factor output period:

```text
2024-01-01 through 2024-01-31
```

Warmup behavior:

- The current factor runner needs warmup trading days for 20-day delta factors.
- It will fetch trading days before `2024-01-01`, but factor outputs should only be generated inside `2024-01-01` through `2024-01-31`.

Forward-return windows:

```text
N+1
N+3
N+5
```

## Required Pre-Run Changes

### 1. Add Tushare Rate Limiting

Tushare quota:

```text
500 calls / minute
```

Implementation target:

```text
backend/app/data/tushare_client.py
```

Expected behavior:

- Apply rate limiting inside `TushareMarketDataClient._call_tushare`.
- Use one limiter for every Tushare endpoint, including:
  - `trade_cal`
  - `cyq_chips`
  - `daily`
  - `stock_basic`
- Default max calls per minute should be configurable but default to `500`.
- The limiter should run before every actual Tushare API call attempt.
- Retry attempts should also count as API calls.
- Emit enough metadata in retry/API logs to diagnose waiting behavior where practical.

Plain-language reason:

> If the API allows 500 calls per minute, the code should pace itself instead of relying on Tushare to reject excessive calls.

Safety margin:

```text
500 / minute = 1 call every 0.12 seconds
```

For the first live run, use a slightly conservative effective setting such as:

```text
450 calls / minute
```

### 2. Normalize Bare Stock Codes In Factor Scripts

Current user input is:

```text
603799
```

The factor scripts should normalize it to:

```text
603799.SH
```

Implementation target:

```text
scripts/chip_factor_runner.py
scripts/chip_factor_batch.py
```

Use the existing normalizer:

```text
backend/app/services/code_normalizer.py
```

Plain-language reason:

> The website already accepts bare six-digit codes. The factor dry-run scripts should behave the same way.

### 3. Use Factor Redundancy Skill Before Strategy Research

Skill:

```text
skills/factor-redundancy-review/SKILL.md
```

Process:

1. Produce the one-stock factor batch.
2. Run `factor-redundancy-review` on the generated batch.
3. Run ECC Artifact Reviewer on the redundancy artifact.
4. Use the review output to classify factors before signal-similarity or rule research.

Important limitation:

> With only one stock and one month, redundancy findings are useful as a pipeline check, not as final factor-retention truth.

## Planned Commands

After the pre-run changes and tests pass, run:

```bash
set -a; source .env.local; set +a; python scripts/chip_factor_batch.py \
  --stock-codes 603799 \
  --factor-start-date 20240101 \
  --factor-end-date 20240131 \
  --offsets 1 3 5 \
  --batch-id factor-batch-dryrun-603799-202401-live \
  --sleep-between-stocks 0
```

Then run the redundancy review:

```bash
python scripts/factor_redundancy_review.py \
  --factor-batch-summary docs/factor-batches/factor-batch-dryrun-603799-202401-live/factor-batch-summary.json \
  --output-dir docs/factor-redundancy-reviews/factor-redundancy-review-dryrun-603799-202401-live \
  --correlation-threshold 0.90 \
  --min-observations 10 \
  --method pearson
```

Then run ECC Artifact Reviewer:

```bash
python scripts/ecc_artifact_reviewer.py \
  --artifact-type factor-redundancy-review \
  --run-id factor-redundancy-review-dryrun-603799-202401-live \
  --no-llm
```

Then run signal similarity review:

```bash
python scripts/factor_signal_similarity_review.py \
  --factor-batch-summary docs/factor-batches/factor-batch-dryrun-603799-202401-live/factor-batch-summary.json \
  --output-dir docs/factor-signal-similarity-reviews/factor-signal-similarity-review-dryrun-603799-202401-live \
  --quantile 0.20 \
  --min-trigger-count 3 \
  --trigger-similarity-threshold 0.75 \
  --spread-diff-threshold 0.005 \
  --review-id factor-signal-similarity-review-dryrun-603799-202401-live
```

## Validation Before Live Run

Before calling Tushare:

```bash
pytest -v backend/tests/test_tushare_client.py backend/tests/test_chip_factors.py backend/tests/test_chip_factor_batch.py
```

Then run the full quality gate:

```bash
python scripts/ecc_quality_workflow.py quality-gate
```

## Expected API Call Shape

Approximate call count for one stock and one month:

- `trade_cal`: at least 2 calls, because date resolution and chip daily fetch both resolve trading days.
- `cyq_chips`: about one call per trading day in the warmup-plus-output period.
- `daily`: 1 call for the full price range.

This should stay well below 500 calls per minute, but the limiter is still required before expanding to more stocks and longer history.

## Success Criteria

The dry run is successful only if:

- Factor batch status is `completed`.
- The run writes `api-calls.jsonl` and `api-retry-events.jsonl`.
- The stock code is normalized to `603799.SH`.
- Factor artifacts include checksums.
- Redundancy review completes.
- ECC Artifact Reviewer passes for the redundancy artifact.
- Signal similarity review completes.
- No direct buy/sell/hold investment advice is produced.

## Stop Conditions

Stop and inspect logs if:

- Tushare returns permission errors.
- Tushare returns repeated rate-limit errors even with the limiter.
- The batch status is `failed` or `partial`.
- Redundancy review reports malformed or insufficient source artifacts.
- Any test or quality gate fails.
