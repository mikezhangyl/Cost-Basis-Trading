from app.domain.models import StrategySignal
from app.strategies.features import MarketFeatures


def evaluate_composite_signal(features: MarketFeatures) -> StrategySignal:
    if _has_missing_core_features(features):
        return StrategySignal(
            strategy_name="trend_confirmed_chip_signal",
            action="HOLD",
            confidence=0.2,
            reasons=["筹码明细或价格数据不足，暂时无法形成方向性信号。"],
            features=features.as_signal_features(),
        )

    buy_score = 0.0
    sell_score = 0.0
    reasons: list[str] = []

    latest_close = features.latest_close or 0
    dominant_peak_price = features.dominant_peak_price or 0
    weighted_chip_cost = features.weighted_chip_cost or 0
    n_day_return = features.n_day_return or 0
    max_drawdown = features.max_drawdown or 0
    percent_below_close = features.percent_below_close or 0

    if latest_close > dominant_peak_price * 1.03 and n_day_return > 0.03 and max_drawdown > -0.08:
        buy_score += 0.42
        reasons.append("最新价向上突破主要筹码峰，且近 10 日涨幅为正。")

    if latest_close > weighted_chip_cost * 1.02 and n_day_return > 0:
        buy_score += 0.22
        reasons.append("最新价高于加权筹码成本，近期价格趋势偏强。")

    if latest_close < weighted_chip_cost * 0.97 and n_day_return < -0.03:
        sell_score += 0.45
        reasons.append("最新价低于加权筹码成本，且近期收益为负。")

    if latest_close < dominant_peak_price * 0.97 and n_day_return < 0:
        sell_score += 0.3
        reasons.append("价格跌破主要筹码峰支撑区域。")

    if percent_below_close >= 70 and n_day_return < -0.01:
        sell_score += 0.22
        reasons.append("获利筹码占比较高，同时价格走势转弱。")

    action = _select_action(buy_score, sell_score)
    confidence = _confidence_for(action, buy_score, sell_score)
    if not reasons:
        reasons.append("筹码分布和价格趋势信号混合，暂未确认明确方向优势。")

    return StrategySignal(
        strategy_name="trend_confirmed_chip_signal",
        action=action,
        confidence=confidence,
        reasons=reasons,
        features=features.as_signal_features(),
    )


def _has_missing_core_features(features: MarketFeatures) -> bool:
    return (
        features.latest_close is None
        or features.dominant_peak_price is None
        or features.weighted_chip_cost is None
        or features.n_day_return is None
    )


def _select_action(buy_score: float, sell_score: float) -> str:
    if buy_score >= 0.55 and buy_score > sell_score + 0.15:
        return "BUY"
    if sell_score >= 0.55 and sell_score > buy_score + 0.15:
        return "SELL"
    return "HOLD"


def _confidence_for(action: str, buy_score: float, sell_score: float) -> float:
    if action == "BUY":
        return min(0.95, 0.45 + buy_score - sell_score * 0.35)
    if action == "SELL":
        return min(0.95, 0.45 + sell_score - buy_score * 0.35)
    return max(0.35, min(0.65, 0.5 + abs(buy_score - sell_score) * 0.2))
