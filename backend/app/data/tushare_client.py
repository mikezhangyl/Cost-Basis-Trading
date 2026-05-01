import os
import re
from datetime import UTC, datetime, timedelta
from time import sleep
from typing import Any, Callable

from app.core.config import load_environment
from app.domain.errors import DataErrorCode, DataUnavailableError
from app.domain.models import ChipDistributionPoint, DailyPriceBar


class TushareMarketDataClient:
    def __init__(self, token: str | None = None, max_retries: int = 3, retry_sleep_seconds: float = 0.5) -> None:
        load_environment()
        self.token = token or os.getenv("TUSHARE_TOKEN")
        if not self.token:
            raise DataUnavailableError(DataErrorCode.MISSING_TOKEN, "TUSHARE_TOKEN is not configured.")
        self._pro: Any | None = None
        self.max_retries = max(1, max_retries)
        self.retry_sleep_seconds = max(0, retry_sleep_seconds)
        self.retry_event_handler: Callable[[dict[str, Any]], None] | None = None

    def set_retry_event_handler(self, handler: Callable[[dict[str, Any]], None] | None) -> None:
        self.retry_event_handler = handler

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
        data = self._call_tushare(
            "trade_cal",
            lambda: self.pro.trade_cal(exchange="SSE", start_date=start_probe, end_date=resolved_end, is_open="1"),
            {"exchange": "SSE", "start_date": start_probe, "end_date": resolved_end, "is_open": "1"},
        )

        dates = sorted(str(item) for item in data["cal_date"].tolist())
        if len(dates) < n_days:
            raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "Not enough trading days returned by Tushare.")
        return dates[-n_days:]

    def resolve_trading_days_from(self, start_date: str, n_days: int) -> list[str]:
        end_probe = (
            datetime.strptime(start_date, "%Y%m%d").replace(tzinfo=UTC) + timedelta(days=max(30, n_days * 3))
        ).strftime("%Y%m%d")
        data = self._call_tushare(
            "trade_cal",
            lambda: self.pro.trade_cal(exchange="SSE", start_date=start_date, end_date=end_probe, is_open="1"),
            {"exchange": "SSE", "start_date": start_date, "end_date": end_probe, "is_open": "1"},
        )
        dates = sorted(str(item) for item in data["cal_date"].tolist())
        if len(dates) < n_days:
            raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "Not enough forward trading days returned by Tushare.")
        return dates[:n_days]

    def get_stock_name(self, ts_code: str) -> str | None:
        try:
            data = self._call_tushare(
                "stock_basic",
                lambda: self.pro.stock_basic(ts_code=ts_code, fields="ts_code,name"),
                {"ts_code": ts_code, "fields": "ts_code,name"},
            )
        except Exception:
            return None
        if data.empty:
            return None
        return str(data.iloc[0]["name"])

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        frames = []
        for trade_date in self._trading_days_between(start_date, end_date):
            frame = self._call_tushare(
                "cyq_chips",
                lambda trade_date=trade_date: self.pro.cyq_chips(ts_code=ts_code, trade_date=trade_date),
                {"ts_code": ts_code, "trade_date": trade_date},
            )
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
        data = self._call_tushare(
            "trade_cal",
            lambda: self.pro.trade_cal(exchange="SSE", start_date=start_date, end_date=end_date, is_open="1"),
            {"exchange": "SSE", "start_date": start_date, "end_date": end_date, "is_open": "1"},
        )
        dates = sorted(str(item) for item in data["cal_date"].tolist())
        if not dates:
            raise DataUnavailableError(DataErrorCode.EMPTY_DATA, "No trading days found for cyq_chips range.")
        return dates

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        data = self._call_tushare(
            "daily",
            lambda: self.pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date),
            {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        )
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

    def _call_tushare(self, endpoint: str, call: Any, params: dict[str, Any]) -> Any:
        last_error: DataUnavailableError | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                result = call()
                if attempt > 1:
                    self._emit_retry_event(
                        {
                            "endpoint": endpoint,
                            "params": params,
                            "attempt": attempt,
                            "max_retries": self.max_retries,
                            "error_code": None,
                            "error_message": None,
                            "raw_error_message": None,
                            "retryable": False,
                            "sleep_seconds": 0,
                            "status": "succeeded_after_retry",
                        }
                    )
                return result
            except Exception as error:
                mapped_error = _map_tushare_error(error, endpoint=endpoint, attempt=attempt, max_retries=self.max_retries)
                retryable = _is_retryable_tushare_error(mapped_error)
                sleep_seconds = self.retry_sleep_seconds * (2 ** (attempt - 1))
                self._emit_retry_event(
                    {
                        "endpoint": endpoint,
                        "params": params,
                        "attempt": attempt,
                        "max_retries": self.max_retries,
                        "error_code": mapped_error.code.value,
                        "error_message": mapped_error.message,
                        "raw_error_message": _sanitize_error_message(str(error)),
                        "retryable": retryable and attempt < self.max_retries,
                        "sleep_seconds": sleep_seconds if retryable and attempt < self.max_retries else 0,
                        "status": "retrying" if retryable and attempt < self.max_retries else "failed",
                    }
                )
                if not _is_retryable_tushare_error(mapped_error) or attempt >= self.max_retries:
                    raise mapped_error from error
                last_error = mapped_error
                sleep(sleep_seconds)
        if last_error is not None:
            raise last_error
        raise DataUnavailableError(DataErrorCode.NETWORK_ERROR, f"Tushare {endpoint} request failed.")

    def _emit_retry_event(self, event: dict[str, Any]) -> None:
        if self.retry_event_handler is not None:
            self.retry_event_handler(event)


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


def _map_tushare_error(error: Exception, endpoint: str | None = None, attempt: int | None = None, max_retries: int | None = None) -> DataUnavailableError:
    message = str(error)
    lowered = message.lower()
    suffix = _retry_suffix(endpoint, attempt, max_retries)
    if "rate" in lowered or "limit" in lowered or "频次" in message:
        return DataUnavailableError(DataErrorCode.RATE_LIMITED, f"Tushare rate limit reached.{suffix}")
    if "permission" in lowered or "权限" in message or "积分不足" in message:
        return DataUnavailableError(DataErrorCode.NO_PERMISSION, f"Tushare endpoint permission is unavailable.{suffix}")
    return DataUnavailableError(DataErrorCode.NETWORK_ERROR, f"Tushare request failed.{suffix}")


def _is_retryable_tushare_error(error: DataUnavailableError) -> bool:
    return error.code in {DataErrorCode.NETWORK_ERROR, DataErrorCode.RATE_LIMITED}


def _retry_suffix(endpoint: str | None, attempt: int | None, max_retries: int | None) -> str:
    if endpoint is None or attempt is None or max_retries is None:
        return ""
    return f" endpoint={endpoint} attempt={attempt}/{max_retries}"


def _sanitize_error_message(message: str, max_length: int = 500) -> str:
    cleaned = message.replace("\n", " ").replace("\r", " ")
    cleaned = re.sub(
        r"(?i)([\"']?\b(?:token|api[_-]?key|secret|password)\b[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)",
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(4)}",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)([\"']?\b(?:token|api[_-]?key|secret|password)\b[\"']?\s*[:=]\s*)(?![\"'])[^\s,;}]+",
        lambda match: f"{match.group(1)}[REDACTED]",
        cleaned,
    )
    return cleaned[:max_length]
