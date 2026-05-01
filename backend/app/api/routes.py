import os

from fastapi import APIRouter, HTTPException

from app.agent_workflows.in_dev_review_graph import InDevReviewService
from app.data.tushare_client import TushareMarketDataClient
from app.domain.errors import DataUnavailableError
from app.domain.models import (
    ApiEnvelope,
    BacktestRequest,
    InDevReviewApprovalRequest,
    InDevReviewRequest,
    ResearchRunRequest,
    ScanRequest,
)
from app.services.backtest_service import BacktestService
from app.services.research_agent_client import DeepSeekResearchAgentClient
from app.services.research_run_service import ResearchRunService
from app.services.scan_service import ScanService

router = APIRouter(prefix="/api")


@router.get("/health", response_model=ApiEnvelope)
def health() -> ApiEnvelope:
    return ApiEnvelope(
        success=True,
        data={
            "status": "ok",
            "tushare_token_configured": bool(os.getenv("TUSHARE_TOKEN")),
        },
        error=None,
    )


@router.post("/scans", response_model=ApiEnvelope)
def create_scan(request: ScanRequest) -> ApiEnvelope:
    try:
        service = ScanService(TushareMarketDataClient())
        return ApiEnvelope(success=True, data=service.scan(request).model_dump(mode="json"), error=None)
    except DataUnavailableError as error:
        raise HTTPException(status_code=503, detail={"code": error.code, "message": error.message}) from error


@router.post("/backtests", response_model=ApiEnvelope)
def create_backtest(request: BacktestRequest) -> ApiEnvelope:
    try:
        service = BacktestService(TushareMarketDataClient())
        return ApiEnvelope(success=True, data=service.run(request).model_dump(mode="json"), error=None)
    except DataUnavailableError as error:
        raise HTTPException(status_code=503, detail={"code": error.code, "message": error.message}) from error


@router.post("/research-runs", response_model=ApiEnvelope)
def create_research_run(request: ResearchRunRequest) -> ApiEnvelope:
    try:
        service = ResearchRunService(
            TushareMarketDataClient(),
            research_agent_client=DeepSeekResearchAgentClient.from_environment(),
        )
        research_run = service.run(request)
        payload = research_run.model_dump(mode="json")
        payload["in_dev_review"] = _create_in_dev_review_for_run(research_run.run_id)
        return ApiEnvelope(success=True, data=payload, error=None)
    except DataUnavailableError as error:
        raise HTTPException(status_code=503, detail={"code": error.code, "message": error.message}) from error


@router.post("/in-dev-reviews", response_model=ApiEnvelope)
def create_in_dev_review(request: InDevReviewRequest) -> ApiEnvelope:
    try:
        service = InDevReviewService()
        return ApiEnvelope(success=True, data=service.create_review(request).model_dump(mode="json"), error=None)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "ARTIFACT_NOT_FOUND", "message": str(error)}) from error


@router.get("/in-dev-reviews/{review_id}", response_model=ApiEnvelope)
def get_in_dev_review(review_id: str) -> ApiEnvelope:
    try:
        service = InDevReviewService()
        return ApiEnvelope(success=True, data=service.get_review(review_id).model_dump(mode="json"), error=None)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "ARTIFACT_NOT_FOUND", "message": str(error)}) from error


@router.post("/in-dev-reviews/{review_id}/approval", response_model=ApiEnvelope)
def approve_in_dev_review(review_id: str, request: InDevReviewApprovalRequest) -> ApiEnvelope:
    try:
        service = InDevReviewService()
        result = service.approve_review(review_id, approved=request.approved, notes=request.notes)
        return ApiEnvelope(success=True, data=result.model_dump(mode="json"), error=None)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "ARTIFACT_NOT_FOUND", "message": str(error)}) from error


def _create_in_dev_review_for_run(run_id: str) -> dict:
    try:
        review = InDevReviewService().create_review(InDevReviewRequest(run_id=run_id))
        return review.model_dump(mode="json")
    except Exception as error:
        return {
            "status": "failed",
            "error": str(error),
        }
