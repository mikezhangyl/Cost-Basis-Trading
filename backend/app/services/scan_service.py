from typing import Protocol
from uuid import uuid4

from app.domain.errors import DataErrorCode, DataUnavailableError
from app.domain.models import (
    ChipDistributionPoint,
    DailyPriceBar,
    DataQuality,
    ScanRequest,
    ScanResponse,
    StockScanResult,
    StrategySignal,
)
from app.services.code_normalizer import normalize_ts_code
from app.strategies.composite import evaluate_composite_signal
from app.strategies.features import build_market_features


class MarketDataClient(Protocol):
    def resolve_trading_days(self, end_date: str | None, n_days: int) -> list[str]:
        ...

    def get_stock_name(self, ts_code: str) -> str | None:
        ...

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        ...

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        ...


class ScanService:
    def __init__(self, market_data_client: MarketDataClient) -> None:
        self.market_data_client = market_data_client

    def scan(self, request: ScanRequest) -> ScanResponse:
        trading_days = self.market_data_client.resolve_trading_days(request.end_date, request.n_days)
        start_date = trading_days[0] if trading_days else request.end_date
        end_date = trading_days[-1] if trading_days else request.end_date
        results = [self._scan_one(raw_code, start_date, end_date) for raw_code in request.stock_codes]
        return ScanResponse.create(scan_id=str(uuid4()), n_days=request.n_days, results=results)

    def _scan_one(self, raw_code: str, start_date: str | None, end_date: str | None) -> StockScanResult:
        ts_code = raw_code
        try:
            ts_code = normalize_ts_code(raw_code)
            if start_date is None or end_date is None:
                raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "No trading days resolved for request.")

            stock_name = self.market_data_client.get_stock_name(ts_code)
            chips = self.market_data_client.get_chip_distribution(ts_code, start_date, end_date)
            prices = self.market_data_client.get_daily_prices(ts_code, start_date, end_date)
            if not chips or not prices:
                raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "Tushare returned no chip or price data.")

            signal = evaluate_composite_signal(build_market_features(ts_code, chips, prices))
            return StockScanResult(
                ts_code=ts_code,
                stock_name=stock_name,
                date_range={"start_date": start_date, "end_date": end_date},
                signal=signal,
                strategy_signals=[signal],
                data_quality=DataQuality(status="OK"),
                row_counts={"chip_points": len(chips), "price_bars": len(prices)},
            )
        except DataUnavailableError as error:
            return self._error_result(ts_code, start_date, end_date, error)

    def _error_result(
        self,
        ts_code: str,
        start_date: str | None,
        end_date: str | None,
        error: DataUnavailableError,
    ) -> StockScanResult:
        signal = StrategySignal(
            strategy_name="trend_confirmed_chip_signal",
            action="HOLD",
            confidence=0,
            reasons=[error.message],
            features={},
        )
        return StockScanResult(
            ts_code=ts_code,
            stock_name=None,
            date_range={"start_date": start_date, "end_date": end_date},
            signal=signal,
            strategy_signals=[signal],
            data_quality=DataQuality(status="ERROR", message=error.message, error_code=error.code),
            row_counts={"chip_points": 0, "price_bars": 0},
        )
