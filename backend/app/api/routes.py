import os

from fastapi import APIRouter, HTTPException

from app.data.market_data_client_factory import build_market_data_client
from app.domain.errors import DataUnavailableError
from app.domain.models import (
    ApiEnvelope,
    BacktestRequest,
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
        service = ScanService(build_market_data_client())
        return ApiEnvelope(success=True, data=service.scan(request).model_dump(mode="json"), error=None)
    except DataUnavailableError as error:
        raise HTTPException(status_code=503, detail={"code": error.code, "message": error.message}) from error


@router.post("/backtests", response_model=ApiEnvelope)
def create_backtest(request: BacktestRequest) -> ApiEnvelope:
    try:
        service = BacktestService(build_market_data_client())
        return ApiEnvelope(success=True, data=service.run(request).model_dump(mode="json"), error=None)
    except DataUnavailableError as error:
        raise HTTPException(status_code=503, detail={"code": error.code, "message": error.message}) from error


@router.post("/research-runs", response_model=ApiEnvelope)
def create_research_run(request: ResearchRunRequest) -> ApiEnvelope:
    try:
        service = ResearchRunService(
            build_market_data_client(),
            research_agent_client=DeepSeekResearchAgentClient.from_environment(),
        )
        research_run = service.run(request)
        return ApiEnvelope(success=True, data=research_run.model_dump(mode="json"), error=None)
    except DataUnavailableError as error:
        raise HTTPException(status_code=503, detail={"code": error.code, "message": error.message}) from error
