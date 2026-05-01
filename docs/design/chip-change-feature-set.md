# ChipChangeFeatureSet Design

## Status

Draft created on 2026-04-29. No code has been implemented from this design yet.

This document defines the proposed feature layer for analyzing **daily changes in existing Tushare chip detail rows**. It builds on the traceability research in [chip-change-strategy-traceability.md](../references/chip-change-strategy-traceability.md).

## Goal

Create a source-traceable `ChipChangeFeatureSet` that converts daily chip detail rows and daily price bars into stable features for later strategy comparison.

This layer should not directly output `BUY`, `HOLD`, or `SELL`. It should only produce explainable features that future candidate strategies can consume and backtest.

## Non-Goals

- Do not recalculate CYQ from turnover and K-line data.
- Do not add a machine-learning model in this phase.
- Do not tune thresholds from intuition.
- Do not promote any candidate rule to a baseline without backtest results.

## Inputs

Required normalized inputs already exist in the project data contract:

```python
class ChipDistributionPoint:
    ts_code: str
    trade_date: str
    price: float
    percent: float

class DailyPriceBar:
    ts_code: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    pre_close: float | None
    pct_chg: float | None
    vol: float | None
    amount: float | None
```

Window semantics:

- `analysis_days`: the first `N` resolved trading days from the selected start date.
- `signal_date`: the `N`th trading day.
- Feature deltas are calculated from the first available chip date in the analysis window to `signal_date`.
- Future observation windows stay outside this feature layer and remain part of backtest scoring. The target observation offsets are `N+1`, `N+3`, `N+5`, `N+15`, `N+30`, `N+60`, `N+90`, and `N+180`; unavailable future trading days are recorded as `N/A`.

## Output Shape

Proposed immutable domain model:

```python
class ChipChangeFeatureSet:
    ts_code: str
    start_date: str
    end_date: str
    daily: list[DailyChipSnapshot]
    deltas: ChipChangeDeltas
    quality: ChipChangeQuality
    traceability: dict[str, FeatureTrace]
```

Daily snapshot:

```python
class DailyChipSnapshot:
    trade_date: str
    close: float
    chip_rows: int
    weighted_chip_cost: float | None
    dominant_peak_price: float | None
    dominant_peak_percent: float | None
    profit_ratio: float | None
    loss_ratio: float | None
    concentration_width_70: float | None
    concentration_width_90: float | None
    weighted_std: float | None
```

Window deltas:

```python
class ChipChangeDeltas:
    price_return: float | None
    weighted_chip_cost_delta: float | None
    dominant_peak_price_delta: float | None
    dominant_peak_percent_delta: float | None
    profit_ratio_delta: float | None
    loss_ratio_delta: float | None
    concentration_width_70_delta: float | None
    concentration_width_90_delta: float | None
    weighted_std_delta: float | None
    retained_low_chip_ratio: float | None
    high_chip_accumulation_ratio: float | None
```

Quality:

```python
class ChipChangeQuality:
    status: Literal["OK", "PARTIAL", "ERROR"]
    expected_days: int
    chip_days: int
    price_days: int
    missing_chip_dates: list[str]
    missing_price_dates: list[str]
    warnings: list[str]
```

Trace:

```python
class FeatureTrace:
    evidence_level: Literal[
        "DIRECT_REPORT",
        "OPEN_REPRODUCTION",
        "FACTOR_FAMILY",
        "HEURISTIC",
        "PROJECT_HYPOTHESIS",
    ]
    source: str
    source_url: str | None
    project_interpretation: str
```

## Daily Feature Formulas

Use one `trade_date` of chip rows plus that date's close.

### Weighted Chip Cost

Formula:

```text
weighted_chip_cost = sum(price_i * percent_i) / sum(percent_i)
```

Null behavior:

- Return `None` when there are no chip rows.
- Return `None` when `sum(percent_i) <= 0`.

Traceability:

- Source family: chip distribution / cost distribution research.
- Evidence level: `FACTOR_FAMILY`.
- Existing project precedent: current baseline already calculates weighted chip cost.

### Dominant Peak Price And Percent

Formula:

```text
dominant_peak = chip row with max(percent_i)
dominant_peak_price = dominant_peak.price
dominant_peak_percent = dominant_peak.percent
```

Tie behavior:

- If multiple buckets share the same max percent, choose the bucket closest to daily close.
- If still tied, choose the lower price bucket for conservative sell-pressure interpretation.

Traceability:

- Source family: CYQ/chip distribution peak interpretation.
- Evidence level: `FACTOR_FAMILY`.

### Profit Ratio

Formula:

```text
profit_ratio = sum(percent_i where price_i < close)
```

Interpretation:

- Approximate share of chips with cost below current close.
- This is the project's "winner chips" ratio.

Traceability:

- Source family: chip distribution factor research using profit/loss level chip proportions.
- Evidence level: `FACTOR_FAMILY`.

### Loss Ratio

Formula:

```text
loss_ratio = sum(percent_i where price_i > close)
```

Interpretation:

- Approximate share of chips with cost above current close.
- This is the project's "trapped chips" ratio.

Traceability:

- Source family: chip distribution factor research using profit/loss level chip proportions.
- Evidence level: `FACTOR_FAMILY`.

### Concentration Width 70 And 90

Preferred formula:

1. Sort chip buckets by `price`.
2. Use a sliding window over sorted buckets.
3. Find the narrowest price range where cumulative `percent >= target`.
4. Normalize by close:

```text
concentration_width_target = (upper_price - lower_price) / close
```

Targets:

- `target = 70`
- `target = 90`

Interpretation:

- Lower value means chips are more concentrated.
- Higher value means chips are more dispersed.

Traceability:

- Source: chip concentration concepts in public quant summaries and broker factor reproductions.
- Evidence level: `OPEN_REPRODUCTION` for the factor family, `PROJECT_HYPOTHESIS` for this exact implementation detail.

### Weighted Standard Deviation

Formula:

```text
mean = weighted_chip_cost
weighted_variance = sum(percent_i * (price_i - mean)^2) / sum(percent_i)
weighted_std = sqrt(weighted_variance) / close
```

Interpretation:

- Alternative chip dispersion measure.
- Used as a robustness check against concentration-width instability.

Traceability:

- Source family: shape/statistical chip distribution factors.
- Evidence level: `FACTOR_FAMILY`.

## Window Delta Formulas

Use `first_snapshot` and `last_snapshot` in the analysis window.

### Price Return

```text
price_return = (last_close - first_close) / first_close
```

Null behavior:

- Return `None` if either close is missing or `first_close == 0`.

### Simple Deltas

For ratio-like fields:

```text
field_delta = last_value - first_value
```

Fields:

- `dominant_peak_percent_delta`
- `profit_ratio_delta`
- `loss_ratio_delta`
- `concentration_width_70_delta`
- `concentration_width_90_delta`
- `weighted_std_delta`

For price-like fields, normalize by first close:

```text
weighted_chip_cost_delta = (last_weighted_chip_cost - first_weighted_chip_cost) / first_close
dominant_peak_price_delta = (last_dominant_peak_price - first_dominant_peak_price) / first_close
```

### Retained Low Chip Ratio

Purpose:

- Approximate whether early-window low-cost chips remain by the signal date.

Traceability:

- Primary source: 华西证券留存筹码比率 concept.
- Evidence level: `DIRECT_REPORT` for retained-chip factor direction, `PROJECT_HYPOTHESIS` for this approximation from daily Tushare chip rows.

Proposed approximation:

1. On `first_snapshot`, define the low-cost anchor as prices at or below the first weighted chip cost.
2. Define a low-cost bucket range:

```text
low_range_upper = first_weighted_chip_cost
low_range_lower = min(first day chip price)
```

3. Calculate first and last low-range chip percentages:

```text
first_low_ratio = sum(first_percent_i where low_range_lower <= price_i <= low_range_upper)
last_low_ratio = sum(last_percent_i where low_range_lower <= price_i <= low_range_upper)
```

4. Retention:

```text
retained_low_chip_ratio = last_low_ratio / first_low_ratio
```

Null behavior:

- Return `None` if `first_low_ratio <= 0`.

Important limitation:

- This is not exact retained-chip accounting. Tushare chip rows are daily distribution estimates, not transaction-level holder records.

### High Chip Accumulation Ratio

Purpose:

- Detect whether new high-cost concentration appears near or above the signal close.

Traceability:

- Source family: chip distribution shape/proportion factors.
- Evidence level: `PROJECT_HYPOTHESIS`.

Proposed approximation:

```text
high_range_lower = max(first_weighted_chip_cost, first_close)
high_range_upper = max(last day chip price)
first_high_ratio = sum(first_percent_i where price_i >= high_range_lower)
last_high_ratio = sum(last_percent_i where price_i >= high_range_lower)
high_chip_accumulation_ratio = last_high_ratio - first_high_ratio
```

Interpretation:

