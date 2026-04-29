from app.domain.models import MarketContextFeatureSet, StrategySignal

COMPOSITE_BASELINE = "composite_baseline"
MARKET_CONTEXT_FOLLOWTHROUGH = "market_context_followthrough"
SUPPORTED_RESEARCH_STRATEGIES = [COMPOSITE_BASELINE, MARKET_CONTEXT_FOLLOWTHROUGH]


def build_research_strategy_signals(
    baseline_signal: StrategySignal,
    market_context: MarketContextFeatureSet,
    strategy_ids: list[str],
) -> list[tuple[str, StrategySignal]]:
    signals: list[tuple[str, StrategySignal]] = []
    for strategy_id in strategy_ids:
        if strategy_id == COMPOSITE_BASELINE:
            signals.append((strategy_id, _as_baseline_signal(baseline_signal)))
        elif strategy_id == MARKET_CONTEXT_FOLLOWTHROUGH:
            signals.append((strategy_id, _evaluate_market_context_followthrough(market_context)))
    return signals


def _as_baseline_signal(signal: StrategySignal) -> StrategySignal:
    return StrategySignal(
        strategy_name=COMPOSITE_BASELINE,
        action=signal.action,
        confidence=signal.confidence,
        reasons=signal.reasons,
        features=signal.features,
    )


def _evaluate_market_context_followthrough(context: MarketContextFeatureSet) -> StrategySignal:
    price_return = context.price_return or 0
    volume_ratio = context.volume_ratio_5 or 0
    bullish_edge = context.bullish_candle_count - context.bearish_candle_count

    if price_return > 0.01 and volume_ratio >= 1.05 and bullish_edge > 0:
        action = "BUY"
        confidence = 0.68
        reason = "量能高于 5 日均量，区间价格为正，且阳线数量占优，短线延续条件较清晰。"
    elif price_return < -0.01 and volume_ratio >= 1.05 and bullish_edge <= 0:
        action = "SELL"
        confidence = 0.66
        reason = "放量伴随区间价格走弱，且阳线优势不足，短线风险条件较清晰。"
    else:
        action = "HOLD"
        confidence = 0.52
        reason = "量价与 K 线条件没有形成一致方向，先记录为持有观察。"

    return StrategySignal(
        strategy_name=MARKET_CONTEXT_FOLLOWTHROUGH,
        action=action,
        confidence=confidence,
        reasons=[reason],
        features={
            "price_return": context.price_return,
            "volume_ratio_5": context.volume_ratio_5,
            "amount_ratio_5": context.amount_ratio_5,
            "bullish_candle_count": context.bullish_candle_count,
            "bearish_candle_count": context.bearish_candle_count,
            "doji_count": context.doji_count,
        },
    )
