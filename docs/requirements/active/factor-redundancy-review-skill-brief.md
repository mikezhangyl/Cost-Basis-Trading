# Factor Redundancy Review Skill Brief

This document is a handoff brief for a new Codex chat. Its purpose is to continue the design and implementation of a reusable skill named `factor-redundancy-review`.

## New Chat Starting Prompt

Copy this section into a new chat:

```text
我们要设计并实现一个可复用的 Codex skill：factor-redundancy-review。

背景：
我在做一个量化研究项目 Cost-Basis-Trading。当前项目已经计算出 13 个筹码相关因子。这里的“因子”指“某只股票在某一天计算出来的一个数值”。同一只股票在一段时间内会形成“日期 × 因子”的二维数据；多只股票则形成“股票 × 日期 × 因子”的数据。

当前问题：
多个因子之间可能表达重复信息。例如 profit_ratio_asof 和 loss_ratio_asof 很可能近似反向重复，因为赚钱筹码比例、亏钱筹码比例和收盘价附近筹码比例加起来约等于全部筹码。我们不希望后续策略 agent 把同一个信号重复计算两次，导致过度自信。

目标：
创建一个可复用 skill，帮助 agent 对一批量化因子做去冗余审查。这个 skill 不做交易建议，只做因子清洗、因子分组、保留/排除/降权建议，并生成可追溯 artifacts。

请先阅读本项目中的这些文件：
- backend/app/factors/chip_factors.py
- docs/design/chip-factor-production-plan-2026q1.md
- docs/factor-batches/factor-batch-2026q1-core5-live-combined/factor-batch-summary.json
- docs/factor-batches/factor-batch-2026q1-core5-live-combined/aggregate-factor-report.md

执行要求：
1. 先设计，不要直接写代码。
2. 设计文档落盘。
3. 设计通过后再实现。
4. 遵循 ECC 规范。
5. 使用测试驱动方式，关键判断规则必须有测试。
6. 产物必须可追溯。
7. 面向金融新手解释专业术语。
8. 第一版可以在 Cost-Basis-Trading 里跑通 E2E，但 skill 设计要尽量通用，未来可以复用到别的量化项目。
```

## Skill Name

Recommended name:

```text
factor-redundancy-review
```

Reason:

- `factor` means quantitative factor.
- `redundancy` means duplicate or overlapping information.
- `review` means this skill audits and explains, rather than blindly deletes factors.

Avoid naming it only `factor-deduplication`, because the skill should do more than delete duplicates. It should classify factor relationships and recommend keep, exclude, downweight, or keep-with-warning.

## Plain-Language Goal

The skill should answer:

> Among these factors, which ones are saying the same thing, which ones are saying opposite versions of the same thing, and which ones should we keep for strategy research?

This matters because two duplicate factors can make later strategy agents overcount the same evidence.

Example:

```text
profit_ratio_asof 很高
loss_ratio_asof 很低
```

This may look like two independent signals, but it may actually be one signal stated twice.

## Current Project Context

The current project has 13 factor definitions:

```text
chip_weighted_std_asof
concentration_width_70_asof
concentration_width_70_delta_20d
concentration_width_90_asof
cyq_cgo_asof
dominant_peak_price_delta_20d
dominant_peak_strength_asof
loss_ratio_asof
loss_ratio_delta_20d
profit_ratio_asof
profit_ratio_delta_20d
weighted_chip_cost_delta_20d
weighted_chip_cost_gap_asof
```

Important distinction:

- Factor definition: the metric name, such as `profit_ratio_asof`.
- Factor value: one stock on one date, such as `000001.SZ` on `2026-01-05` with `profit_ratio_asof = 62.0`.

## Required Relationship Categories

The skill should classify factor pairs or groups into these categories.

### 1. Same-Direction Duplicate

Two factors usually move together.

Example:

```text
correlation ~= +1.0
```

Plain-language meaning:

> When factor A is high, factor B is also high. They may be saying almost the same thing.

### 2. Opposite-Direction Duplicate

