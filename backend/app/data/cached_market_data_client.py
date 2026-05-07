from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Literal

from app.data.cache_writer import CacheWriteMode, CacheWriter
from app.data.market_cache import CacheKey, CachePayloadKind, CacheWrite, MarketCacheStore
from app.domain.errors import DataErrorCode, DataUnavailableError
from app.domain.models import AdjustmentFactor, ChipDistributionPoint, DailyPriceBar

PriceMode = Literal["raw", "return_adjusted", "qfq", "hfq"]


@dataclass(frozen=True)
class PriceSemantics:
    price_mode: PriceMode
    data_horizon: str
    adjustment_anchor_date: str | None = None


class CachedMarketDataClient:
    def __init__(
        self,
        provider_client: Any,
        cache_store: MarketCacheStore,
        cache_writer: CacheWriter | None = None,
        provider: str = "tushare",
    ) -> None:
        self.provider_client = provider_client
        self.cache_store = cache_store
        self.cache_writer = cache_writer or CacheWriter(cache_store, mode=CacheWriteMode.SYNC)
        self.provider = provider
        self.cache_event_handler: Callable[[dict[str, Any]], None] | None = None

    def set_cache_event_handler(self, handler: Callable[[dict[str, Any]], None] | None) -> None:
        self.cache_event_handler = handler

    def resolve_trading_days(self, end_date: str | None, n_days: int) -> list[str]:
        return self.provider_client.resolve_trading_days(end_date, n_days)

    def resolve_trading_days_from(self, start_date: str, n_days: int) -> list[str]:
        return self.provider_client.resolve_trading_days_from(start_date, n_days)

    def get_stock_name(self, ts_code: str) -> str | None:
        return self.provider_client.get_stock_name(ts_code)

    def get_daily_prices(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        semantics: PriceSemantics | None = None,
    ) -> list[DailyPriceBar]:
        semantics = semantics or PriceSemantics(price_mode="raw", data_horizon=end_date)
        _validate_price_semantics(semantics)
        if semantics.price_mode != "raw":
            raise NotImplementedError(
                f"get_daily_prices does not materialize {semantics.price_mode} price bars yet. "
                "Use raw bars plus adjustment factors for adjusted return calculations."
            )
        trade_dates = self._trading_days_between(start_date, end_date)
        keys = [self._daily_key(ts_code, trade_date) for trade_date in trade_dates]
        lookup = self.cache_store.read_many(keys)
        rows_by_date: dict[str, DailyPriceBar] = {}
        suppressed_dates: set[str] = set()
        write_status_counts: dict[str, int] = {}

        for key in keys:
            entry = lookup.hits.get(key.identity)
            if entry is None:
                continue
            if entry.payload_kind == CachePayloadKind.ROWS:
                rows_by_date[key.date_key] = _daily_bar_from_payload(entry.payload)
            elif entry.payload_kind == CachePayloadKind.PERMANENT_NO_DATA:
                suppressed_dates.add(key.date_key)
            elif entry.payload_kind == CachePayloadKind.PROVISIONAL_NO_DATA:
                suppressed_dates.add(key.date_key)

        fetch_dates = [
            key.date_key
            for key in keys
            if key.date_key not in rows_by_date
            and key.date_key not in suppressed_dates
            and (key.identity in lookup.misses or key.identity in lookup.stale)
        ]
        for range_start, range_end in _date_ranges(fetch_dates):
            range_dates = _dates_between(fetch_dates, range_start, range_end)
            try:
                fetched_rows = self.provider_client.get_daily_prices(ts_code, range_start, range_end)
            except DataUnavailableError as error:
                if error.code != DataErrorCode.EMPTY_DATA:
                    raise
                for trade_date in range_dates:
                    _record_write_status(
                        write_status_counts,
                        self.cache_writer.write(self._daily_no_data_write(ts_code, trade_date)).status,
                    )
                continue
            fetched_dates = {row.trade_date for row in fetched_rows}
            for row in fetched_rows:
                rows_by_date[row.trade_date] = row
                _record_write_status(write_status_counts, self.cache_writer.write(self._daily_write(row)).status)
            for trade_date in range_dates:
                if trade_date not in fetched_dates:
                    _record_write_status(
                        write_status_counts,
                        self.cache_writer.write(self._daily_no_data_write(ts_code, trade_date)).status,
                    )

        rows = [rows_by_date[trade_date] for trade_date in trade_dates if trade_date in rows_by_date]
        self._record_cache_event(
            endpoint="daily",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            requested_date_count=len(keys),
            returned_row_count=len(rows),
            fetched_date_count=len(fetch_dates),
            lookup=lookup,
            suppressed_date_count=len(suppressed_dates),
            write_status_counts=write_status_counts,
        )
        return rows

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        trade_dates = self._trading_days_between(start_date, end_date)
        keys = [self._chip_key(ts_code, trade_date) for trade_date in trade_dates]
        lookup = self.cache_store.read_many(keys)
        rows_by_date: dict[str, list[ChipDistributionPoint]] = {}
        suppressed_dates: set[str] = set()
        write_status_counts: dict[str, int] = {}

        for key in keys:
            entry = lookup.hits.get(key.identity)
            if entry is None:
                continue
            if entry.payload_kind == CachePayloadKind.ROWS:
                rows_by_date[key.date_key] = [_chip_point_from_payload(item) for item in entry.payload]
            elif entry.payload_kind == CachePayloadKind.PERMANENT_NO_DATA:
                suppressed_dates.add(key.date_key)
            elif entry.payload_kind == CachePayloadKind.PROVISIONAL_NO_DATA:
                suppressed_dates.add(key.date_key)

        fetch_dates = [
            key.date_key
            for key in keys
            if key.date_key not in rows_by_date
            and key.date_key not in suppressed_dates
            and (key.identity in lookup.misses or key.identity in lookup.stale)
        ]
        for range_start, range_end in _date_ranges(fetch_dates):
            range_dates = _dates_between(fetch_dates, range_start, range_end)
            try:
                fetched_rows = self.provider_client.get_chip_distribution(ts_code, range_start, range_end)
            except DataUnavailableError as error:
                if error.code != DataErrorCode.EMPTY_DATA:
                    raise
                for trade_date in range_dates:
                    _record_write_status(
                        write_status_counts,
                        self.cache_writer.write(self._chip_no_data_write(ts_code, trade_date)).status,
                    )
                continue
            fetched_by_date: dict[str, list[ChipDistributionPoint]] = {}
            for row in fetched_rows:
                fetched_by_date.setdefault(row.trade_date, []).append(row)
            for trade_date, rows in fetched_by_date.items():
                rows_by_date[trade_date] = rows
                _record_write_status(
                    write_status_counts,
                    self.cache_writer.write(self._chip_write(ts_code, trade_date, rows)).status,
                )
            for trade_date in range_dates:
                if trade_date not in fetched_by_date:
                    _record_write_status(
                        write_status_counts,
                        self.cache_writer.write(self._chip_no_data_write(ts_code, trade_date)).status,
                    )

        rows = [row for trade_date in trade_dates for row in rows_by_date.get(trade_date, [])]
        self._record_cache_event(
            endpoint="cyq_chips",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            requested_date_count=len(keys),
            returned_row_count=len(rows),
            fetched_date_count=len(fetch_dates),
            lookup=lookup,
            suppressed_date_count=len(suppressed_dates),
            write_status_counts=write_status_counts,
        )
        return rows

    def get_adjustment_factors(self, ts_code: str, start_date: str, end_date: str) -> list[AdjustmentFactor]:
        trade_dates = self._trading_days_between(start_date, end_date)
        keys = [self._adjustment_key(ts_code, trade_date) for trade_date in trade_dates]
        lookup = self.cache_store.read_many(keys)
        rows_by_date: dict[str, AdjustmentFactor] = {}
        suppressed_dates: set[str] = set()
        write_status_counts: dict[str, int] = {}

        for key in keys:
            entry = lookup.hits.get(key.identity)
            if entry is None:
                continue
            if entry.payload_kind == CachePayloadKind.ROWS:
                rows_by_date[key.date_key] = _adjustment_factor_from_payload(entry.payload)
            elif entry.payload_kind == CachePayloadKind.PERMANENT_NO_DATA:
                suppressed_dates.add(key.date_key)
            elif entry.payload_kind == CachePayloadKind.PROVISIONAL_NO_DATA:
                suppressed_dates.add(key.date_key)

        fetch_dates = [
            key.date_key
            for key in keys
            if key.date_key not in rows_by_date
            and key.date_key not in suppressed_dates
            and (key.identity in lookup.misses or key.identity in lookup.stale)
        ]
        for range_start, range_end in _date_ranges(fetch_dates):
            range_dates = _dates_between(fetch_dates, range_start, range_end)
            try:
                fetched_rows = self.provider_client.get_adjustment_factors(ts_code, range_start, range_end)
            except DataUnavailableError as error:
                if error.code != DataErrorCode.EMPTY_DATA:
                    raise
                for trade_date in range_dates:
                    _record_write_status(
                        write_status_counts,
                        self.cache_writer.write(self._adjustment_no_data_write(ts_code, trade_date)).status,
                    )
                continue
            fetched_dates = {row.trade_date for row in fetched_rows}
            for row in fetched_rows:
                rows_by_date[row.trade_date] = row
                _record_write_status(write_status_counts, self.cache_writer.write(self._adjustment_write(row)).status)
            for trade_date in range_dates:
                if trade_date not in fetched_dates:
                    _record_write_status(
                        write_status_counts,
                        self.cache_writer.write(self._adjustment_no_data_write(ts_code, trade_date)).status,
                    )

        rows = [rows_by_date[trade_date] for trade_date in trade_dates if trade_date in rows_by_date]
        self._record_cache_event(
            endpoint="adj_factor",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            requested_date_count=len(keys),
            returned_row_count=len(rows),
            fetched_date_count=len(fetch_dates),
            lookup=lookup,
            suppressed_date_count=len(suppressed_dates),
            write_status_counts=write_status_counts,
        )
        return rows

    def calculate_adjusted_return(self, ts_code: str, start_date: str, end_date: str, data_horizon: str) -> float:
        _validate_price_semantics(PriceSemantics(price_mode="return_adjusted", data_horizon=data_horizon))
        prices = {row.trade_date: row for row in self.get_daily_prices(ts_code, start_date, end_date)}
        factors = {row.trade_date: row for row in self.get_adjustment_factors(ts_code, start_date, end_date)}
        start_price = prices[start_date].close
        end_price = prices[end_date].close
        start_factor = factors[start_date].adj_factor
        end_factor = factors[end_date].adj_factor
        denominator = start_price * start_factor
        if denominator == 0:
            return 0
        return (end_price * end_factor) / denominator - 1

    def _trading_days_between(self, start_date: str, end_date: str) -> list[str]:
        resolver = getattr(self.provider_client, "get_trading_days_between", None)
        if callable(resolver):
            return sorted(str(date) for date in resolver(start_date, end_date))
        private_resolver = getattr(self.provider_client, "_trading_days_between")
        return sorted(str(date) for date in private_resolver(start_date, end_date))

    def _daily_key(self, ts_code: str, trade_date: str) -> CacheKey:
        return CacheKey(
            provider=self.provider,
            endpoint="daily",
            instrument_id=ts_code,
            date_key=trade_date,
            date_key_role="trade_date",
            semantic_params={
                "schema_version": 1,
                "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"],
                "price_adjustment": "none",
                "asset": "E",
                "freq": "D",
            },
        )

    def _chip_key(self, ts_code: str, trade_date: str) -> CacheKey:
        return CacheKey(
            provider=self.provider,
            endpoint="cyq_chips",
            instrument_id=ts_code,
            date_key=trade_date,
            date_key_role="trade_date",
            semantic_params={
                "schema_version": 1,
                "fields": ["ts_code", "trade_date", "price", "percent"],
            },
        )

    def _adjustment_key(self, ts_code: str, trade_date: str) -> CacheKey:
        return CacheKey(
            provider=self.provider,
            endpoint="adj_factor",
            instrument_id=ts_code,
            date_key=trade_date,
            date_key_role="trade_date",
            semantic_params={
                "schema_version": 1,
                "fields": ["ts_code", "trade_date", "adj_factor"],
                "asset": "E",
            },
        )

    def _daily_write(self, row: DailyPriceBar) -> CacheWrite:
        return CacheWrite(
            key=self._daily_key(row.ts_code, row.trade_date),
            payload_kind=CachePayloadKind.ROWS,
            payload=row.model_dump(mode="json"),
            source_params={"ts_code": row.ts_code, "trade_date": row.trade_date},
            fetched_at=_now_iso(),
            provider_updated_at=None,
            cache_schema_version=1,
        )

    def _daily_no_data_write(self, ts_code: str, trade_date: str) -> CacheWrite:
        return CacheWrite(
            key=self._daily_key(ts_code, trade_date),
            payload_kind=CachePayloadKind.PROVISIONAL_NO_DATA,
            payload={"reason": "provider_returned_no_row", "ts_code": ts_code, "trade_date": trade_date},
            source_params={"ts_code": ts_code, "trade_date": trade_date},
            fetched_at=_now_iso(),
            provider_updated_at=None,
            cache_schema_version=1,
        )

    def _chip_write(self, ts_code: str, trade_date: str, rows: list[ChipDistributionPoint]) -> CacheWrite:
        return CacheWrite(
            key=self._chip_key(ts_code, trade_date),
            payload_kind=CachePayloadKind.ROWS,
            payload=[row.model_dump(mode="json") for row in rows],
            source_params={"ts_code": ts_code, "trade_date": trade_date},
            fetched_at=_now_iso(),
            provider_updated_at=None,
            cache_schema_version=1,
        )

    def _chip_no_data_write(self, ts_code: str, trade_date: str) -> CacheWrite:
        return CacheWrite(
            key=self._chip_key(ts_code, trade_date),
            payload_kind=CachePayloadKind.PROVISIONAL_NO_DATA,
            payload={"reason": "provider_returned_no_row", "ts_code": ts_code, "trade_date": trade_date},
            source_params={"ts_code": ts_code, "trade_date": trade_date},
            fetched_at=_now_iso(),
            provider_updated_at=None,
            cache_schema_version=1,
        )

    def _adjustment_write(self, row: AdjustmentFactor) -> CacheWrite:
        return CacheWrite(
            key=self._adjustment_key(row.ts_code, row.trade_date),
            payload_kind=CachePayloadKind.ROWS,
            payload=row.model_dump(mode="json"),
            source_params={"ts_code": row.ts_code, "trade_date": row.trade_date},
            fetched_at=_now_iso(),
            provider_updated_at=None,
            cache_schema_version=1,
        )

    def _adjustment_no_data_write(self, ts_code: str, trade_date: str) -> CacheWrite:
        return CacheWrite(
            key=self._adjustment_key(ts_code, trade_date),
            payload_kind=CachePayloadKind.PROVISIONAL_NO_DATA,
            payload={"reason": "provider_returned_no_row", "ts_code": ts_code, "trade_date": trade_date},
            source_params={"ts_code": ts_code, "trade_date": trade_date},
            fetched_at=_now_iso(),
            provider_updated_at=None,
            cache_schema_version=1,
        )

    def _record_cache_event(
        self,
        *,
        endpoint: str,
        ts_code: str,
        start_date: str,
        end_date: str,
        requested_date_count: int,
        returned_row_count: int,
        fetched_date_count: int,
        lookup: Any,
        suppressed_date_count: int,
        write_status_counts: dict[str, int],
    ) -> None:
        if self.cache_event_handler is None:
            return
        self.cache_event_handler(
            {
                "provider": self.provider,
                "endpoint": endpoint,
                "ts_code": ts_code,
                "start_date": start_date,
                "end_date": end_date,
                "requested_date_count": requested_date_count,
                "hit_count": len(lookup.hits),
                "miss_count": len(lookup.misses),
                "stale_count": len(lookup.stale),
                "suppressed_no_data_count": suppressed_date_count,
                "fetched_date_count": fetched_date_count,
                "returned_row_count": returned_row_count,
                "write_status_counts": dict(sorted(write_status_counts.items())),
            }
        )


