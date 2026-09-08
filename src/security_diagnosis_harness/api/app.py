"""FastAPI 应用工厂。"""

from __future__ import annotations

from fastapi import FastAPI

from security_diagnosis_harness import __version__
from security_diagnosis_harness.api.schemas import ApiResponse, HealthData

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    """创建最小 FastAPI 应用。"""
    app = FastAPI(
        title="Security Device Diagnosis Harness",
        description="面向安防设备运维的可信诊断 Agent Harness（Phase 0）",
        version=__version__,
    )

    @app.get("/health", response_model=ApiResponse[HealthData], tags=["ops"])
    def health() -> ApiResponse[HealthData]:
        return ApiResponse(
            data=HealthData(
                status="ok",
                service="security-diagnosis-harness",
                version=__version__,
                phase="0A",
            )
        )

    return app


app = create_app()
