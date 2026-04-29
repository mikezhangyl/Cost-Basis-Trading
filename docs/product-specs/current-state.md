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
- backend resolves enough trading days to evaluate `N+1`, `N+3`, and `N+5`
- first `M` trading days form the analysis window
- the `M`th trading day is the signal date
- observation windows are `N+1`, `N+3`, and `N+5`
- output includes the suggested action, confidence, reason, market context, each observation date, each observation close, each period return, and whether the movement matches the original `BUY` or `SELL` suggestion

Implemented first research-run behavior:

- single-stock research workflow prototype
- user enters one stock code, multiple sample start dates, and window size `M`
- backend runs each sample as an isolated analysis window
- each sample freezes candidate strategy signals before scoring future returns
- initial candidate strategies are `composite_baseline` and `market_context_followthrough`
- scoring uses `N+1`, `N+3`, and `N+5` directional returns
- backend writes trace artifacts under `docs/research-runs/<run_id>/`
- frontend displays run id, artifact directory, aggregate strategy scores, and per-sample artifact paths

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