- Positive value means more chips moved into upper-cost areas.
- It is not automatically bearish; it becomes sell-risk only when paired with low-chip loss or weakening price action.

## Traceability Requirements

Every feature returned by `ChipChangeFeatureSet` must include a trace entry.

Minimum required trace entries:

| Feature | Evidence level | Source |
| --- | --- | --- |
| `retained_low_chip_ratio` | `DIRECT_REPORT` plus `PROJECT_HYPOTHESIS` | 华西证券留存筹码比率 report |
| `profit_ratio_delta` | `FACTOR_FAMILY` | Chip distribution factor research summaries |
| `loss_ratio_delta` | `FACTOR_FAMILY` | Chip distribution factor research summaries |
| `concentration_width_70_delta` | `OPEN_REPRODUCTION` plus `PROJECT_HYPOTHESIS` | QuantsPlaybook / JoinQuant reproduction of Guangfa chip distribution factor |
| `dominant_peak_price_delta` | `FACTOR_FAMILY` plus `PROJECT_HYPOTHESIS` | CYQ/chip distribution peak concepts |
| `high_chip_accumulation_ratio` | `PROJECT_HYPOTHESIS` | Project-derived from chip distribution shape concepts |

## Missing Data Rules

Feature extraction should be conservative:

- If a daily close is missing, that date cannot produce a daily snapshot.
- If chip rows are missing for a date, that date cannot produce chip features.
- If either first or last snapshot is missing, all deltas must be `None`.
- If fewer than 70% of requested analysis days have both price and chip data, quality is `ERROR`.
- If 70%-99% of requested days have both price and chip data, quality is `PARTIAL`.
- If all requested days have both price and chip data, quality is `OK`.
- Do not forward-fill chip distributions.
- Do not fabricate missing chip buckets.

## API Exposure Direction

Future backend responses should expose this as a separate block from strategy signals:

```json
{
  "chip_change_features": {
    "daily": [],
    "deltas": {},
    "quality": {},
    "traceability": {}
  }
}
```

The frontend should initially show:

- start and end snapshot summary,
- delta table,
- quality warnings,
- evidence level labels for each feature.

It should not show a final "this source says buy" message. Strategy rules should remain separate.

## Backtest Scoring Direction

Feature extraction itself is not scored. Candidate strategies built on these features should be scored later.

Minimum scoring per strategy:

| Signal | Multi-horizon scoring idea |
| --- | --- |
| `BUY` | Match if forward return is positive; stronger match if return beats a configurable threshold. |
| `SELL` | Match if forward return is negative; stronger match if drawdown or negative return appears. |
| `HOLD` | Do not score as win/loss by default. Track realized absolute return and drawdown separately. |

Current implementation detail:

- `BUY`: `period_return > 0` is `MATCH`; `period_return <= 0` is `MISMATCH`; directional score is `period_return`.
- `SELL`: `period_return < 0` is `MATCH`; `period_return >= 0` is `MISMATCH`; directional score is `-period_return`.
- `HOLD`: always labeled `NEUTRAL` because it makes no directional claim; directional score is `-abs(period_return)` as an opportunity-cost or movement penalty.
- `N/A`: used when the future trading day for an offset does not exist in the resolved data; excluded from average directional score.

This `NEUTRAL` rule is a project scoring convention, not a broker-paper threshold. Future strategy research should decide whether `HOLD` deserves a separate quality score based on avoided drawdown, low volatility, or opportunity cost.

Suggested validation metrics:

- match rate by horizon,
- average forward return by signal,
- median forward return by signal,
- worst drawdown by signal,
- signal coverage rate,
- false positive rate for `BUY` and `SELL`,
- stability by stock and market regime.

## Implementation Sequence

1. Add fixture-based unit tests for daily snapshot formulas.
2. Add fixture-based unit tests for window deltas.
3. Implement pure feature extraction with no Tushare calls.
4. Wire extraction into scan/backtest services as optional response data.
5. Render features in the frontend without changing current strategy output.
6. Add candidate strategy modules only after feature extraction is visible and testable.

## Open Decisions

- Whether concentration width should use raw price width or close-normalized width in the UI.
- Whether low-cost retention should anchor to first weighted cost, first dominant peak, or a percentile bucket.
- Whether high-chip accumulation should use first close, last close, or first weighted cost as the lower bound.
- Whether feature deltas should be absolute percentage-point changes or normalized rates of change.
- Whether thresholds should be global constants, per-stock rolling percentiles, or discovered through offline grid search.
