# Factor Redundancy Review Skill Design

## Status

Draft created on 2026-05-05. This is a design document only. Do not implement the script or reusable skill until this design has been reviewed.

Design review status: conditionally implementation-ready after the constraints in this document are accepted. The implementation should proceed test-first and must not weaken the instrument-isolation rule.

## Plain-Language Goal

The skill should answer:

> Which factors are mostly telling the same story, which factors are mirror versions of the same story, and which factors should remain available for later strategy research?

A factor is a numeric feature calculated for one investment object on one date. In this project, an investment object is usually one stock code such as `000001.SZ`.

The skill must not give buy, sell, or hold advice. Its role is factor hygiene: classify overlap, explain redundancy, and produce traceable keep, exclude, downweight, or warning recommendations.

## Hard Methodology Rule

Factor redundancy must be reviewed inside each investment object first.

The canonical data key is:

```text
(instrument_id, factor_date, factor_id)
```

For example:

```text
(000001.SZ, 20260105, profit_ratio_asof)
```

The skill must not compare raw observations from different investment objects in the same correlation sample. It must not calculate a pairwise factor correlation by mixing `000001.SZ` rows with `600519.SH` rows as if they were one continuous time series.

This is a hard correctness constraint, not a performance preference.

Reason:

> Two stocks can have similar-looking factor values for unrelated reasons. That does not mean one stock's factor should affect the other stock's factor-retention decision.

## Non-Goals

This skill must not:

- give investment advice
- train a trading model
- choose the final trading strategy
- delete source factor artifacts
- mutate immutable factor-run or factor-batch outputs
- use cross-object raw pooling as the primary redundancy evidence

## Inputs

The first implementation should support this project's factor artifacts while keeping the design adapter-friendly.

### Required CLI Inputs

```text
--factor-batch-summary <path>
--output-dir <path>
--correlation-threshold <float, default 0.90>
--min-observations <int, default 30>
--method <pearson|spearman, default pearson>
```

Alternative first-version input:

```text
--factor-run-dirs <path> [<path> ...]
```

### Optional CLI Inputs

```text
--factor-metadata <path>
--formula-hints <path>
--min-instruments-for-global-summary <int, default 2>
--strong-consensus-ratio <float, default 0.80>
```

### Input Schema

The first adapter should read JSONL factor rows with at least:

```json
{
  "factor_id": "profit_ratio_asof",
  "factor_date": "20260105",
  "value": 50.79,
  "quality_status": "OK",
  "lookback_days": null,
  "formula_version": "chip-factor-v1",
  "source_level": "FACTOR_FAMILY",
  "implementation_type": "exact from Tushare cyq_chips snapshot",
  "explanation": "..."
}
```

`instrument_id` should be derived from the stock directory or batch summary result in the current project. Future adapters may provide it directly.

Rows with `value = null` should be excluded from correlation calculations. Rows with `quality_status != "OK"` should be excluded by default and counted in data-quality summaries.

## Relationship Categories

The implementation should use stable enum-like strings so generated artifacts are easy to test and consume.

Recommended relationship types:

```text
same_direction_duplicate
opposite_direction_duplicate
formula_level_relationship
derived_but_not_duplicate
complementary
pooling_artifact_risk
weak_or_no_relationship
insufficient_observations
constant_values
malformed_input
```

Recommended recommendation values:

```text
keep
exclude
downweight
keep_with_warning
no_decision
```

Recommended global recommendation values:

```text
global_keep_with_warning
global_downweight_candidate
global_review_required
global_no_decision
```

### Same-Direction Duplicate

Two factors move together inside the same investment object.

Plain-language explanation:

> When factor A is high, factor B is also high. They may be saying almost the same thing.

Classification hint:

```text
correlation >= threshold
```

### Opposite-Direction Duplicate

Two factors move in opposite directions inside the same investment object.

Plain-language explanation:

> When factor A is high, factor B is low. They may be mirror versions of the same information.

Classification hint:

```text
correlation <= -threshold
```

### Formula-Level Relationship

