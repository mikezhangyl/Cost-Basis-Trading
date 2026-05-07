# Chip Factor Production Plan For 2026-01-01 To 2026-04-30

## Status

Draft created on 2026-05-01.

This document resets the chip-factor design around professional factor-research conventions. It deliberately does not inherit earlier ad hoc assumptions such as "always use 10 days" or "always score only the hand-picked observation dates." Those settings can still exist in product experiments, but factor construction should be source-driven.

## Plain-Language Goal

We want to answer a simple question:

> On each trading day, what does the chip distribution say about the current stock state, and does that state help explain future returns?

For this phase, the first research range is:

```text
factor_date range: 2026-01-01 to 2026-04-30
market: A-share stocks available through Tushare
primary data: Tushare cyq_chips daily chip detail rows
price data: Tushare daily bars
```

`factor_date` means the day when the factor value is calculated. For example, `2026-03-13` can have a `profit_ratio_asof` value, a `concentration_width_70_asof` value, and a `retained_core_chip_ratio_20d_proxy` value.

## Important Window Rule

The user-facing research range is 2026-01-01 to 2026-04-30, but some factors need lookback history.

Example:

- A 20-day factor on 2026-01-15 needs roughly 20 prior trading days.
- A 100-day CGO-style factor on 2026-01-15 needs roughly 100 prior trading days.

Therefore the data collector should treat the date range as:

```text
factor output range: 2026-01-01 to 2026-04-30
warmup fetch range: earlier dates needed by each factor's lookback window
```

If we do not fetch warmup data, early January factor values should be marked `insufficient_history` instead of being forced.

## Source-Driven Design Principles

1. **Factor date first**
   - A factor value belongs to one stock and one `factor_date`.
   - `start_date` is derived from the factor lookback window, not the other way around.

2. **Lookback window belongs to the factor**
   - A 20-day retained-chip proxy uses a 20-trading-day lookback.
   - A 100-day CGO-style factor uses a longer lookback.
   - A same-day distribution shape factor uses only that day's chip distribution.

3. **Exact versus proxy must be explicit**
   - Some public factors require minute-level turnover or investor-level data.
   - If we approximate them with Tushare `cyq_chips`, the factor id must include `_proxy` or `cyq_`.

4. **No direct trading action at the factor layer**
   - The factor layer does not output `BUY`, `HOLD`, or `SELL`.
   - It outputs measurable values, quality flags, and traceability.

5. **Every factor needs a human explanation**
   - A user with little finance background should understand what the factor is trying to measure.

## Source References

| Source | Relevance | How It Is Used |
| --- | --- | --- |
| hugo2046/QuantsPlaybook | Public quant-research repo listing chip distribution factors and Guangfa-style reproductions. | Supports chip distribution factor family and public reproducibility direction. |
| JoinQuant reproduction of chip distribution factors | Public reproduction article referencing Guangfa chip distribution stock-selection research. | Supports separating factor calculation from strategy/backtest. |
| Huaxi Securities retained chip ratio report | Broker research defining retained chip ratio as a stock-selection factor. | Supports retained-chip factor direction; our implementation is a Tushare proxy. |
| Grinblatt and Han disposition-effect / CGO research | Academic basis for capital-gain overhang and investor unrealized gain/loss behavior. | Supports CGO-style chip gain/loss factors. |
| BigQuant chip distribution factor system summary | Public quant-platform summary of chip distribution factor families. | Supports shape, concentration, and distribution-statistic factor families. |

## Output Artifacts

Recommended first artifact layout:

```text
docs/factor-runs/<factor_run_id>/
  factor-run-config.json
  factor-run-manifest.json
  api-calls.jsonl
  stocks/
    <ts_code>/
      factors.parquet or factors.jsonl
      daily-chip-snapshots.jsonl
      factor-quality.json
      factor-traceability.json
```

For the existing research-run workflow, a single sample can also write:

```text
docs/research-runs/<run_id>/samples/<sample_id>/factors/
  chip_factor_set.json
  factor_traceability.json
  manifest.json
```

The first layout is better for real factor research across many dates. The second layout is useful for the current UI/research-run loop.

## Daily Chip Snapshot

Before calculating window factors, each stock/date should have a daily snapshot:

```json
{
  "ts_code": "000001.SZ",
  "factor_date": "20260313",
  "close": 11.45,
  "chip_rows": 99,
  "weighted_chip_cost": 10.82,
  "dominant_peak_price": 10.9,
  "dominant_peak_percent": 6.4,
  "profit_ratio": 72.5,
  "loss_ratio": 27.1,
  "concentration_width_70": 0.18,
  "concentration_width_90": 0.31,
  "chip_weighted_std": 0.11,
  "cyq_cgo": 0.054
}
```

