from app.domain.models import BacktestRequest, ChipDistributionPoint, DailyPriceBar
from app.services.backtest_service import BacktestService


class FakeBacktestClient:
    def get_stock_name(self, ts_code: str) -> str:
        return "贵州茅台"

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        rows: list[ChipDistributionPoint] = []
        for index, trade_date in enumerate(self._dates()):
            peak = 100 + index
            rows.extend(
                [
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=peak - 2, percent=10),
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=peak, percent=55),
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=peak + 2, percent=18),
                ]
            )
        return rows

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        closes = [100, 101, 102, 103, 104, 108, 112, 116, 114, 110, 106, 102]
        return [
            DailyPriceBar(
                ts_code=ts_code,
                trade_date=trade_date,
                open=close - 0.2,
                high=close + 0.4,
                low=close - 0.4,
                close=close,
                pre_close=None,
                pct_chg=None,
                vol=1000,
                amount=10000,
            )
            for trade_date, close in zip(self._dates(), closes, strict=True)
        ]

    def _dates(self) -> list[str]:
        return [
            "20260401",
            "20260402",
            "20260403",
            "20260407",
            "20260408",
            "20260409",
            "20260410",
            "20260413",
            "20260414",
            "20260415",
            "20260416",
            "20260417",
        ]


def test_backtest_service_simulates_long_only_trades() -> None:
    service = BacktestService(FakeBacktestClient())

    result = service.run(
        BacktestRequest(
            stock_code="600519",
            start_date="20260401",
            end_date="20260417",
            n_days=5,
            initial_cash=100000,
        )
    )

    assert result.ts_code == "600519.SH"
    assert result.stock_name == "贵州茅台"
    assert result.summary.initial_cash == 100000
    assert result.summary.final_value > 0
    assert result.summary.signal_count == 7
    assert result.summary.trade_count >= 1
    assert result.equity_curve[-1].trade_date == "20260417"
    assert all(point.portfolio_value > 0 for point in result.equity_curve)