Some factors are connected by construction, not only because historical data happens to be correlated.

Current project example:

```text
profit_ratio_asof + loss_ratio_asof + at_close_ratio ~= total chip percent
```

`at_close_ratio` is available in daily chip snapshots, not in the final 13 factor rows. First implementation may treat this as metadata evidence. A later implementation may verify the formula residual by reading `daily-chip-snapshots.jsonl`.

### Derived But Not Duplicate

Some factors share a base concept but measure different time structure.

Example:

```text
profit_ratio_asof
profit_ratio_delta_20d
```

Plain-language explanation:

> The first is the current state. The second is how the state changed over 20 trading days. They are related, but they should not be automatically removed.

### Complementary Factors

Some factors may be correlated but still describe different dimensions.

Example:

```text
weighted_chip_cost_gap_asof
concentration_width_70_asof
```

Plain-language explanation:

> One asks where price sits relative to average holder cost. The other asks whether holder costs are concentrated or scattered.

### Pooling Artifact Risk

This is a required warning category for multi-object data.

Use it when raw pooled correlation looks high but per-instrument evidence does not support a stable relationship.

Plain-language explanation:

> The factors look similar only after different investment objects are mixed together. That may be a statistical artifact, so the skill must not use it to remove a factor.

## Quantitative Design

### Per-Instrument Correlation

For each `instrument_id`, create a date-by-factor table:

```text
factor_date rows
factor_id columns
factor values
```

Compute pairwise correlation only within that table.

Each pair relationship must record:

```json
{
  "instrument_id": "000001.SZ",
  "factor_a": "profit_ratio_asof",
  "factor_b": "loss_ratio_asof",
  "method": "pearson",
  "correlation": -0.998,
  "observation_count": 77,
  "relationship_type": "opposite_direction_duplicate"
}
```

If overlapping observations are below `--min-observations`, write the pair with:

```text
relationship_type = insufficient_observations
recommendation = no_decision
```

and do not use it as global evidence.

### Cross-Object Summary

Cross-object analysis is allowed only as aggregation over per-instrument results.

Allowed:

```text
For factor A vs factor B, 5 of 5 eligible instruments show same-direction duplicate.
```

Forbidden:

```text
Pool all rows from all instruments and calculate one raw correlation as the primary decision.
```

The summary for each factor pair should record:

```json
{
  "scope": "cross_object_summary",
  "factor_a": "cyq_cgo_asof",
  "factor_b": "weighted_chip_cost_gap_asof",
  "eligible_instrument_count": 5,
  "strong_relationship_count": 5,
  "consensus_ratio": 1.0,
  "dominant_relationship_type": "same_direction_duplicate",
  "correlation_median": 0.994,
  "correlation_min": 0.991,
  "correlation_max": 0.997,
  "global_recommendation": "global_downweight_candidate"
}
```

Cross-object summary may inform later strategy research, but it must not erase per-instrument decisions.

### Optional Diagnostic: Pooled Risk Check

The implementation may calculate a raw pooled correlation only as a diagnostic risk check.

Rules:

- It must be labeled `diagnostic_only`.
- It must not drive `EXCLUDE`.
- If pooled correlation is high but per-instrument consensus is weak, emit `pooling_artifact_risk`.

## Implementation Algorithm

The local script should follow this sequence exactly.

1. Parse and validate configuration.
2. Refuse to write into an existing output directory unless an explicit future `--overwrite` flag is designed and approved.
3. Write `review-config.json`.
4. Build `source-data-manifest.json` from either batch summary or factor-run directories.
5. Load factor rows per instrument.
6. Validate each row has `factor_id`, `factor_date`, `value`, and `quality_status`.
7. Build one date-by-factor table per instrument.
8. For each instrument table, calculate pairwise correlations and observation counts.
9. Classify per-instrument pair relationships.
10. Apply formula and family hints as additional evidence, never as a substitute for scoping.
11. Create per-instrument retention decisions.
12. Aggregate per-instrument evidence into `cross-object-redundancy-summary.json`.
13. Optionally compute diagnostic-only pooled correlations and emit pooling warnings when needed.
14. Create `factor-redundancy-groups.json`.
15. Create beginner-friendly `factor-redundancy-report.md`.
16. Write completion events.

