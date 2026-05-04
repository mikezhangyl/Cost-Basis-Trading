# Existing 13 Factor Research Roadmap

This document records the current plan for continuing the factor research workflow after the first 13 chip factors have been implemented.

## Current State

We currently have:

- 13 implemented chip-related factor definitions.
- Factor production that can generate factor values by stock and trading day.
- Factor evaluation for N+1 / N+3 / N+5 forward returns.
- Factor batch artifacts for a 5-stock 2026 Q1/Q2 sample.
- ECC Artifact Reviewer coverage for factor runs and factor batches.
- A `factor-redundancy-review` skill brief, intended to identify duplicate, mirror, formula-related, and derived-but-not-duplicate factors.

Important distinction:

- A factor definition is the metric name, such as `loss_ratio_asof`.
- A factor value is one stock on one date, such as `000001.SZ` on `2026-01-05` with `loss_ratio_asof = 42.1`.

## Strategic Direction

The long-term idea is an automatic factor research agent:

```text
search public research / papers / open-source projects
  -> propose candidate factors
  -> check redundancy against existing factors
  -> implement candidate factor in a controlled sandbox
  -> run tests
  -> run backtests
  -> generate traceable reports
  -> run ECC review
  -> wait for human approval before promotion
```

However, this project should not jump directly to unrestricted automatic factor discovery.

The next priority is to finish the current 13-factor research loop:

```text
existing 13 factors
  -> factor redundancy review
  -> keep usable non-duplicate factors
  -> run longer-history backtests
  -> discover simple interpretable rules
  -> reserve 2026 as holdout test data
  -> report whether these factors help
```

## Research Question

The main question is:

> Given the current 13 chip factors, can they help explain or predict a stock's future N+1 / N+3 / N+5 returns in a way that is stable, explainable, and not just duplicated evidence?

For a beginner-friendly interpretation:

> We want to know whether today's chip state can tell us anything useful about what may happen over the next few trading days.

This does not mean immediate buy/sell advice. It means validating whether these factors contain useful information.

## Proposed Time Split

Current date in this project context is `2026-05-05`.

To avoid fooling ourselves, we should separate data into:

### Research / Discovery Period

Preferred two-year version:

```text
2024-01-01 through 2025-12-31
```

Fallback one-year version if data/API cost is too high:

```text
2025-01-01 through 2025-12-31
```

Use this period to:

- compute factor values,
- run factor redundancy review,
- study factor behavior,
- discover candidate rules,
- tune thresholds,
- decide which factor combinations deserve testing.

### Holdout Test Period

Use 2026 as the reserved test period:

```text
2026-01-01 through latest available trading day
```

The 2026 data should be treated as out-of-sample data.

Plain-language meaning:

> We design ideas using 2024-2025, then test them on 2026 as if 2026 were unseen. This helps avoid designing a rule that only works because we already looked at the answer.

## Current 13 Factors

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

Expected after redundancy review:

```text
13 original factors -> about 8-10 usable non-duplicate or downweighted factors
```

The actual number must come from the redundancy review, not from a guess.

## Redundancy Review Scope

The redundancy review should not only check whether factor formulas look similar. It should check similarity at several levels.

### 1. Formula Similarity

Example:

```text
profit_ratio_asof + loss_ratio_asof + at_close_ratio ~= total chip percent
```

This suggests `profit_ratio_asof` and `loss_ratio_asof` are likely mirror-related.

### 2. Factor Value Similarity

Check whether two factors produce highly similar values over stock-date samples.

Examples:

```text
correlation close to +1: two factors move together
correlation close to -1: two factors are mirror images
```

Plain-language meaning:

> If two factors always tell the same story, we should not let the later strategy agent count them as two independent pieces of evidence.

### 3. Signal Trigger Similarity

If two factors are converted into simple rules, check whether they trigger on the same days.

Example:

```text
factor A is in its top 20% range
factor B is in its bottom 20% range
```

If those two events happen on almost the same stock-date rows, they are probably redundant in practice.

### 4. Backtest Behavior Similarity

Two factors may not be mathematically identical, but their backtest behavior may be almost the same.

Check:

- similar N+1 / N+3 / N+5 return pattern,
- similar winning days,
- similar losing days,
- similar top/bottom bucket returns,
- similar rule-trigger return distribution.

Plain-language meaning:

> Even if two factors are not formula duplicates, they may behave like the same signal during backtesting.

## Backtest Plan For Existing Factors

For each stock and each factor:

1. Generate daily factor values.
2. Attach future returns:
   - N+1
   - N+3
   - N+5
