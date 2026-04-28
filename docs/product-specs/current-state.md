# Current Product State

## Status

The repository now has a first local app scaffold:

- `backend/` FastAPI scan API, Tushare client boundary, strategy feature builder, composite signal logic, and pytest coverage.
- `frontend/` React/Vite dashboard with manual stock-code input and signal table.
- `docs/` and `codex/` project memory.

Existing Tushare skill source:

- `tushare-skills/` contains an official Tushare-oriented skill package originally designed for natural-language data research workflows.
- `skills/tushare-data/` adapts that guidance for coding-agent implementation.

## Target Product

Build a local-first stock signal dashboard for A-share research.

The first accepted capability is:

- The user manually enters one or more stock codes.
- The system scans the latest `N` trading days, defaulting to `10`.
- The backend retrieves detailed chip distribution data and price movement data through Tushare.
- The system returns one signal per stock: `BUY`, `HOLD`, or `SELL`.
- The frontend displays the queried stock range and each stock's signal in a visual dashboard table.

Implemented first-screen behavior:

- manual stock-code textarea
- trading-day input defaulting to `10`
- scan action
- scan log panel that shows input parsing, local API connection, Tushare data fetch progress, per-stock row counts, and completion state
- result table with code, name, signal, confidence, latest close, N-day return, chip row count, data quality, and primary reason

Implemented first backtest behavior:

- single-stock historical window check
- user enters `YYYYMMDD` start date and window size `M`
- backend resolves the first `M + 1` trading days from the start date
- first `M` trading days form the analysis window
- the `M`th trading day is the signal date
- the `M + 1`th trading day is the observation date
- output includes the suggested action, confidence, reason, signal-day close, observation-day close, next-day return, and a short validation interpretation

## Product Boundaries

- First version is local use only.
- No account system.
- No brokerage integration.
- No automatic order placement.
- No claim of deterministic investment advice.
- Signals must expose data range, strategy source, feature values, and known limitations.

## Data Priority

The user explicitly requires detailed chip data.

Primary Tushare chip endpoint:

- `cyq_chips`: daily chip distribution by price level and percent.

Supporting endpoints:

- `daily` or `pro_bar`: daily OHLCV and return data.
- `trade_cal`: recent trading-day resolution.
- `stock_basic`: stock code and name resolution.
- `cyq_perf`: optional derived chip summary fallback or supplement, not a replacement for `cyq_chips`.

## Strategy Direction

The first implementation should compare multiple interpretable strategies before selecting a combined baseline. Strategy candidates are tracked in [strategy-research.md](../references/strategy-research.md).
