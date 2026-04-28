from app.domain.models import ChipDistributionPoint, DailyPriceBar
from app.strategies.composite import evaluate_composite_signal
from app.strategies.features import build_market_features


def chips_for_day(ts_code: str, trade_date: str, rows: list[tuple[float, float]]) -> list[ChipDistributionPoint]:
    return [
        ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=price, percent=percent)
        for price, percent in rows
    ]


def price_bar(ts_code: str, trade_date: str, close: float) -> DailyPriceBar:
    return DailyPriceBar(
        ts_code=ts_code,
        trade_date=trade_date,
        open=close - 0.1,
        high=close + 0.2,
        low=close - 0.2,
        close=close,
        pre_close=close - 0.1,
        pct_chg=None,
        vol=1000,
        amount=10000,
    )


def test_composite_signal_buys_breakout_above_dominant_chip_peak() -> None:
    ts_code = "600519.SH"
    chips = chips_for_day(
        ts_code,
        "20260424",
        [(95, 8), (100, 48), (105, 20), (110, 8)],
    )
    bars = [
        price_bar(ts_code, "20260413", 101),
        price_bar(ts_code, "20260414", 102),
        price_bar(ts_code, "20260415", 103),
        price_bar(ts_code, "20260416", 104),
        price_bar(ts_code, "20260417", 105),
        price_bar(ts_code, "20260420", 106),
        price_bar(ts_code, "20260421", 107),
        price_bar(ts_code, "20260422", 108),
        price_bar(ts_code, "20260423", 110),
        price_bar(ts_code, "20260424", 112),
    ]

    signal = evaluate_composite_signal(build_market_features(ts_code, chips, bars))

    assert signal.action == "BUY"
    assert signal.confidence >= 0.6
    assert any("主要筹码峰" in reason for reason in signal.reasons)


def test_composite_signal_sells_breakdown_below_cost_basis() -> None:
    ts_code = "000001.SZ"
    chips = chips_for_day(
        ts_code,
        "20260424",
        [(9.5, 12), (10.0, 55), (10.5, 18), (11.0, 6)],
    )
    closes = [10.4, 10.2, 10.1, 9.9, 9.8, 9.6, 9.4, 9.2, 9.1, 8.9]
    bars = [price_bar(ts_code, f"202604{13 + index:02d}", close) for index, close in enumerate(closes)]

    signal = evaluate_composite_signal(build_market_features(ts_code, chips, bars))

    assert signal.action == "SELL"
    assert signal.confidence >= 0.6
    assert any("低于加权筹码成本" in reason for reason in signal.reasons)


def test_composite_signal_holds_mixed_sideways_setup() -> None:
    ts_code = "300750.SZ"
    chips = chips_for_day(
        ts_code,
        "20260424",
        [(188, 18), (190, 28), (192, 24), (195, 16)],
    )
    closes = [190, 190.4, 190.1, 190.8, 190.5, 190.7, 190.4, 190.9, 191.0, 190.6]
    bars = [price_bar(ts_code, f"202604{13 + index:02d}", close) for index, close in enumerate(closes)]

    signal = evaluate_composite_signal(build_market_features(ts_code, chips, bars))

    assert signal.action == "HOLD"
    assert 0.35 <= signal.confidence <= 0.65