Two factors usually move in opposite directions.

Example:

```text
correlation ~= -1.0
```

Plain-language meaning:

> When factor A is high, factor B is low. They may be mirror images of the same information.

Example candidate:

```text
profit_ratio_asof vs loss_ratio_asof
```

### 3. Formula-Level Relationship

Some factors are related because of their formulas, not only because historical data happens to be correlated.

Example:

```text
profit_ratio + loss_ratio + at_close_ratio ~= total chip percent
```

Plain-language meaning:

> These factors are mathematically connected, so we should not treat them as fully independent evidence.

### 4. Derived But Not Duplicate

Some factors are related but should not be automatically removed.

Example:

```text
profit_ratio_asof
profit_ratio_delta_20d
```

Plain-language meaning:

- `profit_ratio_asof` means current state.
- `profit_ratio_delta_20d` means 20-day change.

They come from the same base concept, but one is a current snapshot and the other is a trend/change signal.

### 5. Complementary Factors

Some factors may be correlated but still useful together because they describe different aspects of the stock state.

Example:

```text
weighted_chip_cost_gap_asof
concentration_width_70_asof
```

Plain-language meaning:

- One asks: where is price compared with average holder cost?
- The other asks: are holder costs concentrated or scattered?

## Required Outputs

The skill should generate these artifacts for each review run.

### `factor-correlation-matrix.csv`

A matrix of pairwise factor correlations.

Required columns / structure:

```text
factor_id rows
factor_id columns
correlation values
```

The exact format can be a standard square CSV matrix.

### `factor-pair-relationships.json`

Machine-readable pairwise classification.

Suggested structure:

```json
[
  {
    "factor_a": "profit_ratio_asof",
    "factor_b": "loss_ratio_asof",
    "relationship_type": "opposite_direction_duplicate",
    "correlation": -0.98,
    "formula_evidence": "profit_ratio + loss_ratio + at_close_ratio ~= total chip percent",
    "recommendation": "keep_one",
    "plain_language_explanation": "这两个因子基本是在用相反方向描述赚钱筹码和亏钱筹码。"
  }
]
```

### `factor-redundancy-groups.json`

Groups of factors that belong to the same information family.

Suggested structure:

```json
[
  {
    "group_id": "profit_loss_ratio_family",
    "group_type": "formula_related",
    "factors": [
      "profit_ratio_asof",
      "loss_ratio_asof"
    ],
    "recommended_primary_factor": "loss_ratio_asof",
    "excluded_or_downweighted_factors": [
      "profit_ratio_asof"
    ],
    "reason": "Both describe the distribution of profitable vs trapped chips; keeping both may double count the same signal."
  }
]
```

### `factor-retention-decisions.json`

Final keep / exclude / downweight / keep-with-warning decisions.

Suggested decisions:

```text
KEEP
EXCLUDE
DOWNWEIGHT
KEEP_WITH_WARNING
```

Suggested structure:

```json
[
  {
    "factor_id": "loss_ratio_asof",
    "decision": "KEEP",
    "reason": "More directly interpretable as overhead trapped-chip pressure.",
    "related_factors": [
      "profit_ratio_asof"
    ]
  },
  {
    "factor_id": "profit_ratio_asof",
    "decision": "EXCLUDE",
    "reason": "Highly opposite to loss_ratio_asof and likely duplicates the same information family.",
    "replacement_factor": "loss_ratio_asof"
  }
]
```

### `factor-redundancy-report.md`

Human-readable report for non-experts.

Must include:

- What was reviewed.
- How many factors were reviewed.
- What data range was used.
- Which factors are duplicates.
- Which factors are mirror-image duplicates.
- Which factors are derived but still useful.
- Which factors are recommended to keep.
- Which factors are recommended to exclude or downweight.
- Plain-language explanation for each important decision.
- Limitations and next steps.

### `review-events.jsonl`

Trace log of the review process.

Each line should be a JSON object.

Suggested events:

