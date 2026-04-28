from app.domain.models import BacktestRequest, ChipDistributionPoint, DailyPriceBar
from app.services.backtest_service import BacktestService


class FakeBacktestClient:
    def resolve_trading_days_from(self, start_date: str, n_days: int) -> list[str]:
        return self._dates()[:n_days]

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
        closes = [
            100,
            101,
            102,
            103,
            104,
            108,
            112,
            116,
            114,
            110,
            106,
            102,
            104,
            106,
            108,
            110,
            112,
            114,
            116,
            118,
        ]
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
            "20260420",
            "20260421",
            "20260422",
            "20260423",
            "20260424",
            "20260427",
            "20260428",
            "20260429",
        ]


def test_backtest_service_evaluates_single_window_and_forward_observations() -> None:
    service = BacktestService(FakeBacktestClient())

    result = service.run(
        BacktestRequest(
            stock_code="600519",
            start_date="20260401",
            window_days=5,
        )
    )

    assert result.ts_code == "600519.SH"
    assert result.stock_name == "贵州茅台"
    assert result.window_days == 5
    assert result.analysis_range == {"start_date": "20260401", "end_date": "20260408"}
    assert result.signal_date == "20260408"
    assert [observation.offset_days for observation in result.observations] == [3, 7, 15]
    assert result.signal.action == "HOLD"
    assert result.observations[0].signal_close == 104
    assert result.observations[0].observation_close == 116
    assert result.observations[0].period_return == 0.11538461538461539
    assert result.observations[0].match_label == "NEUTRAL"
