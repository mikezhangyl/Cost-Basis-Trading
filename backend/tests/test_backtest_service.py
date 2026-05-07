import pytest

from app.domain.models import AdjustmentFactor, BacktestRequest, ChipDistributionPoint, DailyPriceBar
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
        volumes = [
            1000,
            1000,
            1000,
            1000,
            1500,
            1600,
            1700,
            1800,
            1600,
            1500,
            1400,
            1300,
            1400,
            1500,
            1600,
            1700,
            1800,
            1900,
            2000,
            2100,
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
                vol=volume,
                amount=volume * 10,
            )
            for trade_date, close, volume in zip(self._dates(), closes, volumes, strict=True)
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


class MissingFuturePriceBacktestClient(FakeBacktestClient):
    def resolve_trading_days_from(self, start_date: str, n_days: int) -> list[str]:
        return [f"202605{day:02d}" for day in range(1, min(n_days, 31) + 1)]

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        return [
            DailyPriceBar(
                ts_code=ts_code,
                trade_date=f"202605{day:02d}",
                open=10 + day,
                high=10.5 + day,
                low=9.5 + day,
                close=10 + day,
                vol=1000,
                amount=10000,
            )
            for day in range(1, 11)
        ]


class CorporateActionBacktestClient(FakeBacktestClient):
    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        bars = super().get_daily_prices(ts_code, start_date, end_date)
        return [
            bar.model_copy(update={"close": 54, "open": 53.8, "high": 54.4, "low": 53.6})
            if bar.trade_date == "20260409"
            else bar
            for bar in bars
        ]

    def get_adjustment_factors(self, ts_code: str, start_date: str, end_date: str) -> list[AdjustmentFactor]:
        return [
            AdjustmentFactor(
                ts_code=ts_code,
                trade_date=trade_date,
                adj_factor=2 if trade_date >= "20260409" else 1,
            )
            for trade_date in self._dates()
            if start_date <= trade_date <= end_date
        ]

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        return [
            ChipDistributionPoint(ts_code=ts_code, trade_date=f"202605{day:02d}", price=10 + day, percent=30)
            for day in range(1, 6)
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
    assert [observation.offset_days for observation in result.observations] == [1, 3, 5, 15, 30, 60, 90, 180]
    assert result.signal.action == "HOLD"
    assert result.observations[0].signal_close == 104
    assert result.observations[0].observation_close == 108
    assert result.observations[0].period_return == 0.038461538461538464
    assert result.observations[0].match_label == "NEUTRAL"
    assert result.observations[3].offset_days == 15
    assert result.observations[3].observation_date == "20260429"
    assert result.observations[4].offset_days == 30
    assert result.observations[4].match_label == "N/A"
    assert result.observations[4].observation_date is None
    assert result.observations[4].observation_close is None
    assert result.observations[4].period_return is None
    assert result.observations[4].interpretation == "N+30 未来交易日不足，暂无法观察。"
    assert result.market_context.price_return == 0.04
    assert result.market_context.volume_ratio_5 == 1.3636363636363635
    assert result.market_context.amount_ratio_5 == 1.3636363636363635
    assert result.market_context.volume_trend == 0.5
    assert result.market_context.close_vs_ma5 == pytest.approx(0.0196078431372549)
    assert result.market_context.doji_count == 0
    assert result.market_context.bullish_candle_count == 5
    assert result.market_context.bearish_candle_count == 0


def test_backtest_service_marks_observation_na_when_future_price_bar_is_missing() -> None:
    service = BacktestService(MissingFuturePriceBacktestClient())

    result = service.run(
        BacktestRequest(
            stock_code="000001",
            start_date="20260501",
            window_days=5,
        )
    )

    assert result.observations[0].offset_days == 1
    assert result.observations[0].observation_date == "20260506"
    assert result.observations[0].period_return is not None
    assert result.observations[3].offset_days == 15
    assert result.observations[3].observation_date is None
    assert result.observations[3].period_return is None
    assert result.observations[3].match_label == "N/A"


def test_backtest_service_uses_adjustment_factors_for_forward_returns() -> None:
    service = BacktestService(CorporateActionBacktestClient())

    result = service.run(
        BacktestRequest(
            stock_code="600519",
            start_date="20260401",
            window_days=5,
        )
    )

    assert result.observations[0].signal_close == 104
    assert result.observations[0].observation_close == 54
    assert result.observations[0].period_return == pytest.approx(0.038461538461538464)
    assert result.market_context.price_return == pytest.approx(0.04)
