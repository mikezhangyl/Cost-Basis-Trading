---
name: factor-redundancy-review
description: Review quantitative factor redundancy across one or more investment objects without mixing raw observations across objects. Use when cleaning factor sets, classifying duplicate or mirror factors, generating keep/exclude/downweight decisions, or preparing traceable factor-redundancy artifacts before strategy research.
---

# Factor Redundancy Review

Use this skill when a factor set needs redundancy review before strategy research. It is a factor-hygiene workflow, not a trading strategy workflow.

## Hard Rules

- Treat each investment object independently.
- The canonical key is `(instrument_id, factor_date, factor_id)`.
- Never classify a factor relationship using raw observations from different investment objects in the same correlation sample.
- Cross-object conclusions may only aggregate per-instrument evidence.
- Raw pooled correlation is diagnostic-only and must never drive `EXCLUDE`.
- Do not give buy, sell, or hold advice.
- Do not mutate source factor-run or factor-batch artifacts.

## Current Project Command

Run the prototype against a completed factor batch:

```bash
python scripts/factor_redundancy_review.py \
  --factor-batch-summary docs/factor-batches/<batch-id>/factor-batch-summary.json \
  --output-dir docs/factor-redundancy-reviews/<review-id> \
  --correlation-threshold 0.90 \
  --min-observations 30 \
  --method pearson
```

The output directory is immutable. Pick a new `<review-id>` for each run.

## Required Outputs

The script writes:

```text
docs/factor-redundancy-reviews/<review-id>/
  review-config.json
  source-data-manifest.json
  review-events.jsonl
  per-instrument/
    <instrument_id>/
      factor-correlation-matrix.csv
      factor-pair-relationships.json
      factor-retention-decisions.json
  cross-object-redundancy-summary.json
  pooled-diagnostics.json
  factor-redundancy-groups.json
  factor-redundancy-report.md
```

## Interpretation Rules

- `same_direction_duplicate`: factors move together inside one investment object.
- `opposite_direction_duplicate`: factors move in opposite directions inside one investment object.
- `derived_but_not_duplicate`: usually an `asof` factor and its `delta_20d` variant; do not auto-exclude.
- `insufficient_observations`: no reliable decision.
- `pooling_artifact_risk`: pooled data looks correlated but per-instrument evidence does not support redundancy.

Decision behavior:

- Formula-level mirror relationships may produce per-instrument `exclude` decisions.
- Non-formula high correlation should usually produce `downweight`, not `exclude`.
- Cross-object summaries should use `global_downweight_candidate`, `global_review_required`, or `global_no_decision`; never global exclude.

## Quality Gate

After generating artifacts, run ECC Artifact Reviewer:

```bash
python scripts/ecc_artifact_reviewer.py \
  --artifact-type factor-redundancy-review \
  --run-id <review-id> \
  --no-llm
```

The review must pass before handing results to a strategy agent.

## When Updating The Workflow

Keep these tests green:

```bash
pytest -v backend/tests/test_factor_redundancy_review.py
pytest -v backend/tests/test_ecc_artifact_reviewer.py
```

Run full backend tests before declaring the workflow stable:

```bash
cd backend && pytest -v
```

## References

- `docs/design/factor-redundancy-review-skill.md`
- `scripts/factor_redundancy_review.py`
- `scripts/ecc_artifact_reviewer.py`