def _daily_bar_from_payload(payload: object) -> DailyPriceBar:
    if not isinstance(payload, dict):
        raise ValueError("Cached daily payload must be an object.")
    return DailyPriceBar(**payload)


def _chip_point_from_payload(payload: object) -> ChipDistributionPoint:
    if not isinstance(payload, dict):
        raise ValueError("Cached chip payload must be an object.")
    return ChipDistributionPoint(**payload)


def _adjustment_factor_from_payload(payload: object) -> AdjustmentFactor:
    if not isinstance(payload, dict):
        raise ValueError("Cached adjustment factor payload must be an object.")
    return AdjustmentFactor(**payload)


def _validate_price_semantics(semantics: PriceSemantics) -> None:
    if not _is_yyyymmdd(semantics.data_horizon):
        raise ValueError("data_horizon must use YYYYMMDD format.")
    if semantics.price_mode in {"raw", "return_adjusted"}:
        if semantics.adjustment_anchor_date is not None:
            raise ValueError(f"adjustment_anchor_date is not allowed for {semantics.price_mode}.")
        return
    if semantics.price_mode in {"qfq", "hfq"}:
        if semantics.adjustment_anchor_date is None:
            raise ValueError(f"adjustment_anchor_date is required for {semantics.price_mode}.")
        if not _is_yyyymmdd(semantics.adjustment_anchor_date):
            raise ValueError("adjustment_anchor_date must use YYYYMMDD format.")
        if semantics.adjustment_anchor_date > semantics.data_horizon:
            raise ValueError("adjustment_anchor_date must not be later than data_horizon.")
        return
    raise ValueError(f"Unsupported price_mode: {semantics.price_mode}")


def _record_write_status(counts: dict[str, int], status: str) -> None:
    counts[status] = counts.get(status, 0) + 1


def _is_yyyymmdd(value: str) -> bool:
    return len(value) == 8 and value.isdigit()


def _date_ranges(dates: list[str]) -> list[tuple[str, str]]:
    if not dates:
        return []
    sorted_dates = sorted(dates)
    ranges: list[tuple[str, str]] = []
    start = sorted_dates[0]
    previous = sorted_dates[0]
    for current in sorted_dates[1:]:
        if _is_next_calendar_day(previous, current):
            previous = current
            continue
        ranges.append((start, previous))
        start = current
        previous = current
    ranges.append((start, previous))
    return ranges


def _dates_between(dates: list[str], start: str, end: str) -> list[str]:
    return [date for date in sorted(dates) if start <= date <= end]


def _is_next_calendar_day(left: str, right: str) -> bool:
    left_date = datetime.strptime(left, "%Y%m%d").date()
    right_date = datetime.strptime(right, "%Y%m%d").date()
    return (right_date - left_date).days == 1


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
