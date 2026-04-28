from typing import Protocol
from uuid import uuid4

from app.domain.errors import DataErrorCode, DataUnavailableError
from app.domain.models import (
    BacktestEquityPoint,
    BacktestRequest,
    BacktestResponse,
    BacktestSummary,
    BacktestTrade,
    ChipDistributionPoint,
    DailyPriceBar,
)
from app.services.code_normalizer import normalize_ts_code
from app.strategies.composite import evaluate_composite_signal
from app.strategies.features import build_market_features


class BacktestMarketDataClient(Protocol):
    def get_stock_name(self, ts_code: str) -> str | None:
        ...

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        ...

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        ...


class BacktestService:
    def __init__(self, market_data_client: BacktestMarketDataClient) -> None:
        self.market_data_client = market_data_client

    def run(self, request: BacktestRequest) -> BacktestResponse:
        if request.start_date > request.end_date:
            raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "start_date must be before or equal to end_date.")

        ts_code = normalize_ts_code(request.stock_code)
        stock_name = self.market_data_client.get_stock_name(ts_code)
        prices = sorted(
            self.market_data_client.get_daily_prices(ts_code, request.start_date, request.end_date),
            key=lambda bar: bar.trade_date,
        )
        chips = self.market_data_client.get_chip_distribution(ts_code, request.start_date, request.end_date)
        if len(prices) <= request.n_days:
            raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "Not enough price bars for requested backtest window.")

        chips_by_date = _group_chips_by_date(chips)
        cash = request.initial_cash
        shares = 0
        trades: list[BacktestTrade] = []
        equity_curve: list[BacktestEquityPoint] = []
        signal_count = 0

        for index in range(request.n_days - 1, len(prices) - 1):
            signal_date = prices[index].trade_date
            execution_bar = prices[index + 1]
            window_bars = prices[index - request.n_days + 1 : index + 1]
            window_chips = _chips_for_window(chips_by_date, window_bars)
            signal = evaluate_composite_signal(build_market_features(ts_code, window_chips, window_bars))
            signal_count += 1

            if signal.action == "BUY" and shares == 0:
                buy_shares = int(cash // execution_bar.close)
                if buy_shares > 0:
                    cash -= buy_shares * execution_bar.close
                    shares = buy_shares
                    trades.append(
                        BacktestTrade(
                            trade_date=execution_bar.trade_date,
                            action="BUY",
                            price=execution_bar.close,
                            shares=buy_shares,
                            cash_after=cash,
                            reason=signal.reasons[0],
                        )
                    )
            elif signal.action == "SELL" and shares > 0:
                cash += shares * execution_bar.close
                trades.append(
                    BacktestTrade(
                        trade_date=execution_bar.trade_date,
                        action="SELL",
                        price=execution_bar.close,
                        shares=shares,
                        cash_after=cash,
                        reason=signal.reasons[0],
                    )
                )
                shares = 0

            equity_curve.append(
                BacktestEquityPoint(
                    trade_date=execution_bar.trade_date,
                    close=execution_bar.close,
                    cash=cash,
                    shares=shares,
                    portfolio_value=cash + shares * execution_bar.close,
                    signal_action=signal.action,
                )
            )

        final_value = equity_curve[-1].portfolio_value if equity_curve else request.initial_cash
        summary = BacktestSummary(
            initial_cash=request.initial_cash,
            final_value=final_value,
            total_return=_safe_return(request.initial_cash, final_value),
            benchmark_return=_safe_return(prices[request.n_days].close, prices[-1].close),
            max_drawdown=_max_drawdown([point.portfolio_value for point in equity_curve]),
            trade_count=len(trades),
            signal_count=signal_count,
        )
        return BacktestResponse.create(
            backtest_id=str(uuid4()),
            ts_code=ts_code,
            stock_name=stock_name,
            date_range={"start_date": request.start_date, "end_date": request.end_date},
            n_days=request.n_days,
            summary=summary,
            trades=trades,
            equity_curve=equity_curve,
        )


def _group_chips_by_date(chips: list[ChipDistributionPoint]) -> dict[str, list[ChipDistributionPoint]]:
    grouped: dict[str, list[ChipDistributionPoint]] = {}
    for point in chips:
        grouped.setdefault(point.trade_date, []).append(point)
    return grouped


def _chips_for_window(
    chips_by_date: dict[str, list[ChipDistributionPoint]],
    window_bars: list[DailyPriceBar],
) -> list[ChipDistributionPoint]:
    rows: list[ChipDistributionPoint] = []
    for bar in window_bars:
        rows.extend(chips_by_date.get(bar.trade_date, []))
    return rows


def _safe_return(start_value: float, end_value: float) -> float:
    if start_value == 0:
        return 0
    return (end_value - start_value) / start_value


def _max_drawdown(values: list[float]) -> float:
    if not values:
        return 0
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            drawdown = min(drawdown, (value - peak) / peak)
    return drawdown
