# Execution Plan: Phase 1 Signal Dashboard

## Goal

Build a local-first web dashboard that scans manually entered stocks over the latest 10 trading days and displays `BUY`, `HOLD`, or `SELL` signals derived from detailed chip distribution and price movement data.

## Architecture Choice

- Backend: Python + FastAPI.
- Data access: Tushare Python SDK.
- Persistence/cache: SQLite for local normalized data and scan history.
- Frontend: React + TypeScript + Vite.
- Testing: pytest for backend, Vitest/React Testing Library for frontend, Playwright for the critical local scan flow.

## Phase 0: Bootstrap And Memory

- Establish docs memory structure.
- Capture PRD, execution plan, data contract, strategy research, architecture, security, and reliability docs.
- Adapt the Tushare skill into a coding-agent-oriented workflow reference.

Status: complete.

## Phase 1: Data Contract And Fixtures

- Define normalized stock-code input schema.
- Define Tushare client interface.
- Define normalized `ChipDistributionPoint` from `cyq_chips`.
- Define normalized `DailyPriceBar` from `daily` or `pro_bar`.
- Create fixture datasets for at least:
  - rising price with low-cost chip concentration
  - price breakdown below major chip peak
  - mixed/sideways signal
  - missing chip data
  - missing Tushare permission

Status: first pass complete for backend tests.

## Phase 2: Strategy Comparison

Implement candidate strategy modules behind the same interface:

- `chip_peak_breakout`: price breaks above the dominant chip peak with improving return confirmation.
- `chip_peak_breakdown`: price loses dominant chip peak support with weak return confirmation.
- `profit_lock_pressure`: high winner concentration plus weakening price action marks sell pressure.
- `cost_center_migration`: average chip cost and dominant peak moving upward with stable price marks accumulation/hold.
- `trend_confirmed_chip_signal`: chip signal must be confirmed by N-day return and drawdown controls.

Compare candidates against fixtures and document differences before selecting the first combined baseline.

Status: first composite baseline implemented. Dedicated side-by-side strategy module comparison is still pending.

## Phase 3: Backend API

- `POST /api/scans`
  - Input: stock codes, optional `n_days`, optional `end_date`.
  - Output: stable envelope with per-stock results.
- `GET /api/health`
  - Includes Tushare token presence but never reveals token value.
- Per-stock failures should not fail the whole request.
- Validate all inputs with schemas.

Status: first pass complete.

## Phase 4: Frontend Dashboard

- Manual stock-code input.
- `N` day input defaulting to 10.
- Scan button with loading state.
- Results table with clear signal states.
- Per-stock data quality/error display.
- Basic responsive layout.

Status: first pass complete.

## Phase 5: Verification

- Unit tests for strategy modules.
- Unit tests for date/window normalization.
- Mock Tushare client tests for permission errors and empty data.
- API integration tests with fake client.
- Frontend component tests for loading, result, and error states.
- Playwright happy path with mock backend.

Status: pytest, Vitest, and frontend production build pass. Live Tushare scan succeeds for `600519` and `000001` after `cyq_chips` switched to per-trading-day retrieval. Playwright E2E is not added yet.

## Done Criteria

- The local app can be started from documented commands.
- A scan over manually entered stock codes returns one result per stock.
- Detailed chip data from `cyq_chips` is part of the backend feature pipeline.
- Strategy decisions are explainable and covered by tests.
- Missing Tushare token or permission produces actionable errors.
- Frontend shows signal conclusions without needing browser console inspection.

## Backtest First Pass

Status: implemented after the initial scaffold commit.

- Added `POST /api/backtests`.
- Added single-window historical validation:
  - start date + `M` trading days
  - signal on day `M`
  - observations on `M + 3`, `M + 7`, and `M + 15`
- Added backend tests for backtest service and API validation.
- Added frontend backtest form, signal/observation metrics, and interpretation copy.

Remaining follow-up:

- Add multi-window rolling validation after this single-window contract is stable.
- Add stock-pool validation.
- Add batch export for historical checks.
- Add caching for larger date ranges.
