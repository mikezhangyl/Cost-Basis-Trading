# Strategy Research

## Status

Initial source scan completed on 2026-04-28. These are candidate strategy patterns to implement and compare with fixtures before choosing a first combined baseline.

This project should treat these as research heuristics, not proven investment advice.

## Sources Reviewed

- Tushare `cyq_chips` documentation: <https://tushare.pro/document/2?doc_id=294>
- `kengerlwl/ChipDistribution`: <https://github.com/kengerlwl/ChipDistribution>
- `myhhub/stock`: <https://github.com/myhhub/stock>
- GitHub topic listing for chip distribution projects: <https://www.github-zh.com/topics/distribution-of-chips?l=Python>
- MBA智库 CYQ overview: <https://wiki.mbalib.com/wiki/%E7%AD%B9%E7%A0%81%E5%88%86%E5%B8%83>
- Tushare local skill reference: `tushare-skills/references/数据接口.md`

## Strategy Candidate 1: Dominant Chip Peak Breakout

Thesis:

- A major chip peak marks a dense holding-cost area.
- If price moves above the dominant peak and recent return confirms strength, supply pressure may be reduced.

Candidate signal:

- `BUY` when latest close is above dominant peak by a configurable margin, N-day return is positive, and drawdown is controlled.
- `HOLD` when price is near the peak without clear breakout.
- `SELL` when the breakout fails and price falls back below the peak.

Features:

- dominant peak price
- latest close
- close / dominant peak ratio
- N-day return
- max drawdown

## Strategy Candidate 2: Chip Peak Breakdown

Thesis:

- A dense chip peak can act as support.
- Losing that area with weak price action suggests trapped holders and potential selling pressure.

Candidate signal:

- `SELL` when latest close is below the dominant peak or weighted average cost by a threshold and recent return is negative.
- `HOLD` when close is slightly below but volatility is low.

Features:

- latest close relative to dominant peak
- latest close relative to weighted cost
- N-day return
- volatility

## Strategy Candidate 3: Profit-Lock Pressure

Thesis:

- When most chips are below current price, many holders are profitable.
- If price momentum weakens after high winner concentration, profit-taking pressure can rise.

Candidate signal:

- `SELL` when percent below close is high but recent price momentum turns negative.
- `HOLD` when percent below close is high and trend remains stable.

Features:

- percent of chip distribution below latest close
- N-day return
- last 3-day return
- drawdown from range high

## Strategy Candidate 4: Cost Center Migration

Thesis:

- Upward migration of the weighted chip cost with stable or rising price can indicate accumulation or healthier handoff.
- Downward price with rising cost can indicate distribution risk or unstable turnover.

Candidate signal:

- `BUY` or `HOLD` when weighted cost rises slowly, price stays above weighted cost, and drawdown is limited.
- `SELL` when cost center rises but price falls below it.

Features:

- weighted average chip cost at start and end
- dominant peak movement
- latest close relative to weighted cost
- N-day return

## Strategy Candidate 5: Trend-Confirmed Composite

Thesis:

- Chip signals should not stand alone.
- A final signal should require confirmation from recent price behavior.

Candidate signal:

- Aggregate candidate strategies with weighted votes.
- Downgrade `BUY` to `HOLD` when price trend is weak or data quality is partial.
- Upgrade risk to `SELL` when multiple sell conditions agree.

Features:

- candidate strategy votes
- confidence score
- data quality
- N-day return
- drawdown

## Implementation Rule

Each strategy must implement a shared interface:

```python
class StrategySignal:
    strategy_name: str
    action: Literal["BUY", "HOLD", "SELL"]
    confidence: float
    reasons: list[str]
    features: dict[str, float | str | None]
```

No strategy may call Tushare directly.
