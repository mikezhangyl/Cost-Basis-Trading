from app.domain.models import DailyPriceBar, MarketContextFeatureSet


def build_market_context(price_bars: list[DailyPriceBar]) -> MarketContextFeatureSet:
    sorted_bars = sorted(price_bars, key=lambda bar: bar.trade_date)
    if not sorted_bars:
        return MarketContextFeatureSet(
            price_return=None,
            volume_ratio_5=None,
            amount_ratio_5=None,
            volume_trend=None,
            close_vs_ma5=None,
            close_vs_ma10=None,
            doji_count=0,
            bullish_candle_count=0,
            bearish_candle_count=0,
            long_upper_shadow_count=0,
            long_lower_shadow_count=0,
            context_summary="缺少日线行情，无法判断量价与K线结构。",
        )

    latest = sorted_bars[-1]
    return MarketContextFeatureSet(
        price_return=_safe_return(sorted_bars[0].close, latest.close),
        volume_ratio_5=_latest_ratio(sorted_bars, "vol", 5),
        amount_ratio_5=_latest_ratio(sorted_bars, "amount", 5),
        volume_trend=_safe_return(_numeric_value(sorted_bars[0].vol), _numeric_value(latest.vol)),
        close_vs_ma5=_close_vs_average(sorted_bars, 5),
        close_vs_ma10=_close_vs_average(sorted_bars, 10),
        doji_count=sum(1 for bar in sorted_bars if _is_doji(bar)),
        bullish_candle_count=sum(1 for bar in sorted_bars if bar.close > bar.open),
        bearish_candle_count=sum(1 for bar in sorted_bars if bar.close < bar.open),
        long_upper_shadow_count=sum(1 for bar in sorted_bars if _upper_shadow_ratio(bar) >= 0.5),
        long_lower_shadow_count=sum(1 for bar in sorted_bars if _lower_shadow_ratio(bar) >= 0.5),
        context_summary=_summarize_context(sorted_bars),
    )


def _numeric_value(value: float | None) -> float | None:
    return value if value is not None else None


def _safe_return(start_value: float | None, end_value: float | None) -> float | None:
    if start_value is None or end_value is None or start_value == 0:
        return None
    return (end_value - start_value) / start_value


def _latest_ratio(price_bars: list[DailyPriceBar], field_name: str, window: int) -> float | None:
    values = [_numeric_value(getattr(bar, field_name)) for bar in price_bars[-window:]]
    numeric_values = [value for value in values if value is not None]
    if not numeric_values:
        return None
    average = sum(numeric_values) / len(numeric_values)
    latest = numeric_values[-1]
    if average == 0:
        return None
    return latest / average


def _close_vs_average(price_bars: list[DailyPriceBar], window: int) -> float | None:
    if len(price_bars) < window:
        return None
    closes = [bar.close for bar in price_bars[-window:]]
    average = sum(closes) / len(closes)
    if average == 0:
        return None
    return price_bars[-1].close / average - 1


def _candle_range(bar: DailyPriceBar) -> float:
    return max(0, bar.high - bar.low)


def _body_ratio(bar: DailyPriceBar) -> float:
    candle_range = _candle_range(bar)
    if candle_range == 0:
        return 0
    return abs(bar.close - bar.open) / candle_range


def _upper_shadow_ratio(bar: DailyPriceBar) -> float:
    candle_range = _candle_range(bar)
    if candle_range == 0:
        return 0
    return (bar.high - max(bar.open, bar.close)) / candle_range


def _lower_shadow_ratio(bar: DailyPriceBar) -> float:
    candle_range = _candle_range(bar)
    if candle_range == 0:
        return 0
    return (min(bar.open, bar.close) - bar.low) / candle_range


def _is_doji(bar: DailyPriceBar) -> bool:
    return _body_ratio(bar) <= 0.1


def _summarize_context(price_bars: list[DailyPriceBar]) -> str:
    price_return = _safe_return(price_bars[0].close, price_bars[-1].close)
    volume_ratio = _latest_ratio(price_bars, "vol", 5)
    doji_count = sum(1 for bar in price_bars if _is_doji(bar))
    bullish_count = sum(1 for bar in price_bars if bar.close > bar.open)
    bearish_count = sum(1 for bar in price_bars if bar.close < bar.open)

    volume_text = "量能缺失"
    if volume_ratio is not None:
        volume_text = "量能高于窗口均量" if volume_ratio >= 1 else "量能低于窗口均量"

    trend_text = "价格趋势缺失"
    if price_return is not None:
        trend_text = "价格趋势为正" if price_return >= 0 else "价格趋势为负"

    if doji_count >= max(2, len(price_bars) // 3):
        candle_text = "K 线十字星较多"
    elif bullish_count > bearish_count:
        candle_text = "K 线以阳线为主"
    elif bearish_count > bullish_count:
        candle_text = "K 线以阴线为主"
    else:
        candle_text = "K 线多空接近"

    return f"{volume_text}，{trend_text}，{candle_text}。"
