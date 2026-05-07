from typing import Protocol
from uuid import uuid4

from app.domain.errors import DataErrorCode, DataUnavailableError
from app.domain.models import (
    AdjustmentFactor,
    BacktestObservationPoint,
    BacktestRequest,
    BacktestResponse,
    ChipDistributionPoint,
    DailyPriceBar,
    MarketContextFeatureSet,
)
from app.services.code_normalizer import normalize_ts_code
from app.strategies.composite import evaluate_composite_signal
from app.strategies.features import build_market_features
from app.strategies.market_context import build_market_context

OBSERVATION_OFFSETS = [1, 3, 5, 15, 30, 60, 90, 180]


class BacktestMarketDataClient(Protocol):
    def resolve_trading_days_from(self, start_date: str, n_days: int) -> list[str]:
        ...

    def get_stock_name(self, ts_code: str) -> str | None:
        ...

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        ...

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        ...

    def get_adjustment_factors(self, ts_code: str, start_date: str, end_date: str) -> list[AdjustmentFactor]:
        ...


class BacktestService:
    def __init__(self, market_data_client: BacktestMarketDataClient) -> None:
        self.market_data_client = market_data_client

    def run(self, request: BacktestRequest) -> BacktestResponse:
        ts_code = normalize_ts_code(request.stock_code)
        required_days = request.window_days + max(OBSERVATION_OFFSETS)
        trading_days = self.market_data_client.resolve_trading_days_from(request.start_date, required_days)
        if len(trading_days) < request.window_days:
            raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "Not enough trading days for requested window.")
        analysis_days = trading_days[: request.window_days]
        signal_date = analysis_days[-1]
        observation_dates = {
            offset: _observation_date(trading_days, request.window_days, offset)
            for offset in OBSERVATION_OFFSETS
        }
        final_observation_date = _last_available_observation_date(observation_dates, signal_date)
        stock_name = self.market_data_client.get_stock_name(ts_code)
        prices = sorted(
            self.market_data_client.get_daily_prices(ts_code, analysis_days[0], final_observation_date),
            key=lambda bar: bar.trade_date,
        )
        chips = self.market_data_client.get_chip_distribution(ts_code, analysis_days[0], signal_date)
        analysis_prices = [bar for bar in prices if bar.trade_date in set(analysis_days)]
        signal_bar = _bar_for_date(prices, signal_date)
        if len(analysis_prices) < request.window_days:
            raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "Not enough price bars for requested backtest window.")

        signal = evaluate_composite_signal(build_market_features(ts_code, chips, analysis_prices))
        adjustment_factors = _load_adjustment_factors(
            self.market_data_client,
            ts_code,
            analysis_days[0],
            final_observation_date,
        )
        market_context = _market_context_with_adjusted_return(
            build_market_context(analysis_prices),
            analysis_prices[0],
            signal_bar,
            adjustment_factors,
        )
        observations = [
            _build_observation(
                signal.action,
                signal_bar,
                _optional_bar_for_date(prices, observation_date),
                offset,
                adjustment_factors,
            )
            for offset, observation_date in observation_dates.items()
        ]
        return BacktestResponse.create(
            backtest_id=str(uuid4()),
            ts_code=ts_code,
            stock_name=stock_name,
            analysis_range={"start_date": analysis_days[0], "end_date": signal_date},
            window_days=request.window_days,
            signal_date=signal_date,
            signal=signal,
            market_context=market_context,
            observations=observations,
            row_counts={"chip_points": len(chips), "price_bars": len(analysis_prices)},
        )


def _safe_return(start_value: float, end_value: float) -> float:
    if start_value == 0:
        return 0
    return (end_value - start_value) / start_value


def _load_adjustment_factors(
    market_data_client: BacktestMarketDataClient,
    ts_code: str,
    start_date: str,
    end_date: str,
) -> dict[str, float] | None:
    if getattr(market_data_client, "supports_adjustment_factors", True) is False:
        return None
    get_adjustment_factors = getattr(market_data_client, "get_adjustment_factors", None)
    if not callable(get_adjustment_factors):
        return None
    factors = get_adjustment_factors(ts_code, start_date, end_date)
    return {factor.trade_date: factor.adj_factor for factor in factors}