The script should use `pandas` for correlation because the project already depends on `pandas>=2.2`. `DataFrame.corr(method="pearson")` and `DataFrame.corr(method="spearman")` are sufficient for the first implementation. Do not introduce new numerical dependencies unless tests show `pandas` is insufficient.

## Validation Rules

Input validation should fail fast with clear errors.

Required failures:

- output directory already exists
- batch summary path does not exist
- batch summary has no completed stock results
- completed stock result lacks `ts_code` or `factor_run_dir`
- expected `stocks/<instrument_id>/factors.jsonl` does not exist
- factor JSONL row is malformed JSON
- factor row lacks `factor_id` or `factor_date`
- factor row has a non-numeric non-null `value`
- unsupported correlation method is requested
- correlation threshold is not between `0` and `1`
- `min_observations` is less than `2`

Non-fatal data-quality conditions:

- `quality_status != "OK"`
- `value = null`
- pair has insufficient overlapping observations
- pair has constant values

These conditions should be recorded in artifacts rather than crashing the whole review.

## Decision Rules

Per-instrument decisions are the primary decisions.

### KEEP

Use when:

- the factor is not strongly redundant inside the instrument
- or it is the selected primary factor in a redundant family
- or it is formula-related but more interpretable or higher quality than alternatives

### EXCLUDE

Use only inside a specific `instrument_id` when:

- redundancy is strong inside that instrument
- the relationship has sufficient observations
- formula-level or concept-level evidence supports overlap
- there is a clear replacement factor

Do not use `EXCLUDE` for a high-correlation pair that has no formula or concept-family evidence. Use `DOWNWEIGHT` or `KEEP_WITH_WARNING` instead.

### DOWNWEIGHT

Use when:

- the factor may still contain useful information
- but overlaps with a stronger or clearer factor inside the same instrument
- later strategy agents should avoid counting it as independent evidence

If a factor is selected as the primary factor in a strong formula-level relationship, non-formula overlaps should not override that primary decision. The overlapping non-formula factor should be downweighted instead.

### KEEP_WITH_WARNING

Use when:

- evidence is mixed
- the factor pair is related but not identical
- different instruments disagree
- more data is needed before excluding

### NO_DECISION

Use when:

- observations are insufficient
- values are constant
- input data is malformed
- the pair cannot be compared safely

## Deterministic Tie-Breakers

When two factors are redundant and one must be selected as primary, use this ordered tie-breaker:

1. Prefer factor with better data quality: fewer missing or non-OK rows.
2. Prefer exact implementation over proxy implementation.
3. Prefer clearer plain-language interpretation for non-expert users.
4. Prefer current-state `asof` factor over derived factor only if the relationship is formula-level and the derived factor is not measuring change.
5. Prefer factor with stronger historical diagnostic stability if evaluation artifacts are provided.
6. Fall back to stable lexical order and record that this fallback was used.

For the current chip factors, expected first-pass preferences are:

- `loss_ratio_asof` may be preferred over `profit_ratio_asof` if the research narrative focuses on trapped-chip pressure.
- `weighted_chip_cost_gap_asof` may be preferred over `cyq_cgo_asof` because it is an exact factor-family measure while `cyq_cgo_asof` is marked as a proxy.

These are not hardcoded deletions. They are tie-breaker hints that must still be backed by per-instrument evidence.

## Artifact Layout

Each review run should create:

