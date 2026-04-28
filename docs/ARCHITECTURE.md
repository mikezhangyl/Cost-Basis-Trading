# Architecture Map

## Intended Repository Boundaries

- `backend/`
  FastAPI application, Tushare data access, scan orchestration, strategy evaluation, SQLite persistence, and backend tests.
- `frontend/`
  React + TypeScript + Vite scanner dashboard and frontend tests.
- `docs/`
  Durable repository memory for product, architecture, security, reliability, requirements, plans, and references.
- `tushare-skills/`
  Current official Tushare skill package. It should be treated as source/reference material until adapted into a coding-agent-oriented project skill.
- `codex/`
  Current-session pointer plus concise execution history.

## Runtime Flow

### Phase 1 Scan Flow

1. The user enters stock codes and optionally adjusts `n_days`.
2. The frontend sends a scan request to the backend.
3. The backend validates input and normalizes stock codes.
4. The backend resolves the latest available trading-day range.
5. For each stock, the backend fetches:
   - detailed chip distribution from `cyq_chips`
   - daily price movement from `daily` or `pro_bar`
   - stock metadata from `stock_basic` when needed
6. The backend normalizes data into internal data structures.
7. Strategy modules calculate candidate signals.
8. A combined signal selector returns `BUY`, `HOLD`, or `SELL` plus reasons.
9. The frontend renders the per-stock result table.

### Backtest Flow

This first backtest surface is a single-window historical validation, not a portfolio simulator.

1. The user enters one stock code, a start date, and window size `M`.
2. The backend resolves the first `M + 1` trading days from that start date.
3. The first `M` trading days form the analysis window.
4. The `M`th trading day is the signal date.
5. The backend fetches `cyq_chips` and daily price bars for the analysis window.
6. The same signal strategy used by live scans generates a `BUY`, `HOLD`, or `SELL` recommendation on the signal date.
7. The `M + 1`th trading day is the observation date.
8. The API returns signal details plus observation-day close and next-day return.

## API Contract Direction

Use a stable response envelope:

```json
{
  "success": true,
  "data": {
    "scan_id": "local-generated-id",
    "requested_at": "2026-04-28T00:00:00Z",
    "n_days": 10,
    "results": []
  },
  "error": null
}
```

Per-stock errors belong in `results[]` so one bad symbol does not fail the whole scan.

Backtest endpoint:

- `POST /api/backtests`

## Strategy Boundary

Strategy modules must not fetch data directly. They receive normalized data and return explainable decisions.

## Storage Boundary

First version should use local SQLite for:

- normalized chip distribution rows
- normalized daily price rows
- scan request metadata
- scan result snapshots

Raw Tushare responses may be added later if debugging or reproducibility requires them.
