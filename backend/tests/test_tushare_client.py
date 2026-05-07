import pandas as pd

from app.data.tushare_client import TushareMarketDataClient
from app.domain.errors import DataErrorCode, DataUnavailableError


class FakePro:
    def __init__(self) -> None:
        self.cyq_calls: list[dict[str, str]] = []

    def trade_cal(self, exchange: str, start_date: str, end_date: str, is_open: str | None = None):
        return pd.DataFrame(
            {
                "cal_date": ["20260415", "20260416", "20260417"],
                "is_open": [1, 1, 1],
            }
        )

    def cyq_chips(self, **kwargs):
        self.cyq_calls.append(kwargs)
        return pd.DataFrame(
            {
                "ts_code": [kwargs["ts_code"]],
                "trade_date": [kwargs["trade_date"]],
                "price": [10.0],
                "percent": [1.0],
            }
        )

    def adj_factor(self, ts_code: str, start_date: str, end_date: str):
        return pd.DataFrame(
            {
                "ts_code": [ts_code, ts_code],
                "trade_date": [start_date, end_date],
                "adj_factor": [1.0, 2.0],
            }
        )


class FakeTushareClient(TushareMarketDataClient):
    def __init__(self, fake_pro: FakePro) -> None:
        self.token = "test-token"
        self._pro = fake_pro
        self.max_retries = 3
        self.retry_sleep_seconds = 0
        self.retry_event_handler = None


def test_chip_distribution_queries_each_trading_day_to_avoid_row_limit() -> None:
    fake_pro = FakePro()
    client = FakeTushareClient(fake_pro)

    rows = client.get_chip_distribution("600519.SH", "20260415", "20260417")

    assert len(rows) == 3
    assert [call["trade_date"] for call in fake_pro.cyq_calls] == ["20260415", "20260416", "20260417"]
    assert all("start_date" not in call for call in fake_pro.cyq_calls)
    assert all("end_date" not in call for call in fake_pro.cyq_calls)


def test_get_adjustment_factors_normalizes_tushare_rows() -> None:
    fake_pro = FakePro()
    client = FakeTushareClient(fake_pro)

    rows = client.get_adjustment_factors("600519.SH", "20260415", "20260417")

    assert [row.trade_date for row in rows] == ["20260415", "20260417"]
    assert [row.adj_factor for row in rows] == [1.0, 2.0]


def test_get_trade_calendar_normalizes_tushare_rows() -> None:
    class CalendarPro(FakePro):
        def trade_cal(self, exchange: str, start_date: str, end_date: str, is_open: str | None = None):
            assert is_open is None
            return pd.DataFrame(
                {
                    "cal_date": ["20260415", "20260416"],
                    "is_open": [1, 0],
                }
            )

    client = FakeTushareClient(CalendarPro())

    rows = client.get_trade_calendar("20260415", "20260416")

    assert rows == [
        {"cal_date": "20260415", "is_open": True},
        {"cal_date": "20260416", "is_open": False},
    ]


class TransientCyqPro(FakePro):
    def __init__(self) -> None:
        super().__init__()
        self.failures_remaining = 1

    def cyq_chips(self, **kwargs):
        self.cyq_calls.append(kwargs)
        if kwargs["trade_date"] == "20260416" and self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("temporary gateway timeout")
        return pd.DataFrame(
            {
                "ts_code": [kwargs["ts_code"]],
                "trade_date": [kwargs["trade_date"]],
                "price": [10.0],
                "percent": [1.0],
            }
        )


class PermissionCyqPro(FakePro):
    def cyq_chips(self, **kwargs):
        self.cyq_calls.append(kwargs)
        raise RuntimeError("权限不足")


class ChineseRateLimitCyqPro(FakePro):
    def __init__(self) -> None:
        super().__init__()
        self.failures_remaining = 1

    def cyq_chips(self, **kwargs):
        self.cyq_calls.append(kwargs)
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("接口调用频次超过限制，积分越多频次越高")
        return pd.DataFrame(
            {
                "ts_code": [kwargs["ts_code"]],
                "trade_date": [kwargs["trade_date"]],
                "price": [10.0],
                "percent": [1.0],
            }
        )


class ExhaustedNetworkCyqPro(FakePro):
    def cyq_chips(self, **kwargs):
        self.cyq_calls.append(kwargs)
        raise RuntimeError("temporary gateway timeout")


class SecretBearingCyqPro(FakePro):
    def cyq_chips(self, **kwargs):
        self.cyq_calls.append(kwargs)
        raise RuntimeError(
            'temporary failure {"token":"json-token"} token=abc123 '
            "'api_key': 'quoted-key' secret: bare-secret password=\"hidden\""
        )


