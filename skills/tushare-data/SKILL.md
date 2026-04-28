---
name: tushare-data
description: Coding-agent workflow for building and testing Tushare-backed data features in this repository. Use when implementing stock data ingestion, chip distribution analysis, strategy signals, scan APIs, or data exports with the Tushare Python SDK.
origin: project
source_skill: ../tushare-skills/SKILL.md
---

# Tushare Data

Use this skill when coding data access, analysis, tests, or backend workflows that depend on Tushare.

The official source package remains in `tushare-skills/`. This project skill adapts it for coding-agent implementation work.

## Non-Negotiable Rules

- Read `TUSHARE_TOKEN` from environment configuration.
- Never hardcode or log the token.
- Treat Tushare as an external dependency with permission, quota, freshness, and network failure modes.
- Do not fabricate market data when an endpoint is unavailable.
- For Phase 1, detailed chip distribution means `cyq_chips`; `cyq_perf` is only a supplement.
- Strategy modules must be pure over normalized inputs and must not call Tushare directly.
- Every financial conclusion must expose data range, strategy version, key features, and limitations.

## Phase 1 Data Endpoints

Primary:

- `cyq_chips`: detailed daily chip distribution by price level and percent.

Supporting:

- `daily` or `pro_bar`: OHLCV and price movement.
- `trade_cal`: latest available trading-day range.
- `stock_basic`: code/name metadata.
- `cyq_perf`: optional summary supplement.

## Implementation Workflow

1. Validate input schema before touching Tushare.
2. Normalize stock codes.
3. Resolve the trading-day range.
4. Fetch chip detail rows from `cyq_chips`.
5. Fetch daily price bars over the same range.
6. Normalize into internal data structures.
7. Derive chip and price features.
8. Run strategy modules.
9. Return explainable per-stock signals.
10. Persist scan metadata and row counts if storage is available.

## Required Error Cases

Represent these explicitly in tests and API responses:

- `MISSING_TOKEN`
- `NO_PERMISSION`
- `EMPTY_DATA`
- `PARTIAL_DATA`
- `RATE_LIMITED`
- `NETWORK_ERROR`
- `INVALID_SYMBOL`

## Testing Contract

Write tests before implementation:

- strategy unit tests using fixtures
- Tushare client tests using mocked responses
- scan service tests with partial failure
- API integration tests with fake Tushare client

Live Tushare tests should be opt-in and skipped when `TUSHARE_TOKEN` is absent.

## References

- `docs/references/tushare-data-contract.md`
- `docs/references/strategy-research.md`
- `tushare-skills/references/数据接口.md`
