# Cost Basis Trading

Local-first stock signal dashboard powered by Tushare chip distribution data.

Phase 1 scans manually entered A-share stock codes over the latest trading-day window, defaults to 10 trading days, and returns a research signal for each stock:

- `BUY`
- `HOLD`
- `SELL`

Signals are research heuristics, not investment advice.

## Current Stack

- Backend: Python, FastAPI, Pydantic, Tushare
- Frontend: React, TypeScript, Vite
- Tests: pytest, Vitest, React Testing Library

## Environment

Create `.env.local` in the repository root, or export the token in your shell:

```bash
export TUSHARE_TOKEN=your_tushare_token
```

The backend automatically loads root `.env.local`. The token is required for live scans. Tests use mocked data and do not need the token.

## Backend

```bash
cd backend
python -m pip install -e '.[dev]'
pytest -v
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Useful endpoints:

- `GET /api/health`
- `POST /api/scans`

Example scan payload:

```json
{
  "stock_codes": ["600519", "000001.SZ"],
  "n_days": 10
}
```

## Frontend

```bash
cd frontend
npm install
npm run test
npm run build
npm run dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8000`.

## ECC Quality Gate

The baseline deterministic quality gate is intended to be run by the ECC Quality Sub-Agent before commit:

```bash
python scripts/ecc_quality_workflow.py quality-gate
```

It runs `git diff --check`, backend `pytest -v`, frontend `npm run test`, and frontend `npm run build`.

Parent Codex remains the orchestrator and approval gate. It may run smaller local checks while developing, but commit readiness should use this sub-agent gate result plus any task-specific reviews.

When the task produced a research run report, include artifact review:

```bash
python scripts/ecc_quality_workflow.py quality-gate --include-artifact-review
```

## Project Memory

Durable project context lives under [docs](./docs/index.md).

Key files:

- [Phase 1 PRD](./docs/requirements/active/phase-1-signal-dashboard/PRD.md)
- [Execution plan](./docs/exec-plans/active/phase-1-signal-dashboard.md)
- [Tushare data contract](./docs/references/tushare-data-contract.md)
- [Strategy research](./docs/references/strategy-research.md)
