from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.errors import DataErrorCode

SignalAction = Literal["BUY", "HOLD", "SELL"]


class ChipDistributionPoint(BaseModel):
    ts_code: str
    trade_date: str
    price: float
    percent: float


class DailyPriceBar(BaseModel):
    ts_code: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    pre_close: float | None = None
    pct_chg: float | None = None
    vol: float | None = None
    amount: float | None = None


class AdjustmentFactor(BaseModel):
    ts_code: str
    trade_date: str
    adj_factor: float


class ScanRequest(BaseModel):
    stock_codes: list[str] = Field(min_length=1, max_length=100)
    n_days: int = Field(default=10, ge=1, le=120)
    end_date: str | None = None

    @field_validator("stock_codes")
    @classmethod
    def validate_stock_codes(cls, value: list[str]) -> list[str]:
        cleaned = [code.strip().upper() for code in value if code.strip()]
        if not cleaned:
            raise ValueError("At least one stock code is required.")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Duplicate stock codes are not allowed.")
        return cleaned

    @field_validator("end_date")
    @classmethod
    def validate_end_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if len(value) != 8 or not value.isdigit():
            raise ValueError("end_date must use YYYYMMDD format.")
        return value


class BacktestRequest(BaseModel):
    stock_code: str = Field(min_length=1, max_length=16)
    start_date: str
    window_days: int = Field(default=10, ge=2, le=120)

    @field_validator("stock_code")
    @classmethod
    def validate_stock_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("start_date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        if len(value) != 8 or not value.isdigit():
            raise ValueError("Date must use YYYYMMDD format.")
        return value


class ResearchRunRequest(BaseModel):
    stock_code: str = Field(min_length=1, max_length=16)
    start_dates: list[str] = Field(min_length=1, max_length=60)
    window_days: int = Field(default=10, ge=2, le=120)
    candidate_strategy_ids: list[str] = Field(
        default_factory=lambda: ["composite_baseline", "market_context_followthrough"],
        min_length=1,
        max_length=10,
    )

    @field_validator("stock_code")
    @classmethod
    def validate_stock_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("start_dates")
    @classmethod
    def validate_start_dates(cls, value: list[str]) -> list[str]:
        cleaned = [date.strip() for date in value if date.strip()]
        if not cleaned:
            raise ValueError("At least one start date is required.")
        for date in cleaned:
            if len(date) != 8 or not date.isdigit():
                raise ValueError("Each start date must use YYYYMMDD format.")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Duplicate start dates are not allowed.")
        return cleaned

    @field_validator("candidate_strategy_ids")
    @classmethod
    def validate_candidate_strategy_ids(cls, value: list[str]) -> list[str]:
        cleaned = [strategy_id.strip() for strategy_id in value if strategy_id.strip()]
        if not cleaned:
            raise ValueError("At least one candidate strategy is required.")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Duplicate candidate strategy ids are not allowed.")
        return cleaned


class StrategySignal(BaseModel):
    strategy_name: str
    action: SignalAction
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]
    features: dict[str, float | str | None]


class DataQuality(BaseModel):
    status: Literal["OK", "WARNING", "ERROR"]
    message: str | None = None
    error_code: DataErrorCode | None = None


class StockScanResult(BaseModel):
    ts_code: str
    stock_name: str | None
    date_range: dict[str, str | None]
    signal: StrategySignal
    strategy_signals: list[StrategySignal] = Field(default_factory=list)
    data_quality: DataQuality
    row_counts: dict[str, int]


class ScanResponse(BaseModel):
    scan_id: str
    requested_at: datetime
    n_days: int
    results: list[StockScanResult]

    @classmethod
    def create(cls, scan_id: str, n_days: int, results: list[StockScanResult]) -> "ScanResponse":
        return cls(scan_id=scan_id, requested_at=datetime.now(UTC), n_days=n_days, results=results)


class BacktestObservationPoint(BaseModel):
    offset_days: int
    observation_date: str | None
    signal_close: float
    observation_close: float | None
    period_return: float | None
    match_label: Literal["MATCH", "MISMATCH", "NEUTRAL", "N/A"]
    interpretation: str


class MarketContextFeatureSet(BaseModel):
    price_return: float | None
    volume_ratio_5: float | None
    amount_ratio_5: float | None
    volume_trend: float | None
    close_vs_ma5: float | None
    close_vs_ma10: float | None
    doji_count: int
    bullish_candle_count: int
    bearish_candle_count: int
    long_upper_shadow_count: int
    long_lower_shadow_count: int
    context_summary: str