## Factor Catalog For First Implementation

### 1. `profit_ratio_asof`

Plain-language explanation:

> This estimates how much of the market's chips are currently profitable. If many chips were bought below today's close, many holders are sitting on gains.

Formula:

```text
profit_ratio_asof = sum(percent where chip_price < close)
```

Example interpretation:

- `80` means roughly 80% of chips are below the current price and are in profit.
- This is not automatically bullish. Too many profitable holders may also create selling pressure if price momentum weakens.

Lookback:

```text
same-day snapshot
```

Source level:

```text
FACTOR_FAMILY
```

Implementation type:

```text
exact from Tushare cyq_chips snapshot
```

### 2. `loss_ratio_asof`

Plain-language explanation:

> This estimates how much of the market's chips are trapped above today's close. These holders are currently losing money and may sell when price rebounds to their cost.

Formula:

```text
loss_ratio_asof = sum(percent where chip_price > close)
```

Example interpretation:

- `65` means roughly 65% of chips have cost above the current price.
- A falling loss ratio can mean overhead pressure is being digested.

Lookback:

```text
same-day snapshot
```

Source level:

```text
FACTOR_FAMILY
```

Implementation type:

```text
exact from Tushare cyq_chips snapshot
```

### 3. `cyq_cgo_asof`

Plain-language explanation:

> This estimates the average unrealized gain or loss embedded in today's chip distribution. It is like asking: "On average, how far is the market's holding cost from today's price?"

Formula:

```text
cyq_cgo_asof = sum(percent_i * (close - chip_price_i) / close) / sum(percent_i)
```

Example interpretation:

- Positive value means the average chip is profitable.
- Negative value means the average chip is losing money.
- Very high positive values may imply profit-taking pressure; very negative values may imply trapped-chip pressure.

Lookback:

```text
same-day snapshot from Tushare cyq_chips
```

Source level:

```text
ACADEMIC_FACTOR_PROXY
```

Implementation type:

```text
proxy
```

Why proxy:

Traditional CGO is usually estimated from historical prices, volume, and turnover. We have Tushare's cost-distribution snapshot, so this factor is a CYQ-based approximation, not a claim to exactly reproduce the academic CGO formula.

### 4. `weighted_chip_cost_gap_asof`

Plain-language explanation:

> This tells whether today's close is above or below the market's weighted average chip cost. It is similar to comparing today's price with the average cost basis of holders.

Formula:

```text
weighted_chip_cost = sum(chip_price_i * percent_i) / sum(percent_i)
weighted_chip_cost_gap_asof = close / weighted_chip_cost - 1
```

Example interpretation:

- `0.05` means close is about 5% above weighted chip cost.
- `-0.05` means close is about 5% below weighted chip cost.

Lookback:

```text
same-day snapshot
```

Source level:

```text
FACTOR_FAMILY
```

Implementation type:

```text
exact from Tushare cyq_chips snapshot
```

### 5. `dominant_peak_strength_asof`

Plain-language explanation:

> This measures how much of the chip distribution is concentrated at the biggest cost peak. A strong peak means many holders have similar cost.

Formula:

```text
dominant_peak_strength_asof = max(percent_i)
```

Example interpretation:

- Higher value means one cost zone dominates the distribution.
- A strong peak can act like an important cost area, but it is not automatically support or resistance without price context.

Lookback:

```text
same-day snapshot
```

Source level:

```text
FACTOR_FAMILY
```

Implementation type:

```text
exact from Tushare cyq_chips snapshot
```

### 6. `concentration_width_70_asof`

Plain-language explanation:

> This asks: "How wide is the narrowest price band that contains 70% of all chips?" A narrower band means chips are more concentrated.

Formula:

```text
Sort chip buckets by price.
Find the narrowest [low_price, high_price] band where cumulative percent >= 70.
concentration_width_70_asof = (high_price - low_price) / close
```

Example interpretation:

- Lower value means holders' costs are packed into a tighter range.
- Higher value means holders' costs are spread out.

Lookback:

```text
same-day snapshot
```

Source level:

```text
OPEN_REPRODUCTION / FACTOR_FAMILY
```

Implementation type:

```text
project implementation of public chip-concentration factor family
```

### 7. `concentration_width_90_asof`

Plain-language explanation:

