from fastapi.testclient import TestClient

from app.api import routes
from app.domain.models import InDevReviewResponse
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


def test_research_run_validates_start_date_format() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/research-runs",
        json={
            "stock_code": "000001",
            "start_dates": ["2026-01-01"],
            "window_days": 10,
        },
    )

    assert response.status_code == 422


def test_in_dev_review_rejects_path_traversal_run_id() -> None:
    client = TestClient(create_app())

    response = client.post("/api/in-dev-reviews", json={"run_id": "../secret"})

    assert response.status_code == 422


def test_in_dev_review_missing_review_returns_404() -> None:
    client = TestClient(create_app())

    response = client.get("/api/in-dev-reviews/review-does-not-exist")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ARTIFACT_NOT_FOUND"


def test_research_run_auto_review_helper_returns_review_payload(monkeypatch) -> None:
    class FakeInDevReviewService:
        def create_review(self, request):
            return InDevReviewResponse(
                review_id="review-1",
                run_id=request.run_id,
                status="awaiting_approval",
                artifact_dir="docs/in-dev-reviews/review-1",
                findings_count=1,
                approval_required=True,
                artifact_refs={"report": "report.md"},
            )

    monkeypatch.setattr(routes, "InDevReviewService", FakeInDevReviewService)

    payload = routes._create_in_dev_review_for_run("run-1")

    assert payload["review_id"] == "review-1"
    assert payload["status"] == "awaiting_approval"


def test_research_run_auto_review_helper_does_not_fail_research_response(monkeypatch) -> None:
    class FailingInDevReviewService:
        def create_review(self, request):
            raise RuntimeError("review failed")

    monkeypatch.setattr(routes, "InDevReviewService", FailingInDevReviewService)

    payload = routes._create_in_dev_review_for_run("run-1")

    assert payload == {"status": "failed", "error": "review failed"}
