from __future__ import annotations

from math import sqrt
from typing import Literal

from pydantic import BaseModel

from app.domain.models import ChipDistributionPoint, DailyPriceBar

FactorQualityStatus = Literal["OK", "PARTIAL", "INSUFFICIENT_HISTORY", "MISSING_CHIP_DATA", "MISSING_PRICE_DATA"]

FORMULA_VERSION = "chip-factor-v1"


class DailyChipSnapshot(BaseModel):
    ts_code: str
    factor_date: str
    close: float | None
    chip_rows: int
    weighted_chip_cost: float | None
    dominant_peak_price: float | None
    dominant_peak_percent: float | None
    profit_ratio: float | None
    loss_ratio: float | None
    at_close_ratio: float | None
    concentration_width_70: float | None
    concentration_width_90: float | None
    chip_weighted_std: float | None
    cyq_cgo: float | None
    max_input_date: str | None
    quality_status: FactorQualityStatus
    missing_reason: str | None = None


class ChipFactorValue(BaseModel):
    factor_id: str
    factor_date: str
    value: float | None
    quality_status: FactorQualityStatus
    lookback_days: int | None
    formula_version: str = FORMULA_VERSION
    source_level: str
    implementation_type: str
    explanation: str
    max_input_date: str | None


class FactorTrace(BaseModel):
    factor_id: str
    source_level: str
    implementation_type: str
    explanation: str


FACTOR_TRACES: dict[str, FactorTrace] = {
    "profit_ratio_asof": FactorTrace(
        factor_id="profit_ratio_asof",
        source_level="FACTOR_FAMILY",
        implementation_type="exact from Tushare cyq_chips snapshot",
        explanation="估算当前有多少筹码处于盈利状态，即筹码成本低于当日收盘价的占比。",
    ),
    "loss_ratio_asof": FactorTrace(
        factor_id="loss_ratio_asof",
        source_level="FACTOR_FAMILY",
        implementation_type="exact from Tushare cyq_chips snapshot",
        explanation="估算当前有多少筹码处于亏损状态，即筹码成本高于当日收盘价的占比。",
    ),
    "cyq_cgo_asof": FactorTrace(
        factor_id="cyq_cgo_asof",
        source_level="ACADEMIC_FACTOR_PROXY",
        implementation_type="proxy",
        explanation="用 Tushare 筹码成本分布近似平均浮盈/浮亏程度。",
    ),
    "weighted_chip_cost_gap_asof": FactorTrace(
        factor_id="weighted_chip_cost_gap_asof",
        source_level="FACTOR_FAMILY",
        implementation_type="exact from Tushare cyq_chips snapshot",
        explanation="比较当日收盘价与加权平均筹码成本的距离。",
    ),
    "dominant_peak_strength_asof": FactorTrace(
        factor_id="dominant_peak_strength_asof",
        source_level="FACTOR_FAMILY",
        implementation_type="exact from Tushare cyq_chips snapshot",
        explanation="衡量最大的筹码成本峰占比，反映筹码是否集中在某个成本区。",
    ),
    "concentration_width_70_asof": FactorTrace(
        factor_id="concentration_width_70_asof",
        source_level="OPEN_REPRODUCTION / FACTOR_FAMILY",
        implementation_type="project implementation of public chip-concentration factor family",
        explanation="衡量覆盖 70% 筹码所需的最窄价格带宽度，越小代表筹码越集中。",
    ),
    "concentration_width_90_asof": FactorTrace(
        factor_id="concentration_width_90_asof",
        source_level="OPEN_REPRODUCTION / FACTOR_FAMILY",
        implementation_type="project implementation of public chip-concentration factor family",
        explanation="衡量覆盖 90% 筹码所需的最窄价格带宽度，用于观察整体筹码分散程度。",
    ),
    "chip_weighted_std_asof": FactorTrace(
        factor_id="chip_weighted_std_asof",
        source_level="FACTOR_FAMILY",
        implementation_type="exact statistical transform of Tushare cyq_chips snapshot",
        explanation="用加权标准差衡量筹码成本围绕平均成本的分散程度。",
    ),
    "profit_ratio_delta_20d": FactorTrace(
        factor_id="profit_ratio_delta_20d",
        source_level="FACTOR_FAMILY",
        implementation_type="exact delta from daily Tushare-derived snapshots",
        explanation="比较当前和 20 个交易日前的获利筹码占比变化。",
    ),
    "loss_ratio_delta_20d": FactorTrace(
        factor_id="loss_ratio_delta_20d",
        source_level="FACTOR_FAMILY",
        implementation_type="exact delta from daily Tushare-derived snapshots",
        explanation="比较当前和 20 个交易日前的套牢筹码占比变化。",
    ),
    "weighted_chip_cost_delta_20d": FactorTrace(
        factor_id="weighted_chip_cost_delta_20d",
        source_level="FACTOR_FAMILY",
        implementation_type="exact delta from Tushare cyq_chips snapshots",
        explanation="比较当前和 20 个交易日前的加权筹码成本变化。",
    ),
    "concentration_width_70_delta_20d": FactorTrace(
        factor_id="concentration_width_70_delta_20d",
        source_level="OPEN_REPRODUCTION / FACTOR_FAMILY",
        implementation_type="project implementation of public chip-concentration factor family",
        explanation="比较当前和 20 个交易日前的 70% 筹码集中宽度变化。",
    ),
    "dominant_peak_price_delta_20d": FactorTrace(
        factor_id="dominant_peak_price_delta_20d",
        source_level="FACTOR_FAMILY",
        implementation_type="exact delta from Tushare cyq_chips snapshots",
        explanation="比较当前和 20 个交易日前的主筹码峰价格变化。",
    ),
}