```text
docs/factor-redundancy-reviews/<review_id>/
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

### `review-config.json`

Purpose:

- freeze the run settings for reproducibility.

Required fields:

```json
{
  "review_id": "factor-redundancy-review-20260505-001",
  "created_at": "2026-05-05T00:00:00+08:00",
  "input_mode": "factor_batch_summary",
  "correlation_threshold": 0.9,
  "min_observations": 30,
  "method": "pearson",
  "instrument_isolation": true,
  "raw_pooled_correlation_policy": "diagnostic_only"
}
```

### `source-data-manifest.json`

Purpose:

- record exactly which immutable inputs were used.

Required fields:

```json
{
  "factor_batch_summary_path": "...",
  "factor_run_dirs": [
    "..."
  ],
  "instruments": [
    {
      "instrument_id": "000001.SZ",
      "factors_jsonl": "...",
      "row_count": 1001,
      "ok_value_count": 1001,
      "date_min": "20260105",
      "date_max": "20260430"
    }
  ]
}
```

### `review-events.jsonl`

Purpose:

- trace the review process.

Required event types:

```text
review_started
source_manifest_started
source_manifest_completed
load_factor_data_started
load_factor_data_completed
per_instrument_correlation_started
per_instrument_correlation_completed
relationship_classification_started
relationship_classification_completed
cross_object_summary_started
cross_object_summary_completed
retention_decision_started
retention_decision_completed
write_artifacts_completed
review_completed
```

### Per-Instrument Artifacts

`factor-correlation-matrix.csv` is a square matrix for one instrument only.

`factor-pair-relationships.json` contains only relationships for one instrument.

`factor-retention-decisions.json` contains only decisions for one instrument.

Every row or object must include enough evidence to explain the decision:

- factor pair
- relationship type
- correlation method
- correlation value
- observation count
- formula evidence if used
- decision or recommendation
- plain-language explanation

Recommended `factor-pair-relationships.json` object:

```json
{
  "scope": "per_instrument",
  "instrument_id": "000001.SZ",
  "factor_a": "profit_ratio_asof",
  "factor_b": "loss_ratio_asof",
  "relationship_type": "opposite_direction_duplicate",
  "recommendation": "exclude",
  "primary_factor": "loss_ratio_asof",
  "replacement_factor": "loss_ratio_asof",
  "correlation": -0.998,
  "correlation_method": "pearson",
  "observation_count": 77,
  "formula_evidence": {
    "evidence_type": "metadata_hint",
    "description": "profit_ratio_asof + loss_ratio_asof + at_close_ratio ~= total chip percent"
  },
  "tie_breaker_evidence": [
    "loss_ratio_asof is more directly interpretable as trapped-chip pressure"
  ],
  "plain_language_explanation": "这两个因子基本是在用相反方向描述赚钱筹码和亏损筹码。"
}
```

Recommended `factor-retention-decisions.json` object:

```json
{
  "scope": "per_instrument",
  "instrument_id": "000001.SZ",
  "factor_id": "profit_ratio_asof",
  "decision": "exclude",
  "reason": "Highly mirror-related to loss_ratio_asof inside this instrument.",
  "related_factors": [
    "loss_ratio_asof"
  ],
  "replacement_factor": "loss_ratio_asof",
  "evidence_refs": [
    {
      "artifact": "factor-pair-relationships.json",
      "factor_a": "loss_ratio_asof",
      "factor_b": "profit_ratio_asof"
    }
  ]
}
```

### Cross-Object Artifacts

`cross-object-redundancy-summary.json` summarizes per-instrument evidence by factor pair.

It must not include raw cross-object pair samples. Its unit of evidence is one instrument-level result.

Recommended summary object:

```json
{
  "scope": "cross_object_summary",
  "factor_a": "cyq_cgo_asof",
  "factor_b": "weighted_chip_cost_gap_asof",
  "eligible_instrument_count": 5,
  "strong_relationship_count": 5,
  "insufficient_instrument_count": 0,
  "consensus_ratio": 1.0,
  "dominant_relationship_type": "same_direction_duplicate",
  "correlation_median": 0.994,
  "correlation_min": 0.991,
  "correlation_max": 0.997,
  "global_recommendation": "global_downweight_candidate",
  "instrument_evidence": [
    {
      "instrument_id": "000001.SZ",
      "relationship_type": "same_direction_duplicate",
      "correlation": 0.993,
      "observation_count": 77
    }
  ],
  "plain_language_explanation": "这组关系在多个投资对象内部重复出现，但全局结论只是提示后续策略避免重复计数。"
}
```

`pooled-diagnostics.json` is diagnostic-only. It exists to catch cases where raw pooled correlation looks high but per-instrument evidence does not support redundancy.

Recommended diagnostic object:

```json
{
  "scope": "diagnostic_only",
  "diagnostic_type": "pooling_artifact_risk",
  "factor_a": "factor_a",
  "factor_b": "factor_b",
  "raw_pooled_correlation": 0.99,
  "correlation_method": "pearson",
  "pooled_observation_count": 100,
  "per_instrument_global_recommendation": "global_no_decision",
  "plain_language_explanation": "The pair looks highly correlated only after investment objects are pooled; do not use this to exclude factors."
}
```

This artifact must never drive `EXCLUDE`.

`factor-redundancy-groups.json` groups factor families using both metadata hints and cross-object evidence. It should distinguish:

```text
group_scope = per_instrument | cross_object_summary
```

### Human Report

`factor-redundancy-report.md` must include:

- what was reviewed
- how many instruments were reviewed
- how many factors were reviewed
- date range per instrument
- plain-language glossary
- per-instrument duplicate and mirror relationships
- cross-object consensus relationships
- factors recommended to keep, exclude, downweight, or keep with warning
- pooling artifact warnings
- limitations and next steps

The report must explicitly say:

> This review does not compare one investment object's raw factor values against another investment object's raw factor values.

## Beginner Glossary

The report must explain these terms:

- Factor: a numeric feature calculated for one investment object on one date.
- Correlation: a number showing whether two factors usually move together or opposite each other.
- Redundant factor: two factors are redundant if they mostly tell the same story.
- Mirror factor: high values of one factor usually mean low values of another factor.
- Derived factor: a factor created from another factor, such as today's value minus the value 20 trading days ago.
- Investment object: the thing being analyzed independently, such as one stock code, ETF, futures contract, or crypto asset.
- Cross-object summary: a summary of patterns found separately inside multiple investment objects.

## Formula Hints For Current Chip Factors

Initial hints:

```json
[
  {
    "family_id": "profit_loss_ratio_family",
    "factors": [
      "profit_ratio_asof",
      "loss_ratio_asof"
    ],
    "relationship_type": "formula_level_relationship",
    "formula_evidence": "profit_ratio_asof + loss_ratio_asof + at_close_ratio ~= total chip percent",
    "default_primary_hint": "loss_ratio_asof"
  },
  {
    "family_id": "profit_loss_delta_family",
    "factors": [
      "profit_ratio_delta_20d",
      "loss_ratio_delta_20d"
    ],
    "relationship_type": "formula_level_relationship",
    "formula_evidence": "20-day profit-ratio and loss-ratio changes are mirror changes when at-close mass is small",
    "default_primary_hint": "loss_ratio_delta_20d"
  },
  {
    "family_id": "cost_position_family",
    "factors": [
      "cyq_cgo_asof",
      "weighted_chip_cost_gap_asof"
    ],
    "relationship_type": "concept_related",
    "formula_evidence": "Both compare current price with the chip cost distribution; cyq_cgo_asof is marked as proxy while weighted_chip_cost_gap_asof is exact project factor-family implementation.",
    "default_primary_hint": "weighted_chip_cost_gap_asof"
  }
]
```

These hints should guide classification and explanation, not override observed per-instrument evidence.

## Implementation Plan

### Phase 1: Design Review

Deliver this document and review it before coding.

Review gates:

- instrument isolation is explicit
- cross-object summary is aggregation-only
- output artifacts are reproducible
- decisions have deterministic tie-breakers
- pooling artifact risk is tested

### Phase 2: Test-First Prototype

Add tests before implementation.

Suggested tests:

- same-direction duplicate detection inside one instrument
- opposite-direction duplicate detection inside one instrument
- low observation count returns `NO_DECISION`
- formula-level relationship appears in pair evidence and report
- `asof` vs `delta_20d` is classified as derived or related, not auto-excluded
- two instruments with identical values never generate cross-instrument factor pairs
- high pooled raw correlation with weak per-instrument evidence emits `pooling_artifact_risk`
- one instrument redundant and another not redundant does not produce global `EXCLUDE`
- output schemas include `instrument_id` or `scope`
- malformed input fails with a clear error

### Phase 3: Local Script

Implement:

```text
scripts/factor_redundancy_review.py
```

First command:

```text
python scripts/factor_redundancy_review.py \
  --factor-batch-summary docs/factor-batches/factor-batch-2026q1-core5-live-combined/factor-batch-summary.json \
  --output-dir docs/factor-redundancy-reviews/<review-id>