> This is the same idea as the 70% concentration width, but it asks for the band that contains 90% of chips. It gives a broader view of total chip dispersion.

Formula:

```text
concentration_width_90_asof = narrowest 90% chip price band / close
```

Example interpretation:

- Useful as a stability check for `concentration_width_70_asof`.
- If 70% is narrow but 90% is very wide, the stock may have a core cost area plus many outlier chips.

Lookback:

```text
same-day snapshot
```

Source level:

```text
OPEN_REPRODUCTION / FACTOR_FAMILY
```

Implementation type:

```text
project implementation of public chip-concentration factor family
```

### 8. `chip_weighted_std_asof`

Plain-language explanation:

> This measures how spread out chip costs are around the weighted average cost. It is a statistical way to describe whether holders' costs are clustered or scattered.

Formula:

```text
mean = weighted_chip_cost
variance = sum(percent_i * (chip_price_i - mean)^2) / sum(percent_i)
chip_weighted_std_asof = sqrt(variance) / close
```

Example interpretation:

- Lower value means chip costs are tighter.
- Higher value means chip costs are more scattered.

Lookback:

```text
same-day snapshot
```

Source level:

```text
FACTOR_FAMILY
```

Implementation type:

```text
exact statistical transform of Tushare cyq_chips snapshot
```

### 9. `profit_ratio_delta_20d`

Plain-language explanation:

> This measures whether profitable chips increased or decreased over the last 20 trading days. It compares the current profitable-chip share with the share 20 trading days ago.

Formula:

```text
profit_ratio_delta_20d = profit_ratio_asof[t] - profit_ratio_asof[t - 20 trading days]
```

Example interpretation:

- Positive value means more chips became profitable.
- Negative value means fewer chips are profitable than 20 trading days ago.

Lookback:

```text
20 trading days
```

Source level:

```text
FACTOR_FAMILY
```

Implementation type:

```text
exact delta from daily Tushare-derived snapshots
```

### 10. `loss_ratio_delta_20d`

Plain-language explanation:

> This measures whether trapped chips increased or decreased over the last 20 trading days. It is a simple way to track whether overhead pressure is building or being digested.

Formula:

```text
loss_ratio_delta_20d = loss_ratio_asof[t] - loss_ratio_asof[t - 20 trading days]
```

Example interpretation:

- Negative value means trapped chips shrank.
- Positive value means more chips are trapped above the current price.

Lookback:

```text
20 trading days
```

Source level:

```text
FACTOR_FAMILY
```

Implementation type:

```text
exact delta from daily Tushare-derived snapshots
```

### 11. `weighted_chip_cost_delta_20d`

Plain-language explanation:

> This measures whether the market's average holding cost moved up or down over the last 20 trading days.

Formula:

```text
weighted_chip_cost_delta_20d = weighted_chip_cost[t] / weighted_chip_cost[t - 20 trading days] - 1
```

Example interpretation:

- Positive value means the cost center moved upward.
- Negative value means the cost center moved downward.
- Upward cost migration with stable price may mean active turnover into higher cost areas; whether that is good or bad needs backtesting.

Lookback:

```text
20 trading days
```

Source level:

```text
FACTOR_FAMILY
```

Implementation type:

```text
exact delta from Tushare cyq_chips snapshots
```

### 12. `concentration_width_70_delta_20d`

Plain-language explanation:

> This measures whether the main 70% chip area became more concentrated or more scattered over the last 20 trading days.

Formula:

```text
concentration_width_70_delta_20d =
  concentration_width_70_asof[t] - concentration_width_70_asof[t - 20 trading days]
```

Example interpretation:

- Negative value means chips became more concentrated.
- Positive value means chips became more dispersed.

Lookback:

```text
20 trading days
```

Source level:

```text
OPEN_REPRODUCTION / FACTOR_FAMILY
```

Implementation type:

```text
project implementation of public chip-concentration factor family
```

### 13. `dominant_peak_price_delta_20d`

Plain-language explanation:

> This tracks whether the largest chip-cost peak moved up or down over the last 20 trading days.

Formula:

```text
dominant_peak_price_delta_20d =
  dominant_peak_price[t] / dominant_peak_price[t - 20 trading days] - 1
```

Example interpretation:

- Positive value means the main cost peak moved upward.
- Negative value means the main cost peak moved downward.
- This should be interpreted together with price movement.

Lookback:

```text
20 trading days
```

Source level:

```text
FACTOR_FAMILY
```

Implementation type:

```text
exact delta from Tushare cyq_chips snapshots
```