def _market_context_with_adjusted_return(
    market_context: MarketContextFeatureSet,
    start_bar: DailyPriceBar,
    end_bar: DailyPriceBar,
    adjustment_factors: dict[str, float] | None,
) -> MarketContextFeatureSet:
    if adjustment_factors is None:
        return market_context
    return market_context.model_copy(
        update={"price_return": _period_return(start_bar, end_bar, adjustment_factors)}
    )


def _period_return(
    start_bar: DailyPriceBar,
    end_bar: DailyPriceBar,
    adjustment_factors: dict[str, float] | None,
) -> float:
    if adjustment_factors is None:
        return _safe_return(start_bar.close, end_bar.close)
    start_factor = adjustment_factors.get(start_bar.trade_date)
    end_factor = adjustment_factors.get(end_bar.trade_date)
    if start_factor is None or end_factor is None:
        raise DataUnavailableError(
            DataErrorCode.EMPTY_DATA,
            f"Missing adjustment factor for {start_bar.trade_date} or {end_bar.trade_date}.",
        )
    denominator = start_bar.close * start_factor
    if denominator == 0:
        return 0
    return (end_bar.close * end_factor) / denominator - 1


def _observation_date(trading_days: list[str], window_days: int, offset_days: int) -> str | None:
    observation_index = window_days - 1 + offset_days
    if observation_index >= len(trading_days):
        return None
    return trading_days[observation_index]


def _last_available_observation_date(observation_dates: dict[int, str | None], fallback_date: str) -> str:
    available_dates = [date for date in observation_dates.values() if date is not None]
    return available_dates[-1] if available_dates else fallback_date


def _bar_for_date(prices: list[DailyPriceBar], trade_date: str) -> DailyPriceBar:
    for bar in prices:
        if bar.trade_date == trade_date:
            return bar
    raise DataUnavailableError(DataErrorCode.EMPTY_DATA, f"Missing price bar for {trade_date}.")


def _optional_bar_for_date(prices: list[DailyPriceBar], trade_date: str | None) -> DailyPriceBar | None:
    if trade_date is None:
        return None
    for bar in prices:
        if bar.trade_date == trade_date:
            return bar
    return None


def _build_observation(
    action: str,
    signal_bar: DailyPriceBar,
    observation_bar: DailyPriceBar | None,
    offset_days: int,
    adjustment_factors: dict[str, float] | None = None,
) -> BacktestObservationPoint:
    if observation_bar is None:
        return BacktestObservationPoint(
            offset_days=offset_days,
            observation_date=None,
            signal_close=signal_bar.close,
            observation_close=None,
            period_return=None,
            match_label="N/A",
            interpretation=f"N+{offset_days} 未来交易日不足，暂无法观察。",
        )
    period_return = _period_return(signal_bar, observation_bar, adjustment_factors)
    match_label = _match_label(action, period_return)
    return BacktestObservationPoint(
        offset_days=offset_days,
        observation_date=observation_bar.trade_date,
        signal_close=signal_bar.close,
        observation_close=observation_bar.close,
        period_return=period_return,
        match_label=match_label,
        interpretation=_interpret_observation(action, period_return, offset_days),
    )


def _match_label(action: str, period_return: float) -> str:
    if action == "BUY":
        return "MATCH" if period_return > 0 else "MISMATCH"
    if action == "SELL":
        return "MATCH" if period_return < 0 else "MISMATCH"
    return "NEUTRAL"


def _interpret_observation(action: str, period_return: float, offset_days: int) -> str:
    if action == "BUY":
        return f"N+{offset_days} 上涨，买入建议得到阶段验证。" if period_return > 0 else f"N+{offset_days} 下跌，买入建议阶段未得到验证。"
    if action == "SELL":
        return f"N+{offset_days} 下跌，卖出建议得到阶段验证。" if period_return < 0 else f"N+{offset_days} 上涨，卖出建议阶段未得到验证。"
    return f"建议为持有，N+{offset_days} 用于记录后续走势，不直接判定胜负。"