```

### Phase 4: E2E Review Run

Run against:

```text
docs/factor-batches/factor-batch-2026q1-core5-live-combined/factor-batch-summary.json
```

Expected first-pass relationships to verify:

- `profit_ratio_asof` vs `loss_ratio_asof` should be a strong mirror candidate.
- `profit_ratio_delta_20d` vs `loss_ratio_delta_20d` should be a strong mirror candidate.
- `cyq_cgo_asof` vs `weighted_chip_cost_gap_asof` should be a strong same-direction candidate.

These expectations must be checked per instrument and then summarized.

### Phase 5: Reusable Skill Extraction

After the local script is stable, create a reusable skill:

```text
~/.agents/skills/factor-redundancy-review/SKILL.md
```

or a project-local skill if the repo chooses to keep it near the code.

The skill should explain:

- when to use it
- required inputs
- safe multi-instrument methodology
- how to run the script
- how to interpret artifacts
- how to hand results to later strategy agents

## Acceptance Criteria

The work is complete only when:

- this design has been reviewed
- tests cover the core classification and isolation rules
- the script runs against the current 13 factors
- all required artifacts are generated
- every per-pair relationship is scoped to one instrument or a cross-object summary
- no raw correlation sample mixes investment objects
- cross-object conclusions aggregate per-instrument evidence only
- reports explain decisions for non-experts
- source artifacts are not modified
- ECC Artifact Reviewer or equivalent quality review passes on the generated redundancy artifacts

## Design Review Verdict

Verdict: proceed to test-first prototype only if the implementation keeps this document's scoping model intact.

Blocking issues for any proposed implementation:

- Any pairwise classifier that accepts a mixed-instrument raw sample.
- Any global recommendation that directly overwrites per-instrument decisions.
- Any artifact that omits both `instrument_id` and `scope`.
- Any formula relationship that is presented as verified data evidence when it is only a metadata hint.
- Any test suite that lacks an adversarial cross-instrument leakage case.

## Strict Mentor Review

This plan is better than a simple pooled-correlation design, but the main failure modes are still serious.

First, the implementation must resist the temptation to treat a larger pooled sample as "more statistically reliable." In this context, pooled raw data can be less reliable because it can turn investment-object differences into fake factor relationships.

Second, global recommendations must stay humble. A factor pair that is redundant in most instruments is useful information, but it is not automatic permission to erase the factor everywhere. The output should prefer per-instrument decisions and cross-object warnings unless formula evidence is strong and stable.

Third, formula hints must be auditable. If a hint is only metadata, label it as metadata. If the script verifies it from snapshots, record the source rows and residual tolerance. Mixing those two evidence levels would make the report sound more certain than the data supports.

Fourth, the tie-breakers must be deterministic. If the system says "keep the easier-to-explain factor," it must explain why that factor won. Otherwise the skill becomes an opinion generator instead of a review tool.

Fifth, the tests must include adversarial multi-instrument cases. Happy-path correlation tests are not enough. The most important bug to catch is accidental cross-object leakage.

The hard bar for implementation readiness is:

> No factor relationship may be classified using raw observations from different investment objects in the same correlation sample.

and:

> Cross-object conclusions must be evidence aggregation over per-instrument reviews, not pooled raw-data correlation.