### 14. `retained_core_chip_ratio_20d_proxy`

Plain-language explanation:

> This estimates whether chips from the earlier core cost area are still present 20 trading days later. In simple terms: "Did the original important holders stay, or did that cost area disappear?"

Approximation formula:

```text
start_core_band = narrowest price band around t-20 dominant/core cost area
retained_core_chip_ratio_20d_proxy =
  sum(current percent where current chip_price is inside start_core_band)
```

Example interpretation:

- Higher value means the original core cost area is still visible.
- Lower value means the old core cost area has rotated away.

Lookback:

```text
20 trading days
```

Source level:

```text
DIRECT_REPORT + PROJECT_APPROXIMATION
```

Implementation type:

```text
proxy
```

Why proxy:

Huaxi's retained-chip ratio is not simply a same-day chip-distribution statistic. It tracks chips bought during a past period and retained by the selection date. Tushare `cyq_chips` gives daily cost-distribution snapshots, not investor-level turnover paths. This project factor is therefore a CYQ snapshot proxy for retained chips.

### 15. `high_chip_accumulation_20d_proxy`

Plain-language explanation:

> This estimates whether new chips are piling up at higher cost areas over the last 20 trading days. It is meant to detect whether recent buyers are concentrated at elevated prices.

Approximation formula:

```text
start_cost = weighted_chip_cost[t - 20 trading days]
high_cost_threshold = start_cost * 1.05
high_chip_accumulation_20d_proxy =
  sum(current percent where chip_price > high_cost_threshold)
```

Example interpretation:

- Higher value means more chips are concentrated above the earlier cost center.
- This may represent healthy upward handoff or risky high-level distribution. It must be tested against future returns.

Lookback:

```text
20 trading days
```

Source level:

```text
PROJECT_HYPOTHESIS based on chip-distribution factor family
```

Implementation type:

```text
proxy
```

## Data Quality Rules

Every factor row should include quality metadata.

```json
{
  "quality_status": "OK",
  "missing_reason": null,
  "required_lookback_days": 20,
  "available_lookback_days": 20,
  "chip_rows": 99,
  "price_available": true
}
```

Status values:

- `OK`: required chip and price data are available.
- `PARTIAL`: enough data exists for a cautious proxy, but history is incomplete.
- `INSUFFICIENT_HISTORY`: the factor cannot be calculated because the lookback window is missing.
- `MISSING_CHIP_DATA`: Tushare returned no chip rows for that factor date.
- `MISSING_PRICE_DATA`: no daily close exists for that factor date.

## Correctness Is The Primary Requirement

Factor correctness is more important than speed, UI polish, or adding more factors.

The implementation should not promote a factor until it passes all relevant validation layers:

1. **Formula fixture tests**
   - Build tiny hand-calculated chip distributions.
   - Verify each factor exactly matches the expected value.
   - Example: three chip buckets at prices 10, 11, 12 with known percents and close 11.5 should produce obvious profit/loss ratios.

2. **Invariants**
   - `profit_ratio_asof + loss_ratio_asof + at_close_ratio` should approximately equal total chip percent.
   - `concentration_width_70_asof <= concentration_width_90_asof`.
   - `chip_weighted_std_asof >= 0`.
   - Factor values that require prices must be null when close is missing.
   - Delta factors must be null or `INSUFFICIENT_HISTORY` when the lookback anchor is missing.

3. **Independent cross-checks**
   - For complicated factors such as concentration width, keep a simple slow implementation in tests and compare it with the production implementation.
   - Do not rely on one implementation to prove itself.

4. **No-future-data checks**
   - Factor rows for `factor_date=t` may only read chip and price data at or before `t`.
   - Future returns are joined only after factors are frozen.
   - Factor artifacts must record the max input date used.

5. **Traceability checks**
   - Every factor output must include `factor_id`, formula version, lookback window, source level, implementation type, and quality status.
   - Proxy factors must state why they are proxy factors.

6. **Statistical sanity checks**
   - Coverage rate by factor and stock.
   - Missing-data rate.
   - Distribution summary: min, max, mean, standard deviation, and suspicious outliers.
   - Quantile monotonicity and RankIC are validation evidence, not proof of correctness.

7. **ECC Artifact Reviewer handoff**
   - Each factor run should produce a review packet so the ECC Quality Sub-Agent can inspect formulas, data coverage, future-leak prevention, and report claims.

## Network And Retry Design

Tushare should be treated as unreliable. Network issues, connection resets, rate limits, and partial responses are expected operating conditions.

