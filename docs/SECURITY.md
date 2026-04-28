# Security Notes

## Secrets

- `TUSHARE_TOKEN` must be read from environment configuration.
- Never hardcode Tushare tokens in source, tests, docs, screenshots, or logs.
- Health checks may report whether a token exists, but must not print the token value.

## Inputs

- Validate all API inputs with schemas.
- Restrict `n_days` to a bounded positive range.
- Normalize stock codes before use.
- Reject malformed stock codes with clear per-stock errors.
- Do not pass arbitrary user-controlled strings into filesystem paths or SQL statements.

## Data And Logs

- Scan results can include financial research data and should be treated as user-sensitive local data.
- Logs must not include secrets.
- Tushare permission errors should be summarized without dumping request internals that may include credentials.

## Financial Safety

- Product language must present outputs as research signals, not direct investment advice.
- The app must not place trades or connect to brokerage APIs in Phase 1.
- Strategy reasons and limitations must be visible enough for the user to understand why a signal appeared.
