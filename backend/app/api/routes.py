import os

from fastapi import APIRouter, HTTPException

from app.data.tushare_client import TushareMarketDataClient
from app.domain.errors import DataUnavailableError
from app.domain.models import ApiEnvelope, ScanRequest
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
