import pandas as pd

from app.data.tushare_client import TushareMarketDataClient


class FakePro:
    def __init__(self) -> None:
        self.cyq_calls: list[dict[str, str]] = []

    def trade_cal(self, exchange: str, start_date: str, end_date: str, is_open: str):
        return pd.DataFrame(
            {
                "cal_date": ["20260415", "20260416", "20260417"],
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


class FakeTushareClient(TushareMarketDataClient):
    def __init__(self, fake_pro: FakePro) -> None:
        self.token = "test-token"
        self._pro = fake_pro


def test_chip_distribution_queries_each_trading_day_to_avoid_row_limit() -> None:
    fake_pro = FakePro()
    client = FakeTushareClient(fake_pro)

    rows = client.get_chip_distribution("600519.SH", "20260415", "20260417")

    assert len(rows) == 3
    assert [call["trade_date"] for call in fake_pro.cyq_calls] == ["20260415", "20260416", "20260417"]
    assert all("start_date" not in call for call in fake_pro.cyq_calls)
    assert all("end_date" not in call for call in fake_pro.cyq_calls)
