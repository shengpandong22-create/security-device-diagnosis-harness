"""API Schema：统一 JSON 响应结构与诊断相关 DTO。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.conclusion import ConclusionConfidence
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.domain.review import HumanReviewAction


class ApiResponse[T](BaseModel):
    """统一响应信封。"""

    model_config = ConfigDict(extra="forbid")

    code: str = "ok"
    message: str = "ok"
    data: T


class HealthData(BaseModel):
    """健康检查载荷。

    Phase 6B-1 新增 `repository_mode` / `database_ready`，
    均带兼容默认值，避免破坏既有调用方。

    安全约束：不返回 database_url、绝对路径、Engine、Session 或凭证。
    """

    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    service: str = "security-diagnosis-harness"
    version: str
    phase: str = "0C"
    repository_mode: str = "memory"
    database_ready: bool = True


class CreateDiagnosisRequest(BaseModel):
    """创建诊断请求。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    fault_type: SecurityFaultType
    reporter: str = Field(min_length=1)
    description: str = ""


class DiagnosisData(BaseModel):
    """诊断概览。"""

    model_config = ConfigDict(extra="forbid")

    diagnosis_id: str
    device_id: str
    fault_type: SecurityFaultType
    status: SecurityDiagnosisStatus
    reporter: str
    description: str
    evidence_count: int
    review_count: int
    has_conclusion: bool
    created_at: datetime
    updated_at: datetime


class ConclusionData(BaseModel):
    """候选结论概览。"""

    model_config = ConfigDict(extra="forbid")

    conclusion_id: str
    fault_type: SecurityFaultType
    summary: str
    root_cause: str | None = None
    confidence: ConclusionConfidence
    cited_evidence_ids: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class RunDiagnosisData(BaseModel):
    """运行诊断的响应。"""

    model_config = ConfigDict(extra="forbid")

    diagnosis_id: str
    ok: bool
    status: SecurityDiagnosisStatus
    evidence_count: int
    conclusion: ConclusionData | None = None
    citations_repaired: bool = False
    confidence_downgraded: bool = False
    rounds: int = 0
    tool_calls: int = 0
    error: str | None = None


class ReviewRequest(BaseModel):
    """人工审核请求。"""

    model_config = ConfigDict(extra="forbid")

    action: HumanReviewAction
    reviewer: str = Field(min_length=1)
    comment: str = ""


class ReviewData(BaseModel):
    """人工审核响应。"""

    model_config = ConfigDict(extra="forbid")

    diagnosis_id: str
    status: SecurityDiagnosisStatus
    action: HumanReviewAction
    reviewer: str
    comment: str
    review_id: str


def to_diagnosis_data(case: object) -> DiagnosisData:
    """把 Case 转成 API 概览 DTO（避免直接暴露领域对象内部结构）。"""
    from security_diagnosis_harness.domain.case import SecurityDiagnosisCase

    assert isinstance(case, SecurityDiagnosisCase)
    return DiagnosisData(
        diagnosis_id=case.diagnosis_id,
        device_id=case.device_id,
        fault_type=case.fault_type,
        status=case.status,
        reporter=case.reporter,
        description=case.description,
        evidence_count=len(case.evidence),
        review_count=len(case.reviews),
        has_conclusion=case.conclusion is not None,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def to_conclusion_data(conclusion: object) -> ConclusionData:
    """把 DiagnosisConclusion 转成 API DTO。"""
    from security_diagnosis_harness.domain.conclusion import DiagnosisConclusion

    assert isinstance(conclusion, DiagnosisConclusion)
    return ConclusionData(
        conclusion_id=conclusion.conclusion_id,
        fault_type=conclusion.fault_type,
        summary=conclusion.summary,
        root_cause=conclusion.root_cause,
        confidence=conclusion.confidence,
        cited_evidence_ids=list(conclusion.cited_evidence_ids),
        next_steps=list(conclusion.next_steps),
    )
