"""诊断 API。

API 层不直接写领域状态，全部委托给 `SecurityDiagnosisApplicationService`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Response

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
    *,
    demo_mode: bool = False,
) -> APIRouter:
    """创建诊断相关路由。

    职责分离（正式模式）：

    - 操作（创建 / 运行 / 读取）只接受 ``operator`` 角色，且拒绝同时带
      ``reviewer`` 角色的 principal；
    - 审核只接受 ``reviewer`` 角色，且拒绝同时带 ``operator`` 角色的 principal；
    - actor 只能来自认证 principal，正文自报的 reporter/reviewer 一律忽略；
    - 同一 actor 不能审核自己创建（上报）的诊断。

    ``demo_mode=True`` 时才回到"无认证 + 正文自报身份"的显式演示语义。
    """
    router = APIRouter(prefix=ROUTER_PREFIX, tags=["diagnoses"])

    def authorize(
        required_role: str,
        forbidden_role: str,
        authorization: str | None,
    ) -> ApiPrincipal | None:
        if authenticator is None:
            return None
        principal = authenticator.authorize(authorization, required_role)
        if forbidden_role in principal.roles:
            raise HTTPException(status_code=403, detail="permission_denied")
        return principal

    def require_operator(
        authorization: str | None = Header(default=None),
    ) -> ApiPrincipal | None:
        return authorize("operator", "reviewer", authorization)

    def require_reviewer(
        authorization: str | None = Header(default=None),
    ) -> ApiPrincipal | None:
        return authorize("reviewer", "operator", authorization)

    def actor_for(principal: ApiPrincipal | None, declared: str) -> str:
        """确定 actor：正式 API 只认认证 principal；正文自报值仅 Demo 生效。"""
        if demo_mode:
            return declared
        if principal is None:  # 防御性：非 Demo 模式下依赖注入必已认证。
            raise HTTPException(status_code=401, detail="authentication_required")
        return principal.actor

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
            reporter=actor_for(principal, payload.reporter),
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
        reviewer = actor_for(principal, payload.reviewer)
        # 同一 actor 不能自审：先取诊断（不存在时按 404 处理），再比对上报人。
        case = service.get_diagnosis(diagnosis_id)
        if case.reporter == reviewer:
            raise HTTPException(status_code=403, detail="self_review_not_allowed")
        result = service.review_diagnosis(
            diagnosis_id=diagnosis_id,
            action=payload.action,
            reviewer=reviewer,
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