For local development, do not introduce Kafka or a Kafka-compatible broker as the first step. Kafka/Redpanda is useful when we need distributed producers and consumers, but it adds operational weight that does not help factor correctness yet.

Preferred first design:

```text
local durable job table + idempotent workers + artifact checkpoints
```

Recommended local components:

- SQLite job table for factor-run tasks.
- Local artifact directory for immutable outputs.
- JSONL logs for API calls, retry events, and worker decisions.
- Idempotency keys based on `ts_code`, `endpoint`, `trade_date`, and request parameters.
- Resume support: completed tasks are skipped; failed retryable tasks can be resumed.

Job states:

```text
PENDING
RUNNING
SUCCEEDED
FAILED_RETRYABLE
FAILED_PERMANENT
SKIPPED_EXISTING
```

Retry policy:

- Retry transient network and rate-limit errors with bounded exponential backoff.
- Do not retry permission, schema, invalid-parameter, or empty-data errors as if they were network errors.
- Persist every retry event to disk before sleeping.
- Store sanitized raw error text only; never write tokens or credentials.

When to consider a mini Kafka/Redpanda-style queue:

- multiple local processes need to coordinate work,
- we need streaming progress across many stocks,
- SQLite locking becomes a real bottleneck,
- the factor run is large enough that a durable message log materially improves reliability.

Until those conditions exist, SQLite plus append-only local logs is simpler, easier to inspect, and more aligned with local-first research.

## Local Persistence Design

All factor data should be stored locally. No external database is required for the first implementation.

Recommended layout:

```text
data/
  factor-cache/
    tushare/
      cyq_chips/
        <ts_code>/<trade_date>.json
      daily/
        <ts_code>/<start_date>_<end_date>.json
    jobs.sqlite
docs/
  factor-runs/
    <factor_run_id>/
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

Storage rules:

- Raw API cache is local and reusable.
- Factor-run artifacts are immutable once the run is completed.
- If formulas change, create a new `factor_formula_version` and a new factor run.
- Do not silently overwrite completed factor artifacts.
- A run is reproducible only if config, API logs, factor formula versions, quality summaries, and output checksums are present.

## Validation Plan

For 2026-01-01 to 2026-04-30:

1. Build daily snapshots for each factor date.
2. Build 20-day factors only when a full 20-trading-day lookback exists, unless explicitly running a `PARTIAL` experiment.
3. Build `cyq_cgo_asof` for every date with a valid same-day chip snapshot.
4. Do not treat 100-day CGO as production-ready unless warmup data before 2026-01-01 is collected.
5. Join each factor row with future returns:
   - 1 trading day
   - 5 trading days
   - 10 trading days
   - 20 trading days
6. Evaluate:
   - factor coverage,
   - missing-data rate,
   - average future return by factor quantile,
   - rank correlation between factor value and future return,
   - monotonicity across quantiles.

## First Implementation Recommendation

Implement in this order:

1. Local persistence and job metadata:
   - `factor-run-config.json`
   - `factor-run-manifest.json`
   - local API cache
   - retry and worker event logs
2. `DailyChipSnapshot` calculator.
3. Formula fixture tests and invariant tests.
4. Same-day factors:
   - `profit_ratio_asof`
   - `loss_ratio_asof`
   - `cyq_cgo_asof`
   - `weighted_chip_cost_gap_asof`
   - `dominant_peak_strength_asof`
   - `concentration_width_70_asof`
   - `concentration_width_90_asof`
   - `chip_weighted_std_asof`
5. 20-day delta/proxy factors:
   - `profit_ratio_delta_20d`
   - `loss_ratio_delta_20d`
   - `weighted_chip_cost_delta_20d`
   - `concentration_width_70_delta_20d`
   - `dominant_peak_price_delta_20d`
   - `retained_core_chip_ratio_20d_proxy`
   - `high_chip_accumulation_20d_proxy`
6. Factor artifact writer with checksums.
7. Factor evaluation join with future returns.
8. ECC Artifact Reviewer packet for factor-run outputs.
9. Only after this, create factor-based candidate strategies.

## Open Questions

- Should `retained_core_chip_ratio_20d_proxy` use a dominant-peak band, a 70% concentration band, or a fixed percentage band around weighted cost?
- Should `high_chip_accumulation_20d_proxy` use 5%, 10%, or rolling percentile thresholds?
- Should factor evaluation start with one stock or a small stock pool?
- Should missing warmup data before 2026-01-01 be fetched, or should early dates be marked `INSUFFICIENT_HISTORY`?