class BacktestResponse(BaseModel):
    backtest_id: str
    requested_at: datetime
    ts_code: str
    stock_name: str | None
    analysis_range: dict[str, str]
    window_days: int
    signal_date: str
    signal: StrategySignal
    market_context: MarketContextFeatureSet
    observations: list[BacktestObservationPoint]
    row_counts: dict[str, int]

    @classmethod
    def create(
        cls,
        backtest_id: str,
        ts_code: str,
        stock_name: str | None,
        analysis_range: dict[str, str],
        window_days: int,
        signal_date: str,
        signal: StrategySignal,
        market_context: MarketContextFeatureSet,
        observations: list[BacktestObservationPoint],
        row_counts: dict[str, int],
    ) -> "BacktestResponse":
        return cls(
            backtest_id=backtest_id,
            requested_at=datetime.now(UTC),
            ts_code=ts_code,
            stock_name=stock_name,
            analysis_range=analysis_range,
            window_days=window_days,
            signal_date=signal_date,
            signal=signal,
            market_context=market_context,
            observations=observations,
            row_counts=row_counts,
        )


class ResearchObservationScore(BaseModel):
    offset_days: int
    period_return: float | None
    match_label: Literal["MATCH", "MISMATCH", "NEUTRAL", "N/A"]
    directional_score: float | None


class ResearchStrategyScore(BaseModel):
    strategy_id: str
    signal: StrategySignal
    observation_scores: list[ResearchObservationScore]
    average_directional_score: float
    match_count: int
    mismatch_count: int
    neutral_count: int
    unavailable_count: int


class ResearchAggregateScore(BaseModel):
    strategy_id: str
    sample_count: int
    average_directional_score: float
    match_count: int
    mismatch_count: int
    neutral_count: int
    unavailable_count: int


class ResearchSampleResult(BaseModel):
    sample_id: str
    start_date: str
    signal_date: str
    status: Literal["completed", "invalid", "failed"]
    artifact_dir: str
    strategies: list[ResearchStrategyScore]


class ResearchReportValidation(BaseModel):
    status: Literal["passed", "corrected"]
    canonical_observation_labels: list[str]
    missing_observation_labels: list[str]


class ResearchAiReviewSummary(BaseModel):
    status: Literal["completed", "skipped", "failed"]
    model: str | None = None
    summary: str
    report_validation: ResearchReportValidation | None = None
    artifact_refs: dict[str, str]


class CacheEventSummary(BaseModel):
    cache_event_count: int
    endpoint_count: int
    endpoints: list[str]
    request_count: int
    hit_count: int
    miss_count: int
    hit_rate_percent: float
    miss_rate_percent: float
    stale_count: int
    stale_rate_percent: float
    fetched_date_count: int
    suppressed_no_data_count: int


class ResearchRunResponse(BaseModel):
    run_id: str
    requested_at: datetime
    ts_code: str
    stock_name: str | None
    window_days: int
    observation_offsets: list[int]
    sample_count: int
    artifact_dir: str
    cache_event_summary: CacheEventSummary
    ai_review: ResearchAiReviewSummary
    aggregate_scores: list[ResearchAggregateScore]
    samples: list[ResearchSampleResult]

    @classmethod
    def create(
        cls,
        run_id: str,
        ts_code: str,
        stock_name: str | None,
        window_days: int,
        observation_offsets: list[int],
        artifact_dir: str,
        cache_event_summary: CacheEventSummary,
        ai_review: ResearchAiReviewSummary,
        aggregate_scores: list[ResearchAggregateScore],
        samples: list[ResearchSampleResult],
    ) -> "ResearchRunResponse":
        return cls(
            run_id=run_id,
            requested_at=datetime.now(UTC),
            ts_code=ts_code,
            stock_name=stock_name,
            window_days=window_days,
            observation_offsets=observation_offsets,
            sample_count=len(samples),
            artifact_dir=artifact_dir,
            cache_event_summary=cache_event_summary,
            ai_review=ai_review,
            aggregate_scores=aggregate_scores,
            samples=samples,
        )


class ApiEnvelope(BaseModel):
    success: bool
    data: object | None = None
    error: str | None = None
