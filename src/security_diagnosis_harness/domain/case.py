"""一次安防设备诊断用例及其状态机。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.common import new_id, utc_now
from security_diagnosis_harness.domain.conclusion import DiagnosisConclusion
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.errors import (
    EvidenceDiagnosisMismatch,
    InvalidStatusTransition,
    ReviewNotAllowed,
    UnknownEvidenceReference,
)
from security_diagnosis_harness.domain.evidence import DiagnosisEvidence
from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction

# 终态：不允许再发生任何状态变化。
TERMINAL_STATUSES: frozenset[SecurityDiagnosisStatus] = frozenset(
    {
        SecurityDiagnosisStatus.CONFIRMED,
        SecurityDiagnosisStatus.REJECTED,
        SecurityDiagnosisStatus.INCONCLUSIVE,
    }
)

# 允许的状态跳转。注意：没有任何源状态可以直接跳到 CONFIRMED，
# confirmed 只能由 `apply_human_review` 产生。
ALLOWED_STATUS_TRANSITIONS: dict[SecurityDiagnosisStatus, frozenset[SecurityDiagnosisStatus]] = {
    SecurityDiagnosisStatus.CREATED: frozenset(
        {
            SecurityDiagnosisStatus.INVESTIGATING,
            SecurityDiagnosisStatus.WAITING_FOR_INPUT,
            SecurityDiagnosisStatus.REJECTED,
            SecurityDiagnosisStatus.INCONCLUSIVE,
        }
    ),
    SecurityDiagnosisStatus.INVESTIGATING: frozenset(
        {
            SecurityDiagnosisStatus.WAITING_FOR_INPUT,
            SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION,
            SecurityDiagnosisStatus.REJECTED,
            SecurityDiagnosisStatus.INCONCLUSIVE,
        }
    ),
    SecurityDiagnosisStatus.WAITING_FOR_INPUT: frozenset(
        {
            SecurityDiagnosisStatus.INVESTIGATING,
            SecurityDiagnosisStatus.REJECTED,
            SecurityDiagnosisStatus.INCONCLUSIVE,
        }
    ),
    SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION: frozenset(
        {
            SecurityDiagnosisStatus.INVESTIGATING,
            SecurityDiagnosisStatus.WAITING_FOR_INPUT,
            SecurityDiagnosisStatus.REJECTED,
        }
    ),
    SecurityDiagnosisStatus.CONFIRMED: frozenset(),
    SecurityDiagnosisStatus.REJECTED: frozenset(),
    SecurityDiagnosisStatus.INCONCLUSIVE: frozenset(),
}


class SecurityDiagnosisCase(BaseModel):
    """一次安防设备诊断。"""

    model_config = ConfigDict(extra="forbid")

    diagnosis_id: str = Field(default_factory=lambda: new_id("diag"))
    fault_type: SecurityFaultType
    device_id: str = Field(min_length=1)
    reporter: str = Field(min_length=1)
    description: str = ""
    status: SecurityDiagnosisStatus = SecurityDiagnosisStatus.CREATED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    evidence: list[DiagnosisEvidence] = Field(default_factory=list)
    conclusion: DiagnosisConclusion | None = None
    reviews: list[HumanReview] = Field(default_factory=list)

    # ------------------------------------------------------------------ 状态
    def can_transition_to(self, target: SecurityDiagnosisStatus) -> bool:
        """是否允许跳转到目标状态（confirmed 永远返回 False）。"""
        if target is SecurityDiagnosisStatus.CONFIRMED:
            return False
        return target in ALLOWED_STATUS_TRANSITIONS[self.status]

    def transition_to(self, target: SecurityDiagnosisStatus) -> None:
        """执行普通状态推进。禁止直接推进到 confirmed。"""
        if target is SecurityDiagnosisStatus.CONFIRMED:
            raise InvalidStatusTransition(
                "confirmed 只能由人工 review 产生，禁止通过普通状态流直接跳转"
            )
        if target not in ALLOWED_STATUS_TRANSITIONS[self.status]:
            raise InvalidStatusTransition(
                f"非法状态跳转: {self.status.value} -> {target.value}"
            )
        self.status = target
        self._touch()

    # ------------------------------------------------------------------ 证据
    def add_evidence(self, evidence: DiagnosisEvidence) -> DiagnosisEvidence:
        """挂接证据。同一诊断下内容重复的证据按 hash 去重。"""
        if not evidence.belongs_to(self.diagnosis_id):
            raise EvidenceDiagnosisMismatch(
                f"evidence {evidence.evidence_id} 不属于诊断 {self.diagnosis_id}"
            )
        if any(item.content_hash == evidence.content_hash for item in self.evidence):
            return evidence
        self.evidence.append(evidence)
        self._touch()
        return evidence

    # ------------------------------------------------------------------ 结论
    def set_conclusion(self, conclusion: DiagnosisConclusion) -> DiagnosisConclusion:
        """登记模型候选结论，并校验引用只指向本诊断的证据。"""
        if not conclusion.belongs_to(self.diagnosis_id):
            raise EvidenceDiagnosisMismatch(
                f"conclusion {conclusion.conclusion_id} 不属于诊断 {self.diagnosis_id}"
            )
        known = {item.evidence_id for item in self.evidence}
        unknown = [eid for eid in conclusion.cited_evidence_ids if eid not in known]
        if unknown:
            raise UnknownEvidenceReference(
                f"结论引用了不属于诊断 {self.diagnosis_id} 的 evidence: {unknown}"
            )
        self.conclusion = conclusion
        self._touch()
        return conclusion

    # ------------------------------------------------------------------ 审核
    def apply_human_review(self, review: HumanReview) -> HumanReview:
        """执行人工审核，这是产生 confirmed 的唯一入口。"""
        if not review.belongs_to(self.diagnosis_id):
            raise EvidenceDiagnosisMismatch(
                f"review {review.review_id} 不属于诊断 {self.diagnosis_id}"
            )
        if self.status in TERMINAL_STATUSES:
            raise ReviewNotAllowed(f"诊断已处于终态 {self.status.value}，不能再审核")

        if review.action is HumanReviewAction.CONFIRM:
            if self.status is not SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION:
                raise ReviewNotAllowed(
                    f"只有在 {SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION.value} "
                    f"状态下才能确认，当前状态 {self.status.value}"
                )
            if self.conclusion is None:
                raise ReviewNotAllowed("没有候选结论，不能确认诊断")
            target = SecurityDiagnosisStatus.CONFIRMED
        elif review.action is HumanReviewAction.REJECT:
            target = SecurityDiagnosisStatus.REJECTED
        else:
            target = SecurityDiagnosisStatus.WAITING_FOR_INPUT

        self.reviews.append(review)
        self.status = target
        self._touch()
        return review

    def _touch(self) -> None:
        self.updated_at = utc_now()