def test_chip_distribution_retries_transient_tushare_failures() -> None:
    fake_pro = TransientCyqPro()
    client = FakeTushareClient(fake_pro)

    rows = client.get_chip_distribution("600519.SH", "20260415", "20260417")

    assert len(rows) == 3
    assert [call["trade_date"] for call in fake_pro.cyq_calls] == [
        "20260415",
        "20260416",
        "20260416",
        "20260417",
    ]


def test_chip_distribution_does_not_retry_permission_errors() -> None:
    fake_pro = PermissionCyqPro()
    client = FakeTushareClient(fake_pro)

    try:
        client.get_chip_distribution("600519.SH", "20260415", "20260417")
    except DataUnavailableError as error:
        assert error.code == DataErrorCode.NO_PERMISSION
    else:
        raise AssertionError("Expected permission errors to fail without retry.")
    assert len(fake_pro.cyq_calls) == 1


def test_chip_distribution_retries_chinese_rate_limit_messages_before_permission_matching() -> None:
    fake_pro = ChineseRateLimitCyqPro()
    client = FakeTushareClient(fake_pro)

    rows = client.get_chip_distribution("600519.SH", "20260415", "20260415")

    assert len(rows) == 3
    assert len(fake_pro.cyq_calls) == 4


def test_chip_distribution_emits_retry_events_with_raw_error_summary() -> None:
    fake_pro = ChineseRateLimitCyqPro()
    client = FakeTushareClient(fake_pro)
    events: list[dict] = []
    client.set_retry_event_handler(events.append)

    client.get_chip_distribution("600519.SH", "20260415", "20260415")

    assert events[0]["endpoint"] == "cyq_chips"
    assert events[0]["params"] == {"ts_code": "600519.SH", "trade_date": "20260415"}
    assert events[0]["attempt"] == 1
    assert events[0]["max_retries"] == 3
    assert events[0]["error_code"] == "RATE_LIMITED"
    assert events[0]["retryable"] is True
    assert events[0]["status"] == "retrying"
    assert "频次" in events[0]["raw_error_message"]
    assert events[-1]["status"] == "succeeded_after_retry"
    assert events[-1]["attempt"] == 2
    assert events[-1]["error_code"] is None


def test_chip_distribution_redacts_secret_like_values_from_retry_events() -> None:
    fake_pro = SecretBearingCyqPro()
    client = FakeTushareClient(fake_pro)
    events: list[dict] = []
    client.set_retry_event_handler(events.append)

    try:
        client.get_chip_distribution("600519.SH", "20260415", "20260415")
    except DataUnavailableError:
        pass
    else:
        raise AssertionError("Expected retry exhaustion to raise.")

    assert "abc123" not in events[0]["raw_error_message"]
    assert "json-token" not in events[0]["raw_error_message"]
    assert "quoted-key" not in events[0]["raw_error_message"]
    assert "bare-secret" not in events[0]["raw_error_message"]
    assert "hidden" not in events[0]["raw_error_message"]
    assert "[REDACTED]" in events[0]["raw_error_message"]


def test_chip_distribution_redacts_env_and_authorization_secret_formats() -> None:
    class EnvSecretCyqPro(FakePro):
        def cyq_chips(self, **kwargs):
            self.cyq_calls.append(kwargs)
            raise RuntimeError(
                "TUSHARE_TOKEN=ts-secret OPENAI_API_KEY=openai-secret "
                "Authorization: Bearer bearer-secret"
            )

    fake_pro = EnvSecretCyqPro()
    client = FakeTushareClient(fake_pro)
    events: list[dict] = []
    client.set_retry_event_handler(events.append)

    try:
        client.get_chip_distribution("600519.SH", "20260415", "20260415")
    except DataUnavailableError:
        pass
    else:
        raise AssertionError("Expected retry exhaustion to raise.")

    raw_error = events[0]["raw_error_message"]
    assert "ts-secret" not in raw_error
    assert "openai-secret" not in raw_error
    assert "bearer-secret" not in raw_error
    assert "[REDACTED]" in raw_error


def test_chip_distribution_reports_final_attempt_when_retries_are_exhausted() -> None:
    fake_pro = ExhaustedNetworkCyqPro()
    client = FakeTushareClient(fake_pro)

    try:
        client.get_chip_distribution("600519.SH", "20260415", "20260415")
    except DataUnavailableError as error:
        assert error.code == DataErrorCode.NETWORK_ERROR
        assert "attempt=3/3" in error.message
    else:
        raise AssertionError("Expected exhausted transient failures to raise.")
    assert len(fake_pro.cyq_calls) == 3
