# Phase 1 PRD: Signal Dashboard

## Capability

As a local research user, I want to manually enter stock codes and a trading-day lookback window so that I can see a first-pass `BUY`, `HOLD`, or `SELL` signal for each stock based on detailed chip distribution and recent price movement.

## Fixed Requirements

- Stock input is manual stock-code entry.
- Default lookback is `10` trading days.
- The system must prioritize detailed chip distribution from Tushare `cyq_chips`.
- The system must combine chip distribution features with recent price movement features.
- The first dashboard must show each queried stock and its final signal.
- The signal must include at least one human-readable reason.
- Tushare token must come from environment configuration, not source code.
- The first version is local-only.

## User Inputs

- `stock_codes`: one or more stock codes, accepting normalized Tushare codes such as `600519.SH`.
- `n_days`: optional positive integer, default `10`.
- `end_date`: optional date, default latest available trading day.

## User-Visible Output

For each valid stock:

- stock code
- stock name when available
- resolved trading-date range
- signal: `BUY`, `HOLD`, or `SELL`
- confidence score or confidence band
- key reasons
- latest close
- N-day return
- chip detail summary derived from `cyq_chips`
- data quality status

## Strategy Requirements

- Do not ship one opaque strategy.
- Research several open-source or community strategy patterns first.
- Implement strategy modules with a shared interface so they can be compared.
- First combined baseline should be explainable and testable with mock data.
- Strategy output is a research signal, not investment advice.

## Data Quality Requirements

- Detect missing Tushare token.
- Detect missing endpoint permission, especially for `cyq_chips`.
- Handle empty data, suspended trading days, and partial date ranges.
- Do not fabricate data if Tushare access is unavailable.
- Include data range and retrieval status in API responses.

## Non-Goals

- User authentication.
- Cloud deployment.
- Real-time trading.
- Broker order execution.
- Portfolio accounting.
- Complex backtesting engine.
- Final visualization design beyond the first signal list.

## Acceptance Criteria

- A user can enter a comma- or newline-separated list of stock codes and run a scan.
- The backend resolves the latest `10` trading days by default.
- The backend requests `cyq_chips` detail data for each stock and date range.
- The backend requests recent price movement data for the same range.
- The API returns a stable JSON envelope with one result per stock.
- The frontend renders `BUY`, `HOLD`, and `SELL` states distinctly.
- Permission or data errors are shown per stock without breaking the whole scan.
- Unit tests cover strategy decisions with fixture chip and price data.

## Open Questions

- Whether to accept bare six-digit stock codes and infer exchange automatically.
- Exact first-pass strategy thresholds after exploratory comparison.
- Whether to cache raw Tushare responses on disk or only normalized rows in SQLite.
- Whether `cyq_chips` permission is available on the user's Tushare account.
