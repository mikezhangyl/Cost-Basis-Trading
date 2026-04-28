from dataclasses import dataclass

from app.domain.models import ChipDistributionPoint, DailyPriceBar


@dataclass(frozen=True)
class MarketFeatures:
    ts_code: str
    latest_trade_date: str | None
    latest_close: float | None
    n_day_return: float | None
    max_drawdown: float | None
    volatility: float | None
    dominant_peak_price: float | None
    dominant_peak_percent: float | None
    weighted_chip_cost: float | None
    percent_below_close: float | None
    percent_above_close: float | None
    chip_point_count: int
    price_bar_count: int

    def as_signal_features(self) -> dict[str, float | str | None]:
        return {
            "latest_trade_date": self.latest_trade_date,
            "latest_close": self.latest_close,
            "n_day_return": self.n_day_return,
            "max_drawdown": self.max_drawdown,
            "volatility": self.volatility,
            "dominant_peak_price": self.dominant_peak_price,
            "dominant_peak_percent": self.dominant_peak_percent,
            "weighted_chip_cost": self.weighted_chip_cost,
            "percent_below_close": self.percent_below_close,
            "percent_above_close": self.percent_above_close,
            "chip_point_count": self.chip_point_count,
            "price_bar_count": self.price_bar_count,
        }


def build_market_features(
    ts_code: str,
    chip_points: list[ChipDistributionPoint],
    price_bars: list[DailyPriceBar],
) -> MarketFeatures:
    sorted_bars = sorted(price_bars, key=lambda bar: bar.trade_date)
    latest_close = sorted_bars[-1].close if sorted_bars else None
    latest_trade_date = sorted_bars[-1].trade_date if sorted_bars else None
    n_day_return = _calculate_return(sorted_bars)
    max_drawdown = _calculate_max_drawdown(sorted_bars)
    volatility = _calculate_volatility(sorted_bars)

    latest_chip_date = max((point.trade_date for point in chip_points), default=None)
    latest_chip_points = [point for point in chip_points if point.trade_date == latest_chip_date]
    dominant_peak = max(latest_chip_points, key=lambda point: point.percent, default=None)
    weighted_chip_cost = _weighted_chip_cost(latest_chip_points)
    percent_below_close = _percent_below_close(latest_chip_points, latest_close)
    percent_above_close = _percent_above_close(latest_chip_points, latest_close)

    return MarketFeatures(
        ts_code=ts_code,
        latest_trade_date=latest_trade_date,
        latest_close=latest_close,
        n_day_return=n_day_return,
        max_drawdown=max_drawdown,
        volatility=volatility,
        dominant_peak_price=dominant_peak.price if dominant_peak else None,
        dominant_peak_percent=dominant_peak.percent if dominant_peak else None,
        weighted_chip_cost=weighted_chip_cost,
        percent_below_close=percent_below_close,
        percent_above_close=percent_above_close,
        chip_point_count=len(chip_points),
        price_bar_count=len(price_bars),
    )


def _calculate_return(price_bars: list[DailyPriceBar]) -> float | None:
    if len(price_bars) < 2:
        return None
    first_close = price_bars[0].close
    latest_close = price_bars[-1].close
    if first_close == 0:
        return None
    return (latest_close - first_close) / first_close


def _calculate_max_drawdown(price_bars: list[DailyPriceBar]) -> float | None:
    if not price_bars:
        return None
    peak = price_bars[0].close
    max_drawdown = 0.0
    for bar in price_bars:
        peak = max(peak, bar.close)
        if peak > 0:
            max_drawdown = min(max_drawdown, (bar.close - peak) / peak)
    return max_drawdown


def _calculate_volatility(price_bars: list[DailyPriceBar]) -> float | None:
    if len(price_bars) < 2:
        return None
    returns = []
    for previous, current in zip(price_bars, price_bars[1:], strict=False):
        if previous.close != 0:
            returns.append((current.close - previous.close) / previous.close)
    if not returns:
        return None
    average = sum(returns) / len(returns)
    variance = sum((item - average) ** 2 for item in returns) / len(returns)
    return variance**0.5


def _weighted_chip_cost(chip_points: list[ChipDistributionPoint]) -> float | None:
    total_percent = sum(point.percent for point in chip_points)
    if total_percent <= 0:
        return None
    return sum(point.price * point.percent for point in chip_points) / total_percent


def _percent_below_close(chip_points: list[ChipDistributionPoint], latest_close: float | None) -> float | None:
    if latest_close is None:
        return None
    return sum(point.percent for point in chip_points if point.price < latest_close)


def _percent_above_close(chip_points: list[ChipDistributionPoint], latest_close: float | None) -> float | None:
    if latest_close is None:
        return None
    return sum(point.percent for point in chip_points if point.price > latest_close)
