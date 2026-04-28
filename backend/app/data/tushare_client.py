import os
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import load_environment
from app.domain.errors import DataErrorCode, DataUnavailableError
from app.domain.models import ChipDistributionPoint, DailyPriceBar


class TushareMarketDataClient:
    def __init__(self, token: str | None = None) -> None:
        load_environment()
        self.token = token or os.getenv("TUSHARE_TOKEN")
        if not self.token:
            raise DataUnavailableError(DataErrorCode.MISSING_TOKEN, "TUSHARE_TOKEN is not configured.")
        self._pro: Any | None = None

    @property
    def pro(self) -> Any:
        if self._pro is None:
            try:
                import tushare as ts
            except ImportError as error:
                raise DataUnavailableError(DataErrorCode.NETWORK_ERROR, "The tushare package is not installed.") from error
            self._pro = ts.pro_api(self.token)
        return self._pro

    def resolve_trading_days(self, end_date: str | None, n_days: int) -> list[str]:
        resolved_end = end_date or datetime.now(UTC).strftime("%Y%m%d")
        start_probe = (
            datetime.strptime(resolved_end, "%Y%m%d").replace(tzinfo=UTC) - timedelta(days=max(30, n_days * 3))
        ).strftime("%Y%m%d")
        try:
            data = self.pro.trade_cal(exchange="SSE", start_date=start_probe, end_date=resolved_end, is_open="1")
        except Exception as error:
            raise _map_tushare_error(error) from error

        dates = sorted(str(item) for item in data["cal_date"].tolist())
        if len(dates) < n_days:
            raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "Not enough trading days returned by Tushare.")
        return dates[-n_days:]

    def get_stock_name(self, ts_code: str) -> str | None:
        try:
            data = self.pro.stock_basic(ts_code=ts_code, fields="ts_code,name")
        except Exception:
            return None
        if data.empty:
            return None
        return str(data.iloc[0]["name"])

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        frames = []
        for trade_date in self._trading_days_between(start_date, end_date):
            try:
                frame = self.pro.cyq_chips(ts_code=ts_code, trade_date=trade_date)
            except Exception as error:
                raise _map_tushare_error(error) from error
            if not frame.empty:
                frames.append(frame)
        if not frames:
            raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "No cyq_chips rows returned.")
        data = _concat_frames(frames)
        return [
            ChipDistributionPoint(
                ts_code=str(row["ts_code"]),
                trade_date=str(row["trade_date"]),
                price=float(row["price"]),
                percent=float(row["percent"]),
            )
            for _, row in data.iterrows()
        ]

    def _trading_days_between(self, start_date: str, end_date: str) -> list[str]:
        try:
            data = self.pro.trade_cal(exchange="SSE", start_date=start_date, end_date=end_date, is_open="1")
        except Exception as error:
            raise _map_tushare_error(error) from error
        dates = sorted(str(item) for item in data["cal_date"].tolist())
        if not dates:
            raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "No trading days found for cyq_chips range.")
        return dates

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        try:
            data = self.pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        except Exception as error:
            raise _map_tushare_error(error) from error
        if data.empty:
            raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "No daily price rows returned.")
        return [
            DailyPriceBar(
                ts_code=str(row["ts_code"]),
                trade_date=str(row["trade_date"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                pre_close=_optional_float(row.get("pre_close")),
                pct_chg=_optional_float(row.get("pct_chg")),
                vol=_optional_float(row.get("vol")),
                amount=_optional_float(row.get("amount")),
            )
            for _, row in data.iterrows()
        ]


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _concat_frames(frames: list[Any]) -> Any:
    import pandas as pd

    return pd.concat(frames, ignore_index=True)


def _map_tushare_error(error: Exception) -> DataUnavailableError:
    message = str(error)
    lowered = message.lower()
    if "permission" in lowered or "权限" in message or "积分" in message:
        return DataUnavailableError(DataErrorCode.NO_PERMISSION, "Tushare endpoint permission is unavailable.")
    if "rate" in lowered or "limit" in lowered or "频次" in message:
        return DataUnavailableError(DataErrorCode.RATE_LIMITED, "Tushare rate limit reached.")
    return DataUnavailableError(DataErrorCode.NETWORK_ERROR, "Tushare request failed.")