```text
review_started
load_factor_data_started
load_factor_data_completed
correlation_matrix_started
correlation_matrix_completed
relationship_classification_started
relationship_classification_completed
retention_decision_started
retention_decision_completed
write_artifacts_completed
review_completed
```

## Required Inputs

The skill should not be hardcoded to Cost-Basis-Trading paths.

It should accept configurable inputs:

```text
factor data path
factor metadata path, optional
output directory
correlation threshold, default 0.90 or 0.95
minimum observation count
method: pearson / spearman
```

First implementation can support JSONL factor files from this project, but the skill design should allow future adapters.

## Suggested First E2E Input

Use the current project's real factor artifacts:

```text
docs/factor-runs/factor-run-000001-20260101-20260430-live/stocks/000001.SZ/factors.jsonl
```

And/or combined batch artifacts:

```text
docs/factor-batches/factor-batch-2026q1-core5-live-combined/factor-batch-summary.json
```

For better redundancy detection across stocks, prefer using all completed factor runs in:

```text
docs/factor-batches/factor-batch-2026q1-core5-live-combined/factor-batch-summary.json
```

Each completed stock result points to a `factor_run_dir`, and each factor run has stock-level `factors.jsonl`.

## Quantitative Checks

The skill should compute these checks.

### Correlation

Compute pairwise correlation between factor values.

Recommended:

- Pearson correlation for linear similarity.
- Spearman correlation for rank similarity if practical.

Plain-language explanation:

> Correlation measures whether two factors move together. `+1` means almost same direction. `-1` means almost mirror image. `0` means little linear relationship.

### Absolute Correlation Threshold

Suggested default:

```text
abs(correlation) >= 0.90
```

Meaning:

> If two factors have correlation above 0.90 or below -0.90, they should be reviewed as potentially redundant.

Do not automatically delete only based on threshold. The skill should generate a review recommendation.

### Minimum Observation Count

Do not judge a pair if there are too few overlapping observations.

Suggested default:

```text
minimum overlapping observations >= 30
```

Reason:

> If two factors only overlap for a few days, the correlation can be accidental.

### Formula Evidence

The skill should allow formula-level hints.

For the current 13 chip factors, include at least:

```text
profit_ratio_asof + loss_ratio_asof + at_close_ratio ~= total chip percent
```

Even if `at_close_ratio` is not one of the final 13 factors, it exists in snapshot logic and explains why `profit_ratio_asof` and `loss_ratio_asof` may be mirror-related.

## Current Factor Family Hints

Use these as initial human knowledge, but validate with data.

### Profit / Loss Family

Potentially redundant:

```text
profit_ratio_asof
loss_ratio_asof
```

Related but not automatically duplicate:

```text
profit_ratio_delta_20d
loss_ratio_delta_20d
```

### Cost Position Family

Related:

```text
cyq_cgo_asof
weighted_chip_cost_gap_asof
```

They both compare current price against cost distribution. They may be highly related and require review.

### Concentration Family

Related:

```text
concentration_width_70_asof
concentration_width_90_asof
chip_weighted_std_asof
dominant_peak_strength_asof
```

They all describe concentration or dispersion, but they may capture different details. Do not blindly delete without explanation.

### Change / Delta Family

Related to their base factors:

```text
profit_ratio_delta_20d
loss_ratio_delta_20d
weighted_chip_cost_delta_20d
concentration_width_70_delta_20d
dominant_peak_price_delta_20d
```

These describe 20-trading-day changes. They should usually not be removed just because they are related to `asof` factors.

## Decision Rules

The skill should produce recommendations using these principles.

### Keep One

Use when:

- Two factors are highly same-direction or opposite-direction correlated.
- They have formula-level or concept-level duplication.
- One is easier to explain or has better data quality.

### Keep Both With Warning

Use when:

- Two factors are correlated but not identical.
- They represent related but distinct concepts.
- More data is needed before excluding either one.

### Downweight

Use when:

- A factor may be useful but overlaps with a stronger factor.
- Later strategy agents should avoid counting it as fully independent evidence.

