"""FastAPI 应用工厂。

注意：模块级 `app = create_app()` 保持**内存装配**，导入本模块不产生任何
副作用（不建目录、不建数据库、不跑迁移）。正式本地 SQLite 运行入口是
`scripts/run_api.py`。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from security_diagnosis_harness import __version__
from security_diagnosis_harness.api.routes.diagnoses import create_diagnoses_router
from security_diagnosis_harness.api.schemas import ApiResponse, HealthData
from security_diagnosis_harness.application.diagnoses import SecurityDiagnosisApplicationService
from security_diagnosis_harness.application.errors import (
    ApplicationError,
    DiagnosisAlreadyExistsError,
    DiagnosisNotFoundError,
    KnowledgeAlreadyExistsError,
    KnowledgeNotFoundError,
    RepositoryPersistenceError,
    UnsupportedFaultTypeError,
)
from security_diagnosis_harness.domain.errors import (
    CitationPolicyViolation,
    DomainError,
    InvalidStatusTransition,
    ReviewNotAllowed,
)

API_PREFIX = "/api/v1"

# 503 的安全文案：绝不回显底层 ORM 异常、SQL、URL 或路径。
REPOSITORY_UNAVAILABLE_CODE = "repository_unavailable"
REPOSITORY_UNAVAILABLE_MESSAGE = "诊断数据暂时不可用"

# 领域/应用异常到 HTTP 状态码的受控映射，避免把原始堆栈抛给调用方。
# 顺序敏感：子类必须排在父类之前。
ERROR_STATUS_CODES: tuple[tuple[type[BaseException], int], ...] = (
    (CitationPolicyViolation, 422),
    (UnsupportedFaultTypeError, 422),
    (RepositoryPersistenceError, 503),
    (DiagnosisAlreadyExistsError, 409),
    (KnowledgeAlreadyExistsError, 409),
    (ReviewNotAllowed, 409),
    (InvalidStatusTransition, 409),
    (DiagnosisNotFoundError, 404),
    (KnowledgeNotFoundError, 404),
    (ApplicationError, 500),
    (DomainError, 400),
)

ERROR_CODES: tuple[tuple[type[BaseException], str], ...] = (
    (CitationPolicyViolation, "citation_policy_violation"),
    (UnsupportedFaultTypeError, "unsupported_fault_type"),
    (RepositoryPersistenceError, REPOSITORY_UNAVAILABLE_CODE),
    (DiagnosisAlreadyExistsError, "diagnosis_already_exists"),
    (KnowledgeAlreadyExistsError, "knowledge_already_exists"),
    (ReviewNotAllowed, "review_not_allowed"),
    (InvalidStatusTransition, "invalid_status_transition"),
    (DiagnosisNotFoundError, "diagnosis_not_found"),
    (KnowledgeNotFoundError, "knowledge_not_found"),
)

# 这些异常对外只允许返回安全文案，不暴露内部细节。
SANITIZED_MESSAGES: tuple[tuple[type[BaseException], str], ...] = (
    (RepositoryPersistenceError, REPOSITORY_UNAVAILABLE_MESSAGE),
)


def _error_payload(code: str, message: str) -> dict[str, object]:
    return ApiResponse[None](code=code, message=message, data=None).model_dump(mode="json")


def _status_code_for(error: BaseException) -> int:
    for error_type, status_code in ERROR_STATUS_CODES:
        if isinstance(error, error_type):
            return status_code
    return 400


def _code_for(error: BaseException) -> str:
    if isinstance(error, RepositoryPersistenceError):
        return REPOSITORY_UNAVAILABLE_CODE
    for error_type, code in ERROR_CODES:
        if isinstance(error, error_type):
            return code
    return "application_error"


def _message_for(error: BaseException) -> str:
    for error_type, message in SANITIZED_MESSAGES:
        if isinstance(error, error_type):
            return message
    return str(error)


def create_app(
    service: SecurityDiagnosisApplicationService | None = None,
    *,
    repository_mode: str = "memory",
    database_ready: bool = True,
    phase: str = "6B",
) -> FastAPI:
    """创建 FastAPI 应用（纯工厂，无副作用）。

    Args:
        service: 应用服务；为 None 时用默认**内存**装配（StaticDeviceGateway + FakeLLM）。
        repository_mode: 仅用于 health 展示，不参与装配。
        database_ready: 仅用于 health 展示；正式入口会传入运行时真实状态。
        phase: health 中展示的阶段标识。

    说明：默认装配是**安全的内存/测试装配**；正式本地 SQLite 运行入口是
    `scripts/run_api.py`。
    """
    if service is None:
        from security_diagnosis_harness.bootstrap.container import build_service

        service = build_service()

    app = FastAPI(
        title="Security Device Diagnosis Harness",
        description="面向安防设备运维的可信诊断 Agent Harness（Phase 6B）",
        version=__version__,
    )
    app.state.service = service
    app.state.repository_mode = repository_mode
    app.state.database_ready = database_ready
    app.state.phase = phase

    @app.get("/health", response_model=ApiResponse[HealthData], tags=["ops"])
    def health() -> ApiResponse[HealthData]:
        ready_check = getattr(app.state, "database_ready", True)
        ready = bool(ready_check() if callable(ready_check) else ready_check)
        mode_getter = getattr(app.state, "repository_mode", "memory")
        mode = mode_getter() if callable(mode_getter) else mode_getter
        return ApiResponse(
            data=HealthData(
                status="ok",
                service="security-diagnosis-harness",
                version=__version__,
                phase=getattr(app.state, "phase", "6B"),
                repository_mode=mode,
                database_ready=ready,
            )
        )

    @app.exception_handler(ApplicationError)
    def _handle_application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_code_for(exc),
            content=_error_payload(_code_for(exc), _message_for(exc)),
        )

    @app.exception_handler(DomainError)
    def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_code_for(exc),
            content=_error_payload(_code_for(exc), str(exc)),
        )

    app.include_router(create_diagnoses_router(service))
    return app


# 模块级 app 保持内存装配：导入本模块不产生任何副作用。
app = create_app()
