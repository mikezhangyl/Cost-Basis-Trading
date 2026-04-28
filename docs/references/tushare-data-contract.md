# Tushare Data Contract

## Credential

Read `TUSHARE_TOKEN` from the environment. Do not hardcode it.

## Primary Endpoint: `cyq_chips`

Purpose: detailed daily chip distribution.

Source: Tushare official documentation.

Reference: <https://tushare.pro/document/2?doc_id=294>

Important documented properties:

- Provides A-share daily chip distribution by price level.
- Data starts from 2018.
- Updates around 18:00-19:00.
- Can be queried by stock code and date range.
- Single request limit is documented as 2000 rows.

Inputs:

- `ts_code`: required stock code, such as `600000.SH`.
- `trade_date`: optional `YYYYMMDD`.
- `start_date`: optional `YYYYMMDD`.
- `end_date`: optional `YYYYMMDD`.

Outputs:

- `ts_code`
- `trade_date`
- `price`
- `percent`

Internal normalized shape:

```python
class ChipDistributionPoint:
    ts_code: str
    trade_date: str
    price: float
    percent: float
```

Required derived features:

- dominant chip peak price
- dominant chip peak percent
- chip concentration near current price
- weighted average chip cost
- percent below latest close
- percent above latest close
- day-over-day movement of dominant peak

## Supporting Endpoint: `daily` Or `pro_bar`

Purpose: recent price movement over the same trading-day range.

Internal normalized shape:

```python
class DailyPriceBar:
    ts_code: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    pre_close: float | None
    pct_chg: float | None
    vol: float | None
    amount: float | None
```

Required derived features:

- N-day return
- maximum drawdown
- volatility
- latest close
- price position relative to dominant chip peak
- price position relative to weighted chip cost

## Optional Endpoint: `cyq_perf`

Purpose: derived chip performance summary.

Use as a supplement or fallback only. It does not replace `cyq_chips` because Phase 1 requires chip detail.

## Error Model

Represent data errors explicitly:

- `MISSING_TOKEN`
- `NO_PERMISSION`
- `EMPTY_DATA`
- `PARTIAL_DATA`
- `RATE_LIMITED`
- `NETWORK_ERROR`
- `INVALID_SYMBOL`

## Query Rules

- Resolve the trading-day range before querying.
- Keep `start_date <= end_date`.
- Query `cyq_chips` one trading day at a time, then merge rows. High-price stocks can exceed the 2000-row single-request limit when queried over a date range.
- Avoid assuming today's data is available.
- Do not fabricate missing chip points.
- Keep row counts and endpoint names in scan metadata.

## Live Check

2026-04-28 local live check with root `.env.local`:

- `GET /api/health` reported `tushare_token_configured: true`.
- `POST /api/scans` for `600519` and `000001`, `n_days=10`, resolved range `20260415` to `20260428`.
- `600519.SH` returned `990` chip detail rows and `10` price bars.
- `000001.SZ` returned `1060` chip detail rows and `10` price bars.
- Both returned data quality `OK`.