3. Split into discovery period and holdout period.
4. Analyze the discovery period first.
5. Only after rules are fixed, evaluate them on the 2026 holdout period.

## Metrics To Report

For each factor, report:

### Coverage

How many valid stock-date samples exist.

Plain-language meaning:

> Did this factor produce enough data to trust the result?

### Average Forward Return

The average future return after a factor condition appears.

Plain-language meaning:

> After this signal appears, did the stock tend to go up or down?

### Win Rate

The percentage of samples where future return is positive.

Plain-language meaning:

> Out of 100 similar cases, how many ended up positive?

### Top-Bottom Spread

Compare high-factor samples against low-factor samples.

Plain-language meaning:

> Do high values of this factor behave differently from low values?

### Stability

Check whether a factor works across:

- multiple stocks,
- multiple months,
- N+1 / N+3 / N+5 windows,
- discovery period and holdout period.

Plain-language meaning:

> Did it work broadly, or only by luck in one stock or one month?

## Candidate Rule Discovery

After redundancy review, use only the retained or non-downweighted factor set for rule discovery.

Do not start with complicated machine learning.

Start with simple, explainable rules:

```text
if factor is in top 20% of its own historical range
if factor is in bottom 20% of its own historical range
if factor improved over 20 trading days
if two non-duplicate factors agree
```

Example only, not a final rule:

```text
loss_ratio_delta_20d < 0
and concentration_width_70_delta_20d < 0
and weighted_chip_cost_gap_asof is not too high
```

Plain-language interpretation:

> trapped chips are shrinking, core chips are becoming more concentrated, and price has not moved too far above average holder cost.

Any such rule must be discovered and tested on the discovery period first, then evaluated on 2026 holdout data.

## Avoiding Data Leakage

Data leakage means:

> The rule accidentally uses information from the future while pretending to make a decision in the past.

Rules:

1. Factor values for date `t` can only use data available on or before `t`.
2. Thresholds and rules must be selected using 2024-2025 discovery data.
3. 2026 must not be used to choose thresholds.
4. 2026 is only used after the rule is fixed.

## Artifact Plan

Each major run should be immutable and locally stored.

Suggested directories:

```text
docs/factor-redundancy-reviews/<review-id>/
docs/factor-research-runs/<research-run-id>/
docs/factor-rule-backtests/<backtest-id>/
```

Suggested artifacts:

```text
factor-correlation-matrix.csv
factor-pair-relationships.json
factor-redundancy-groups.json
factor-retention-decisions.json
factor-redundancy-report.md
factor-rule-candidates.json
factor-rule-backtest-summary.json
factor-rule-backtest-report.md
review-events.jsonl
```

## ECC Review Requirements

Every generated report should be reviewable by ECC Artifact Reviewer or an equivalent artifact reviewer.

Review should check:

- Did the run use the correct date split?
- Was 2026 held out?
- Were redundant factors handled correctly?
- Were thresholds chosen only from discovery data?
- Are all source artifacts traceable?
- Are findings explained in beginner-friendly language?
- Does the report avoid direct investment advice?

## Longer-Term Automation Plan

After the 13-factor research loop is complete, build toward the automatic factor research agent.

### Missing Capabilities

1. Research agent with web / paper / open-source search.
2. Factor proposal format.
3. Factor authoring sandbox.
4. One-factor-one-file or one-factor-one-config plugin interface.
5. Restricted coding surface.
6. Automatic tests for new factors.
7. Automatic backtest and redundancy review.
8. ECC review before human approval.

### Safety Rule

The automatic agent should not freely edit the framework.

It should only be allowed to create a small bounded artifact, such as:

```text
one candidate factor config
one candidate factor implementation file
one candidate factor test file
one candidate factor proposal document
```

Promotion into the official factor set should require human approval.

## Immediate Next Steps

1. Run or integrate the completed `factor-redundancy-review` skill on the current 13 factors.
2. Generate a factor retention decision report.
3. Choose the discovery period:
   - preferred: 2024-01-01 to 2025-12-31,
   - fallback: 2025-01-01 to 2025-12-31.
4. Reserve 2026-01-01 onward as holdout test data.
5. Expand factor production over the chosen discovery period.
6. Run factor backtests on retained factors only.
7. Generate simple candidate rules from discovery data.
8. Test fixed rules on 2026 holdout data.
9. Run ECC review on all generated reports.

## Non-Goals For The Next Phase

Do not do these yet:

- unrestricted automatic factor discovery,
- deep learning,
- free-form code generation by an agent,
- live trading,
- portfolio allocation,
- direct buy/sell advice.

The next phase is still research:

> Determine whether the existing 13 factors, after redundancy cleanup, have useful and stable historical signal value.

