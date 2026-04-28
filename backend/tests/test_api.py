from fastapi.testclient import TestClient

from app.main import create_app


def test_health_does_not_expose_token_value(monkeypatch) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-token-value")
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["tushare_token_configured"] is True
    assert "secret-token-value" not in response.text


def test_scan_validates_empty_stock_list() -> None:
    client = TestClient(create_app())

    response = client.post("/api/scans", json={"stock_codes": [], "n_days": 10})

    assert response.status_code == 422


def test_backtest_validates_date_format() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/backtests",
        json={
            "stock_code": "600519",
            "start_date": "2026-01-01",
            "window_days": 10,
        },
    )

    assert response.status_code == 422