### Exclude

Use when:

- A factor is almost fully redundant.
- It has lower interpretability or poorer data quality.
- There is a clear replacement factor.

## Beginner-Friendly Explanation Requirements

Every report must explain professional terms.

Required examples:

### Factor

Explain as:

> A factor is a numeric feature calculated for one stock on one date.

### Correlation

Explain as:

> Correlation tells whether two factors usually move together or opposite each other.

### Redundant Factor

Explain as:

> Two factors are redundant if they mostly tell the same story.

### Mirror Factor

Explain as:

> A mirror factor is one where high values of one factor usually mean low values of another factor.

### Derived Factor

Explain as:

> A derived factor is created from another factor, such as today's value minus the value 20 trading days ago. It may still be useful because it describes change rather than current state.

## ECC Requirements

Follow the repository's ECC workflow:

1. Plan before implementation.
2. Use TDD for new logic.
3. Keep artifacts traceable.
4. Avoid hardcoded secrets.
5. Validate inputs at boundaries.
6. Do not mutate existing immutable run artifacts.
7. Add focused tests for each important rule.
8. Run quality checks before commit.
9. Use ECC Artifact Reviewer or an equivalent review packet for generated factor-redundancy artifacts once implemented.

## Proposed Implementation Phases

### Phase 1: Design

Deliver:

```text
docs/design/factor-redundancy-review-skill.md
```

The design should include:

- Purpose
- Inputs
- Outputs
- Relationship categories
- Decision rules
- Artifact layout
- Test plan
- E2E plan

### Phase 2: Project Script Prototype

Implement a local script first, before extracting to a reusable skill.

Suggested script:

```text
scripts/factor_redundancy_review.py
```

Suggested command:

```text
python scripts/factor_redundancy_review.py \
  --factor-run-dirs ... \
  --output-dir docs/factor-redundancy-reviews/<review-id>
```

Alternative input:

```text
python scripts/factor_redundancy_review.py \
  --factor-batch-summary docs/factor-batches/factor-batch-2026q1-core5-live-combined/factor-batch-summary.json \
  --output-dir docs/factor-redundancy-reviews/<review-id>
```

### Phase 3: Tests

Add tests for:

- Same-direction duplicate detection.
- Opposite-direction duplicate detection.
- Low observation count should not be trusted.
- Formula-level relationship should be included in report.
- `asof` vs `delta_20d` should be classified as derived, not auto-excluded.
- Outputs are written with expected schema.
- Missing or malformed input gives clear error.

### Phase 4: E2E With Current 13 Factors

Run the review on the current completed batch:

```text
docs/factor-batches/factor-batch-2026q1-core5-live-combined/factor-batch-summary.json
```

Expected output directory:

```text
docs/factor-redundancy-reviews/<review-id>
```

### Phase 5: Skill Extraction

After the project script is stable, extract the workflow into a reusable Codex skill.

Recommended skill location depends on the environment:

```text
~/.agents/skills/factor-redundancy-review/SKILL.md
```

or project-local skill directory if this repo keeps project-specific skills.

The skill should describe:

- When to use it.
- Required inputs.
- How to run the project script or equivalent logic.
- How to interpret outputs.
- How to hand results to later strategy agents.

## Acceptance Criteria

The work is complete when:

1. A design document exists.
2. The redundancy review can run against the current 13 factors.
3. It generates all required artifacts.
4. It explains results in beginner-friendly language.
5. It identifies likely duplicate or mirror factor relationships.
6. It does not blindly remove `delta_20d` factors only because they are related to `asof` factors.
7. Tests cover core relationship rules.
8. E2E output is stored locally and traceable.
9. ECC review or equivalent quality review has passed.

## Important Non-Goals

This skill must not:

- Give buy/sell/hold investment advice.
- Train a trading model.
- Decide final trading strategy.
- Delete source factor artifacts.
- Modify immutable factor run or factor batch outputs.

Its role is only:

> Review factor redundancy and produce a clean, explainable candidate factor set for later strategy research.
