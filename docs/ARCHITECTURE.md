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

## Strategy Boundary

Strategy modules must not fetch data directly. They receive normalized data and return explainable decisions.

## Storage Boundary

First version should use local SQLite for:

- normalized chip distribution rows
- normalized daily price rows
- scan request metadata
- scan result snapshots

Raw Tushare responses may be added later if debugging or reproducibility requires them.
