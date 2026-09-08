"""FastAPI 应用工厂。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from security_diagnosis_harness import __version__
from security_diagnosis_harness.api.routes.diagnoses import create_diagnoses_router
from security_diagnosis_harness.api.schemas import ApiResponse, HealthData
from security_diagnosis_harness.application.diagnoses import SecurityDiagnosisApplicationService
from security_diagnosis_harness.application.errors import ApplicationError
from security_diagnosis_harness.domain.errors import (
    CitationPolicyViolation,
    DomainError,
    InvalidStatusTransition,
    ReviewNotAllowed,
)

API_PREFIX = "/api/v1"

# 领域/应用异常到 HTTP 状态码的受控映射，避免把原始堆栈抛给调用方。
# 顺序敏感：子类必须排在父类之前。
ERROR_STATUS_CODES: tuple[tuple[type[BaseException], int], ...] = (
    (CitationPolicyViolation, 422),
    (ReviewNotAllowed, 409),
    (InvalidStatusTransition, 409),
    (ApplicationError, 404),
    (DomainError, 400),
)

ERROR_CODES: tuple[tuple[type[BaseException], str], ...] = (
    (CitationPolicyViolation, "citation_policy_violation"),
    (ReviewNotAllowed, "review_not_allowed"),
    (InvalidStatusTransition, "invalid_status_transition"),
)


def _error_payload(code: str, message: str) -> dict[str, object]:
    return ApiResponse[None](code=code, message=message, data=None).model_dump(mode="json")


def _status_code_for(error: BaseException) -> int:
    for error_type, status_code in ERROR_STATUS_CODES:
        if isinstance(error, error_type):
            return status_code
    return 400


def _code_for(error: BaseException) -> str:
    for error_type, code in ERROR_CODES:
        if isinstance(error, error_type):
            return code
    return "application_error"


def create_app(service: SecurityDiagnosisApplicationService | None = None) -> FastAPI:
    """创建 FastAPI 应用。

    Args:
        service: 应用服务；为 None 时用默认装配（StaticDeviceGateway + FakeLLM）。
    """
    if service is None:
        from security_diagnosis_harness.bootstrap.container import build_service

        service = build_service()

    app = FastAPI(
        title="Security Device Diagnosis Harness",
        description="面向安防设备运维的可信诊断 Agent Harness（Phase 0）",
        version=__version__,
    )
    app.state.service = service

    @app.get("/health", response_model=ApiResponse[HealthData], tags=["ops"])
    def health() -> ApiResponse[HealthData]:
        return ApiResponse(
            data=HealthData(
                status="ok",
                service="security-diagnosis-harness",
                version=__version__,
                phase="0C",
            )
        )

    @app.exception_handler(ApplicationError)
    def _handle_application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_code_for(exc),
            content=_error_payload(_code_for(exc), str(exc)),
        )

    @app.exception_handler(DomainError)
    def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_code_for(exc),
            content=_error_payload(_code_for(exc), str(exc)),
        )

    app.include_router(create_diagnoses_router(service))
    return app


app = create_app()
