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


class ApiEnvelope(BaseModel):
    success: bool
    data: object | None = None
    error: str | None = None