FORMULA_PRUNED_FACTOR_REPLACEMENTS: dict[str, dict[str, str]] = {
    "profit_ratio_asof": {
        "replacement_factor": "loss_ratio_asof",
        "reason": "Formula mirror of loss_ratio_asof; keeping trapped-chip pressure as the primary interpretation.",
    },
    "profit_ratio_delta_20d": {
        "replacement_factor": "loss_ratio_delta_20d",
        "reason": "Formula mirror of loss_ratio_delta_20d; keeping trapped-chip change as the primary interpretation.",
    },
    "cyq_cgo_asof": {
        "replacement_factor": "weighted_chip_cost_gap_asof",
        "reason": "Proxy for the same price-vs-weighted-chip-cost relationship; keeping the exact project factor-family implementation.",
    },
}

PRUNED_FACTOR_IDS = frozenset(FORMULA_PRUNED_FACTOR_REPLACEMENTS)
ACTIVE_FACTOR_IDS = tuple(factor_id for factor_id in FACTOR_TRACES if factor_id not in PRUNED_FACTOR_IDS)


def build_daily_chip_snapshot(
    ts_code: str,
    factor_date: str,
    chip_points: list[ChipDistributionPoint],
    price_bar: DailyPriceBar | None,
) -> DailyChipSnapshot:
    same_day_points = [point for point in chip_points if point.trade_date == factor_date]
    close = price_bar.close if price_bar is not None else None
    weighted_chip_cost = _weighted_chip_cost(same_day_points)
    dominant_peak = _dominant_peak(same_day_points, close)
    profit_ratio = _profit_ratio(same_day_points, close)
    loss_ratio = _loss_ratio(same_day_points, close)
    at_close_ratio = _at_close_ratio(same_day_points, close)
    concentration_width_70 = _concentration_width(same_day_points, close, 70)
    concentration_width_90 = _concentration_width(same_day_points, close, 90)
    chip_weighted_std = _chip_weighted_std(same_day_points, close, weighted_chip_cost)
    cyq_cgo = _cyq_cgo(weighted_chip_cost, close)
    max_input_date = _max_input_date(same_day_points, price_bar)

    quality_status: FactorQualityStatus = "OK"
    missing_reason = None
    if not same_day_points:
        quality_status = "MISSING_CHIP_DATA"
        missing_reason = "No chip rows are available for the factor date."
    elif price_bar is None:
        quality_status = "MISSING_PRICE_DATA"
        missing_reason = "No daily price bar is available for the factor date."

    return DailyChipSnapshot(
        ts_code=ts_code,
        factor_date=factor_date,
        close=close,
        chip_rows=len(same_day_points),
        weighted_chip_cost=weighted_chip_cost,
        dominant_peak_price=dominant_peak.price if dominant_peak is not None else None,
        dominant_peak_percent=dominant_peak.percent if dominant_peak is not None else None,
        profit_ratio=profit_ratio,
        loss_ratio=loss_ratio,
        at_close_ratio=at_close_ratio,
        concentration_width_70=concentration_width_70,
        concentration_width_90=concentration_width_90,
        chip_weighted_std=chip_weighted_std,
        cyq_cgo=cyq_cgo,
        max_input_date=max_input_date,
        quality_status=quality_status,
        missing_reason=missing_reason,
    )


