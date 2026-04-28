import pytest

from app.domain.errors import DataErrorCode, DataUnavailableError
from app.domain.models import ChipDistributionPoint, DailyPriceBar, ScanRequest
from app.services.scan_service import ScanService


class FakeMarketDataClient:
    def __init__(self, *, chip_error: Exception | None = None) -> None:
        self.chip_error = chip_error

    def resolve_trading_days(self, end_date: str | None, n_days: int) -> list[str]:
        return [
            "20260413",
            "20260414",
            "20260415",
            "20260416",
            "20260417",
            "20260420",
            "20260421",
            "20260422",
            "20260423",
            "20260424",
        ][-n_days:]

    def get_stock_name(self, ts_code: str) -> str:
        return {"600519.SH": "贵州茅台"}.get(ts_code, ts_code)

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        if self.chip_error:
            raise self.chip_error
        return [
            ChipDistributionPoint(ts_code=ts_code, trade_date=end_date, price=100, percent=52),
            ChipDistributionPoint(ts_code=ts_code, trade_date=end_date, price=105, percent=20),
            ChipDistributionPoint(ts_code=ts_code, trade_date=end_date, price=110, percent=10),
        ]

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        closes = [101, 102, 103, 104, 105, 106, 107, 108, 110, 112]
        return [
            DailyPriceBar(
                ts_code=ts_code,
                trade_date=f"202604{13 + index:02d}",
                open=close - 0.1,
                high=close + 0.2,
                low=close - 0.2,
                close=close,
                pre_close=close - 0.1,
                pct_chg=None,
                vol=1000,
                amount=10000,
            )
            for index, close in enumerate(closes)
        ]


def test_scan_service_returns_per_stock_signal() -> None:
    service = ScanService(FakeMarketDataClient())

    result = service.scan(ScanRequest(stock_codes=["600519.SH"], n_days=10))

    assert result.n_days == 10
    assert result.results[0].ts_code == "600519.SH"
    assert result.results[0].stock_name == "贵州茅台"
    assert result.results[0].signal.action == "BUY"
    assert result.results[0].data_quality.status == "OK"
    assert result.results[0].row_counts["chip_points"] == 3


@pytest.mark.parametrize("raw_code,normalized", [("600519", "600519.SH"), ("000001", "000001.SZ")])
def test_scan_service_accepts_bare_six_digit_codes(raw_code: str, normalized: str) -> None:
    service = ScanService(FakeMarketDataClient())

    result = service.scan(ScanRequest(stock_codes=[raw_code], n_days=10))

    assert result.results[0].ts_code == normalized


def test_scan_service_reports_permission_error_per_stock() -> None:
    service = ScanService(
        FakeMarketDataClient(chip_error=DataUnavailableError(DataErrorCode.NO_PERMISSION, "cyq_chips permission missing"))
    )

    result = service.scan(ScanRequest(stock_codes=["600519.SH"], n_days=10))

    assert result.results[0].signal.action == "HOLD"
    assert result.results[0].data_quality.status == "ERROR"
    assert result.results[0].data_quality.error_code == DataErrorCode.NO_PERMISSION


def test_scan_request_rejects_large_lookback() -> None:
    with pytest.raises(ValueError):
        ScanRequest(stock_codes=["600519.SH"], n_days=999)
