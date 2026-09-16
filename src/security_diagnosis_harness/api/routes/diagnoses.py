"""诊断 API。

API 层不直接写领域状态，全部委托给 `SecurityDiagnosisApplicationService`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response

from security_diagnosis_harness.api.auth import ApiPrincipal, BearerAuthenticator
from security_diagnosis_harness.api.schemas import (
    ApiResponse,
    CreateDiagnosisRequest,
    ReviewData,
    ReviewRequest,
    RunDiagnosisData,
    to_conclusion_data,
    to_diagnosis_data,
)
from security_diagnosis_harness.application.diagnoses import SecurityDiagnosisApplicationService
from security_diagnosis_harness.domain.evidence import DiagnosisEvidence

ROUTER_PREFIX = "/api/v1/diagnoses"


def create_diagnoses_router(
    service: SecurityDiagnosisApplicationService,
    authenticator: BearerAuthenticator | None = None,
) -> APIRouter:
    """创建诊断相关路由。"""
    router = APIRouter(prefix=ROUTER_PREFIX, tags=["diagnoses"])

    def authorize(
        role: str,
        authorization: str | None,
    ) -> ApiPrincipal | None:
        if authenticator is None:
            return None
        return authenticator.authorize(authorization, role)

    def require_operator(
        authorization: str | None = Header(default=None),
    ) -> ApiPrincipal | None:
        return authorize("operator", authorization)

    def require_reviewer(
        authorization: str | None = Header(default=None),
    ) -> ApiPrincipal | None:
        return authorize("reviewer", authorization)

    operator_dependency = Depends(require_operator)
    reviewer_dependency = Depends(require_reviewer)

    @router.post("", response_model=ApiResponse[object], status_code=201)
    def create_diagnosis(
        payload: CreateDiagnosisRequest,
        principal: ApiPrincipal | None = operator_dependency,
    ) -> ApiResponse[object]:
        case = service.create_diagnosis(
            device_id=payload.device_id,
            fault_type=payload.fault_type,
            reporter=principal.actor if principal is not None else payload.reporter,
            description=payload.description,
        )
        return ApiResponse(data=to_diagnosis_data(case))

    @router.get("", response_model=ApiResponse[object])
    def list_diagnoses(
        principal: ApiPrincipal | None = operator_dependency,
    ) -> ApiResponse[object]:
        cases = [to_diagnosis_data(case) for case in service.list_diagnoses()]
        return ApiResponse(data=cases)

    @router.get("/{diagnosis_id}", response_model=ApiResponse[object])
    def get_diagnosis(
        diagnosis_id: str,
        principal: ApiPrincipal | None = operator_dependency,
    ) -> ApiResponse[object]:
        case = service.get_diagnosis(diagnosis_id)
        return ApiResponse(data=to_diagnosis_data(case))

    @router.post("/{diagnosis_id}/runs", response_model=ApiResponse[object])
    def run_diagnosis(
        diagnosis_id: str,
        principal: ApiPrincipal | None = operator_dependency,
    ) -> ApiResponse[object]:
        result = service.run_diagnosis(diagnosis_id)
        data = RunDiagnosisData(
            diagnosis_id=result.diagnosis_id,
            ok=result.ok,
            status=result.status,
            evidence_count=result.evidence_count,
            conclusion=(
                to_conclusion_data(result.conclusion) if result.conclusion is not None else None
            ),
            citations_repaired=result.citations_repaired,
            confidence_downgraded=result.confidence_downgraded,
            rounds=result.rounds,
            tool_calls=result.tool_calls,
            error=result.error,
            candidate_label=(
                result.candidate_label.value if result.candidate_label is not None else None
            ),
            candidate_explanation=result.candidate_explanation,
            evidence_chain=list(result.evidence_chain),
            excluded_candidates=list(result.excluded_candidates),
            troubleshooting_order=list(result.troubleshooting_order),
            degraded=result.degraded,
            failure_kinds=list(result.failure_kinds),
        )
        return ApiResponse(data=data)

    @router.get(
        "/{diagnosis_id}/evidence",
        response_model=ApiResponse[list[DiagnosisEvidence]],
    )
    def list_evidence(
        diagnosis_id: str,
        principal: ApiPrincipal | None = operator_dependency,
    ) -> ApiResponse[list[DiagnosisEvidence]]:
        evidence = service.list_evidence(diagnosis_id)
        return ApiResponse(data=evidence)

    @router.post("/{diagnosis_id}/review", response_model=ApiResponse[object])
    def review_diagnosis(
        diagnosis_id: str,
        payload: ReviewRequest,
        principal: ApiPrincipal | None = reviewer_dependency,
    ) -> ApiResponse[object]:
        result = service.review_diagnosis(
            diagnosis_id=diagnosis_id,
            action=payload.action,
            reviewer=principal.actor if principal is not None else payload.reviewer,
            comment=payload.comment,
        )
        data = ReviewData(
            diagnosis_id=result.diagnosis_id,
            status=result.status,
            action=result.action,
            reviewer=result.review.reviewer,
            comment=result.review.comment,
            review_id=result.review.review_id,
        )
        return ApiResponse(data=data)

    @router.get("/{diagnosis_id}/report.md", response_class=Response)
    def get_report(
        diagnosis_id: str,
        principal: ApiPrincipal | None = operator_dependency,
    ) -> Response:
        markdown = service.render_report(diagnosis_id)
        return Response(content=markdown, media_type="text/markdown; charset=utf-8")

    return router