def build_factor_values(
    snapshots: list[DailyChipSnapshot],
    factor_date: str,
    lookback_days: int = 20,
    expected_trading_dates: list[str] | None = None,
) -> list[ChipFactorValue]:
    snapshots_by_date = {snapshot.factor_date: snapshot for snapshot in snapshots}
    ordered_dates = sorted(snapshots_by_date)
    current = snapshots_by_date[factor_date]
    values = [
        _as_factor("profit_ratio_asof", factor_date, current.profit_ratio, current.quality_status, None, current.max_input_date),
        _as_factor("loss_ratio_asof", factor_date, current.loss_ratio, current.quality_status, None, current.max_input_date),
        _as_factor("cyq_cgo_asof", factor_date, current.cyq_cgo, current.quality_status, None, current.max_input_date),
        _as_factor(
            "weighted_chip_cost_gap_asof",
            factor_date,
            _safe_ratio_delta(current.close, current.weighted_chip_cost),
            current.quality_status,
            None,
            current.max_input_date,
        ),
        _as_factor(
            "dominant_peak_strength_asof",
            factor_date,
            current.dominant_peak_percent,
            current.quality_status,
            None,
            current.max_input_date,
        ),
        _as_factor(
            "concentration_width_70_asof",
            factor_date,
            current.concentration_width_70,
            current.quality_status,
            None,
            current.max_input_date,
        ),
        _as_factor(
            "concentration_width_90_asof",
            factor_date,
            current.concentration_width_90,
            current.quality_status,
            None,
            current.max_input_date,
        ),
        _as_factor(
            "chip_weighted_std_asof",
            factor_date,
            current.chip_weighted_std,
            current.quality_status,
            None,
            current.max_input_date,
        ),
    ]

    anchor = _lookback_snapshot(snapshots_by_date, ordered_dates, factor_date, lookback_days, expected_trading_dates)
    values.extend(_delta_factors(current, anchor, factor_date, lookback_days))
    return [factor for factor in values if factor.factor_id in ACTIVE_FACTOR_IDS]


def factor_traceability_payload() -> list[dict[str, str]]:
    return [FACTOR_TRACES[factor_id].model_dump(mode="json") for factor_id in ACTIVE_FACTOR_IDS]


def factor_retention_policy_payload() -> dict[str, object]:
    return {
        "policy_id": "formula-pruned-v1",
        "active_factor_ids": list(ACTIVE_FACTOR_IDS),
        "excluded_factor_ids": sorted(PRUNED_FACTOR_IDS),
        "excluded_factors": [
            {
                "factor_id": factor_id,
                **FORMULA_PRUNED_FACTOR_REPLACEMENTS[factor_id],
            }
            for factor_id in sorted(PRUNED_FACTOR_IDS)
        ],
    }


def _as_factor(
    factor_id: str,
    factor_date: str,
    value: float | None,
    quality_status: FactorQualityStatus,
    lookback_days: int | None,
    max_input_date: str | None,
) -> ChipFactorValue:
    trace = FACTOR_TRACES[factor_id]
    return ChipFactorValue(
        factor_id=factor_id,
        factor_date=factor_date,
        value=value,
        quality_status=quality_status if value is not None else _missing_quality(quality_status),
        lookback_days=lookback_days,
        source_level=trace.source_level,
        implementation_type=trace.implementation_type,
        explanation=trace.explanation,
        max_input_date=max_input_date,
    )


def _delta_factors(
    current: DailyChipSnapshot,
    anchor: DailyChipSnapshot | None,
    factor_date: str,
    lookback_days: int,
) -> list[ChipFactorValue]:
    if anchor is None:
        return [
            _insufficient_factor(factor_id, factor_date, lookback_days, current.max_input_date)
            for factor_id in (
                "profit_ratio_delta_20d",
                "loss_ratio_delta_20d",
                "weighted_chip_cost_delta_20d",
                "concentration_width_70_delta_20d",
                "dominant_peak_price_delta_20d",
            )
        ]
    max_input_date = max(date for date in [current.max_input_date, anchor.max_input_date] if date is not None)
    return [
        _as_factor(
            "profit_ratio_delta_20d",
            factor_date,
            _safe_subtract(current.profit_ratio, anchor.profit_ratio),
            current.quality_status,
            lookback_days,
            max_input_date,
        ),
        _as_factor(
            "loss_ratio_delta_20d",
            factor_date,
            _safe_subtract(current.loss_ratio, anchor.loss_ratio),
            current.quality_status,
            lookback_days,
            max_input_date,
        ),
        _as_factor(
            "weighted_chip_cost_delta_20d",
            factor_date,
            _safe_ratio_delta(current.weighted_chip_cost, anchor.weighted_chip_cost),
            current.quality_status,
            lookback_days,
            max_input_date,
        ),
        _as_factor(
            "concentration_width_70_delta_20d",
            factor_date,
            _safe_subtract(current.concentration_width_70, anchor.concentration_width_70),
            current.quality_status,
            lookback_days,
            max_input_date,
        ),
        _as_factor(
            "dominant_peak_price_delta_20d",
            factor_date,
            _safe_ratio_delta(current.dominant_peak_price, anchor.dominant_peak_price),
            current.quality_status,
            lookback_days,
            max_input_date,
        ),
    ]


