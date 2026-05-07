# Execution Plan: Chip Factor Production 2026 Q1

## Goal

Build a local-first chip-factor production lane for factor dates from 2026-01-01 to 2026-04-30. The first implementation focuses on correctness, reproducibility, local persistence, and traceability. It does not output trading actions.

Design source:

- [../../design/chip-factor-production-plan-2026q1.md](../../design/chip-factor-production-plan-2026q1.md)

## Scope

In scope:

- local factor-run artifact layout,
- local API/cache directory conventions,
- deterministic factor formulas,
- formula fixture tests,
- invariant tests,
- same-day chip snapshot factors,
- 20-trading-day delta/proxy factors,
- retry/event logging compatibility with existing Tushare resilience,
- first CLI skeleton for local factor production.

Out of scope for this phase:

- UI changes,
- BUY/HOLD/SELL strategy changes,
- full-market execution,
- Kafka/Redpanda,
- external database deployment,
- automatic factor promotion.

## First Stock Scope

Start with a tiny stock set to validate the pipeline:

```text
000001.SZ
600519.SH
300750.SZ
```

The first code skeleton should be able to run with mocked data and then with one live stock. Scaling to more stocks waits until correctness and retry behavior are proven.

## Date Semantics

```text
factor output range: 2026-01-01 to 2026-04-30
warmup range: fetched as needed by factor lookback windows
```

The implementation should expose both:

- requested output range,
- actual data fetch range.

Early output dates that lack lookback history must be marked `INSUFFICIENT_HISTORY`, not silently calculated from partial history unless the run config explicitly allows partial factors.

## Local Persistence

Recommended local directories:

```text
data/
  factor-cache/
    tushare/
      cyq_chips/
      daily/
    jobs.sqlite
docs/
  factor-runs/
```

`data/factor-cache/` is local cache and should not be committed.

Run artifacts under `docs/factor-runs/<factor_run_id>/` are immutable evidence packages:

```text
factor-run-config.json
factor-run-manifest.json
api-calls.jsonl
api-retry-events.jsonl
worker-events.jsonl
stocks/<ts_code>/daily-chip-snapshots.jsonl
stocks/<ts_code>/factors.jsonl
stocks/<ts_code>/factor-quality.json
stocks/<ts_code>/factor-traceability.json
```

## Queue And Retry Choice

Use a local SQLite job table and append-only logs first.

Do not introduce Kafka/Redpanda until there is a demonstrated need for distributed producers/consumers or SQLite locking becomes a real bottleneck.

Job states:

```text
PENDING
RUNNING
SUCCEEDED
FAILED_RETRYABLE
FAILED_PERMANENT
SKIPPED_EXISTING
```

Every API request, retry, failure classification, worker decision, and artifact write should be locally traceable.

## Formula Implementation Order

### Phase 1: Daily Snapshot

Implement:

- `weighted_chip_cost`
- `dominant_peak_price`
- `dominant_peak_percent`
- `profit_ratio`
- `loss_ratio`
- `cyq_cgo`
- `concentration_width_70`
- `concentration_width_90`
- `chip_weighted_std`

### Phase 2: Same-Day Factors

Implement:

- `profit_ratio_asof`
- `loss_ratio_asof`
- `cyq_cgo_asof`
- `weighted_chip_cost_gap_asof`
- `dominant_peak_strength_asof`
- `concentration_width_70_asof`
- `concentration_width_90_asof`
- `chip_weighted_std_asof`

### Phase 3: 20-Day Factors

Implement:

- `profit_ratio_delta_20d`
- `loss_ratio_delta_20d`
- `weighted_chip_cost_delta_20d`
- `concentration_width_70_delta_20d`
- `dominant_peak_price_delta_20d`
- `retained_core_chip_ratio_20d_proxy`
- `high_chip_accumulation_20d_proxy`

## Correctness Gate

Before any live Tushare run is trusted:

- formula fixture tests pass,
- invariant tests pass,
- missing-data tests pass,
- no-future-data tests pass,
- `python scripts/ecc_quality_workflow.py quality-gate` passes.

Required tests:

- hand-calculated daily snapshot fixture,
- concentration-width slow reference implementation comparison,
- missing close returns null price-dependent factors,
- missing lookback marks 20-day factors `INSUFFICIENT_HISTORY`,
- same factor run config produces the same artifact path shape,
- completed artifacts are not overwritten unless a new run id is created.

## CLI Skeleton

Add a project-local CLI entrypoint:

```bash
python scripts/chip_factor_runner.py \
  --stock-codes 000001.SZ \
  --factor-start-date 20260101 \
  --factor-end-date 20260430 \
  --dry-run
```

The first skeleton can support mocked/dry-run generation before live Tushare fetching is wired in.

## Acceptance Criteria

- Design and exec plan are committed before implementation.
- `data/factor-cache/` is ignored by git.
- Domain models for factor snapshots and factor values exist.
- Formula tests cover the daily snapshot factors.
- CLI dry-run writes a local immutable `docs/factor-runs/<factor_run_id>/` artifact package.
- Artifact package includes config, manifest, worker events, factor traceability, and quality summary.
- Quality gate passes.

## Verification

Run inside ECC Quality Sub-Agent when available:

```bash
python scripts/ecc_quality_workflow.py quality-gate
```

