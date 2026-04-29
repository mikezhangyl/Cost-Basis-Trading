# Chip Change Strategy Traceability

## Status

Research note created on 2026-04-29.

This document records the intermediate research process for strategies that use **daily changes in existing chip detail data**. It is intentionally separate from implementation code. No rule in this document should be treated as a production trading strategy until it has been backtested against this project's `N+3`, `N+7`, and `N+15` observation windows.

## Research Correction

The first research pass focused too much on how to calculate CYQ or chip distribution from price and turnover. That is not the target problem for this project because Tushare already provides daily chip detail rows.

The corrected target is:

- Input: daily `cyq_chips` detail rows already available from Tushare.
- Objective: analyze how the chip structure changes over a chosen window.
- Output: evidence-backed candidate indicators and strategy rules for `BUY`, `HOLD`, or `SELL`.
- Constraint: every rule must map back to a source or be explicitly labeled as a project hypothesis.

## Source Log

Sources found in this pass:

| Source | Type | Relevance | Notes |
| --- | --- | --- | --- |
| [hugo2046/QuantsPlaybook](https://github.com/hugo2046/QuantsPlaybook) | Open-source research repo | High | GitHub project lists `筹码分布因子` and cites `广发证券_多因子Alpha系列报告之（二十七）——基于筹码分布的选股策略`. Useful as a public bridge from broker research to reproducible quant code. |
| [JoinQuant: 复现筹码分布因子](https://www.joinquant.com/community/post/detailMobile?postId=41902) | Open-source/reproduction article | High | Hugo2046 article says the reproduction references Guangfa's chip distribution stock selection report, uses Qlib, and implements related operators such as `cyq_ops.py`, `turnover_coefficient_ops.py`, and `distribution_of_chips.py`. |
| [华西证券: 留存筹码比率选股因子](https://www.fxbaogao.com/detail/4871715) | Broker quant report | High | Defines retained chip ratio as chips bought in a past period and not sold again by the selection date. Report states higher retained chip ratio measures higher chip accumulation and may support right-side trend behavior. |
| [BigQuant: 筹码分布因子系统构建](https://bigquant.com/square/paper/5f3c2f13-367e-4be1-a0b7-e926acca54fd) | Quant platform research summary | Medium | Summarizes chip distribution factor classes and mentions shape/statistical factors such as kurtosis and chip distribution system construction. Use for factor-family orientation, not direct rule thresholds. |
| [中金高频因子手册分享: 筹码分布因子](https://www.pandaai.online/community/article/182) | Public factor strategy article | Medium | Shows a factor workflow: periodically calculate chip factor values, rank stocks, and rebalance. Useful for separating factor construction from trading schedule. |
| [Stock-Fund/XCrawler](https://github.com/Stock-Fund/XCrawler) | Open-source notes/tooling | Low to Medium | Contains practical chip-analysis notes such as winner/loser chip pressure. Useful as heuristic background only, not a formal source for thresholds. |

## Evidence Levels

Use these labels when designing rules:

- `DIRECT_REPORT`: a broker or quant report directly defines the factor or concept.
- `OPEN_REPRODUCTION`: an open-source repo or public notebook reproduces a broker factor or cites the source report.
- `FACTOR_FAMILY`: the source supports this class of factor, but not the exact rule.
- `HEURISTIC`: practical market experience or platform notes, not enough for formal strategy design.
- `PROJECT_HYPOTHESIS`: derived from our data shape and needs. Must be backtested before promotion.

## Traceability Matrix

| Candidate indicator or rule | Source | Source concept | Project conversion | Evidence level | Validation status |
| --- | --- | --- | --- | --- | --- |
| `retained_chip_ratio` | 华西证券留存筹码比率报告 | Retained amount/chip ratio represents chips bought in a past period and retained until the selection date. Higher value indicates stronger chip accumulation and possible right-side trend formation. | Approximate retention using daily Tushare chip rows by tracking stable cost buckets over the analysis window. | `DIRECT_REPORT` | Not implemented. Needs window-level backtest. |
| `retained_chip_ratio_delta` | 华西证券留存筹码比率报告 | The report emphasizes accumulated retained chips as a factor, not only a single-day chip shape. | Compare retained-chip estimate at window start and window end. Rising retention means chips are accumulating rather than rapidly rotating away. | `DIRECT_REPORT` plus `PROJECT_HYPOTHESIS` | Not implemented. Needs definition from Tushare rows. |
| `chip_concentration_delta` | QuantsPlaybook / JoinQuant reproduction of Guangfa chip distribution factor | Guangfa chip distribution factor direction studies cost distribution; reproduction includes CYQ and chip distribution algorithms. | Measure whether the cost distribution becomes more concentrated or more dispersed across the window. | `OPEN_REPRODUCTION` | Not implemented. Need concentration metric choice. |
| `profit_ratio_delta` | Chip distribution factor family from CICC/BigQuant style summaries | Chip factors can include proportions of chips at different profit/loss levels. | For each day, calculate percent of chip rows below current close. Delta over the window is winner-chip expansion or contraction. | `FACTOR_FAMILY` | Not implemented. Needs Tushare row aggregation. |
| `loss_ratio_delta` | Chip distribution factor family from CICC/BigQuant style summaries | Chip proportions at different profit/loss levels can be factor inputs. | For each day, calculate percent of chip rows above current close. Delta over the window is trapped-chip expansion or contraction. | `FACTOR_FAMILY` | Not implemented. Needs Tushare row aggregation. |
| `dominant_peak_price_shift` | CYQ/chip distribution factor sources and practical chip analysis | Dominant chip peak marks the densest holding-cost zone. | Track the price of the highest-percent chip bucket across days. Upward shift may indicate cost center migration; downward shift may indicate failed support or lower-cost turnover. | `FACTOR_FAMILY` plus `PROJECT_HYPOTHESIS` | Not implemented. Must be tested. |
| `low_chip_retention` | 华西 retained-chip concept plus practical chip migration reading | Retained chips represent cost areas not sold away. | Define low-cost bucket range near the early window's dominant/weighted cost. Check whether those chips remain after price rises. | `PROJECT_HYPOTHESIS` | Not implemented. High priority to test. |
| `high_chip_accumulation` | Chip distribution shape factor family | Distribution shape can indicate new high-cost concentration. | Detect whether new chip concentration appears above prior cost center while low-cost chips shrink. | `PROJECT_HYPOTHESIS` | Not implemented. Use as sell-risk hypothesis. |
| `price_up_profit_ratio_down` | Practical chip pressure heuristic, needs stronger source | Rising price with falling profitable-chip ratio may imply high turnover or distribution into strength. | Flag as sell-risk only when combined with high-chip accumulation or concentration deterioration. | `HEURISTIC` | Not implemented. Do not use without backtest evidence. |
| `price_flat_loss_ratio_down` | Practical chip digestion heuristic, needs stronger source | Sideways price with shrinking trapped chips may indicate overhead pressure digestion. | Flag as buy-watch only, not immediate buy. | `HEURISTIC` plus `PROJECT_HYPOTHESIS` | Not implemented. Needs careful false-positive testing. |

## Candidate Rule Templates

These are not final trading rules. They are templates for future implementation and backtest comparison.

### Rule A: Retained Accumulation Buy Watch

Traceability:

- Primary source: 华西证券留存筹码比率选股因子.
- Project interpretation: stronger retained chips across the window may support trend continuation.

Draft logic:

```text
IF retained_chip_ratio_delta > threshold
AND chip_concentration_delta <= stable_or_more_concentrated
AND price_return >= neutral_or_positive
THEN BUY_WATCH
```

Status: source-backed factor direction, but Tushare-row conversion and thresholds are project hypotheses.

### Rule B: Overhead Digestion Hold-to-Buy Watch

Traceability:

- Source family: profit/loss chip proportion factors from chip distribution factor research.
- Practical heuristic: trapped chips shrinking while price holds suggests overhead pressure digestion.

Draft logic:

```text
IF loss_ratio_delta < 0
AND price_return >= 0
AND dominant_peak_price_shift is stable_or_up
THEN HOLD or BUY_WATCH
```

Status: candidate only. Needs backtest because shrinking loss ratio may also occur during short squeezes or volatile rebounds.

### Rule C: Distribution Risk Sell Watch

Traceability:

- Source family: chip distribution shape/proportion factors.
- Practical heuristic: low-cost chips leaving and high-cost concentration forming can indicate distribution into strength.

Draft logic:

```text
IF low_chip_retention_delta < negative_threshold
AND high_chip_accumulation_delta > positive_threshold
AND price_return >= 0
THEN SELL_WATCH
```

Status: mostly project hypothesis. Must be proven with `N+3`, `N+7`, and `N+15` validation before use.

### Rule D: Concentration Improvement Hold

Traceability:

- Source family: Guangfa chip distribution factor via QuantsPlaybook/JoinQuant reproduction.

Draft logic:

```text
IF chip_concentration_delta improves
AND price is above or near weighted chip cost
AND profit_ratio is not extreme
THEN HOLD
```

Status: source-backed factor family, but the specific action mapping is project hypothesis.

## Data Mapping From Tushare

Existing project data:

- `cyq_chips.trade_date`
- `cyq_chips.price`
- `cyq_chips.percent`
- daily close from `daily`

Potential daily aggregates:

| Aggregate | Calculation sketch |
| --- | --- |
| `weighted_chip_cost` | `sum(price * percent) / sum(percent)` for one trade date |
| `dominant_peak_price` | `price` at max `percent` for one trade date |
| `profit_ratio` | `sum(percent where price < close)` |
| `loss_ratio` | `sum(percent where price > close)` |
| `concentration_width_70` | Narrowest price band covering 70% of chip percent, or approximation by cumulative sorted price buckets |
| `concentration_width_90` | Same for 90% |
| `chip_kurtosis_like` | Moment-style shape metric over price buckets weighted by percent |
| `retained_low_chip_ratio` | Percent remaining in initial low-cost bucket range at later dates |
| `high_chip_accumulation_ratio` | Percent in newly formed upper price bucket range |

## Research Decisions

1. Do not use the current hand-written threshold strategy as the final algorithm.
2. Treat the current app strategy as a temporary baseline only.
3. Future strategy work should first implement chip-change feature extraction, then compare multiple rules with the existing backtest surface.
4. A rule cannot be promoted from candidate to baseline unless the traceability matrix lists its evidence level and the backtest results are recorded.
5. Broker-report-derived factors should be preferred over unsupported retail heuristics.

## Open Questions

- Can Tushare `cyq_chips` daily rows be used to approximate retained chip ratio with enough fidelity, given that the 华西 report uses minute-level turnover and amount?
- Which concentration metric is most stable with Tushare's chip row granularity: 70/90 width, weighted standard deviation, entropy, or peak-percent concentration?
- Should thresholds be fixed globally or learned by rolling percentile within each stock?
- Should `HOLD` be treated as a neutral result in backtest scoring, or evaluated by drawdown avoidance?
- Do `N+3`, `N+7`, and `N+15` need different rule weights?

## Next Research Step

Before coding, produce a small design proposal for a `ChipChangeFeatureSet` that includes:

- exact formulas,
- source mapping for each feature,
- missing-data behavior,
- expected API response fields,
- and backtest scoring criteria.
