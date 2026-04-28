# Reliability Notes

## External Dependency

Tushare availability, endpoint permissions, update time, and rate limits are external dependencies.

`cyq_chips` is the critical endpoint for Phase 1. If it is unavailable or unauthorized, the scan should return a per-stock data-quality error instead of inventing a signal.

## Data Freshness

Tushare documentation describes `cyq_chips` as daily chip distribution data that updates around evening. The app should surface the actual latest `trade_date` returned instead of assuming today's data exists.

## Partial Failure

One stock's failure must not stop other stocks from being scanned.

Common partial failures:

- malformed stock code
- no trading data in requested range
- suspended trading
- missing `cyq_chips` permission
- rate limit or transient network error

## Retry Policy

- Retry transient network or rate-limit failures with a small bounded retry count.
- Do not retry schema, parameter, permission, or validation errors.

## Reproducibility

Each scan result should include:

- requested stock codes
- normalized stock codes
- resolved trading-date range
- strategy version
- data endpoint names
- row counts
- data quality status