def _insufficient_factor(
    factor_id: str,
    factor_date: str,
    lookback_days: int,
    max_input_date: str | None,
) -> ChipFactorValue:
    trace = FACTOR_TRACES[factor_id]
    return ChipFactorValue(
        factor_id=factor_id,
        factor_date=factor_date,
        value=None,
        quality_status="INSUFFICIENT_HISTORY",
        lookback_days=lookback_days,
        source_level=trace.source_level,
        implementation_type=trace.implementation_type,
        explanation=trace.explanation,
        max_input_date=max_input_date,
    )


def _lookback_snapshot(
    snapshots_by_date: dict[str, DailyChipSnapshot],
    ordered_dates: list[str],
    factor_date: str,
    lookback_days: int,
    expected_trading_dates: list[str] | None,
) -> DailyChipSnapshot | None:
    if expected_trading_dates is not None:
        if factor_date not in expected_trading_dates:
            return None
        index = expected_trading_dates.index(factor_date)
        if index < lookback_days:
            return None
        required_dates = expected_trading_dates[index - lookback_days : index + 1]
        if any(trade_date not in snapshots_by_date for trade_date in required_dates):
            return None
        return snapshots_by_date[expected_trading_dates[index - lookback_days]]

    index = ordered_dates.index(factor_date)
    if index < lookback_days:
        return None
    return snapshots_by_date[ordered_dates[index - lookback_days]]


def _missing_quality(current_status: FactorQualityStatus) -> FactorQualityStatus:
    if current_status != "OK":
        return current_status
    return "PARTIAL"


def _weighted_chip_cost(chip_points: list[ChipDistributionPoint]) -> float | None:
    total_percent = sum(point.percent for point in chip_points)
    if total_percent <= 0:
        return None
    return sum(point.price * point.percent for point in chip_points) / total_percent


def _dominant_peak(chip_points: list[ChipDistributionPoint], close: float | None) -> ChipDistributionPoint | None:
    if not chip_points:
        return None
    if close is None:
        return max(chip_points, key=lambda point: (point.percent, -point.price))
    return max(chip_points, key=lambda point: (point.percent, -abs(point.price - close), -point.price))


def _profit_ratio(chip_points: list[ChipDistributionPoint], close: float | None) -> float | None:
    if close is None:
        return None
    return sum(point.percent for point in chip_points if point.price < close)


def _loss_ratio(chip_points: list[ChipDistributionPoint], close: float | None) -> float | None:
    if close is None:
        return None
    return sum(point.percent for point in chip_points if point.price > close)


def _at_close_ratio(chip_points: list[ChipDistributionPoint], close: float | None) -> float | None:
    if close is None:
        return None
    return sum(point.percent for point in chip_points if point.price == close)


def _concentration_width(chip_points: list[ChipDistributionPoint], close: float | None, target_percent: float) -> float | None:
    if close is None or close <= 0 or not chip_points:
        return None
    sorted_points = sorted(chip_points, key=lambda point: point.price)
    total_percent = sum(point.percent for point in sorted_points)
    if total_percent <= 0:
        return None
    required = total_percent * target_percent / 100
    best_width: float | None = None
    for start in range(len(sorted_points)):
        cumulative = 0.0
        for end in range(start, len(sorted_points)):
            cumulative += sorted_points[end].percent
            if cumulative >= required:
                width = sorted_points[end].price - sorted_points[start].price
                if best_width is None or width < best_width:
                    best_width = width
                break
    if best_width is None:
        return None
    return best_width / close


def _chip_weighted_std(
    chip_points: list[ChipDistributionPoint],
    close: float | None,
    weighted_chip_cost: float | None,
) -> float | None:
    if close is None or close <= 0 or weighted_chip_cost is None:
        return None
    total_percent = sum(point.percent for point in chip_points)
    if total_percent <= 0:
        return None
    variance = sum(point.percent * (point.price - weighted_chip_cost) ** 2 for point in chip_points) / total_percent
    return sqrt(variance) / close


def _cyq_cgo(weighted_chip_cost: float | None, close: float | None) -> float | None:
    if weighted_chip_cost is None or close is None or close == 0:
        return None
    return (close - weighted_chip_cost) / close


def _max_input_date(chip_points: list[ChipDistributionPoint], price_bar: DailyPriceBar | None) -> str | None:
    dates = [point.trade_date for point in chip_points]
    if price_bar is not None:
        dates.append(price_bar.trade_date)
    return max(dates) if dates else None


def _safe_subtract(current: float | None, anchor: float | None) -> float | None:
    if current is None or anchor is None:
        return None
    return current - anchor


def _safe_ratio_delta(current: float | None, anchor: float | None) -> float | None:
    if current is None or anchor is None or anchor == 0:
        return None
    return current / anchor - 1
