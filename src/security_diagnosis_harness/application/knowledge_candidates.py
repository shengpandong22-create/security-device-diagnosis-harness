"""从人工确认的诊断中提炼可审核知识候选。

这里是 Diagnosis 与 Knowledge 两个领域之间的应用层防腐边界：

- 只接受经过人工确认的诊断；
- 只复制可复用摘要和 Evidence ID，不复制现场 Evidence payload；
- 自动流程永远只生成 candidate，知识确认仍由 KnowledgeReview 完成；
- 生成过程不修改原始 DiagnosisCase、Conclusion 或 Evidence。
"""

from __future__ import annotations

from typing import Protocol

from security_diagnosis_harness.application.errors import (
    KnowledgeCandidateGenerationError,
)
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus
from security_diagnosis_harness.domain.knowledge import (
    LIST_ITEM_MAX_LENGTH,
    TEXT_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    KnowledgeCandidate,
    KnowledgeCandidateSource,
    KnowledgeCandidateStatus,
)
from security_diagnosis_harness.domain.review import HumanReviewAction


class CandidateInsight(Protocol):
    """四类诊断规则输出在知识提炼阶段需要的最小公共视图。"""

    @property
    def label(self) -> object: ...

    @property
    def explanation(self) -> str: ...

    @property
    def troubleshooting_order(self) -> list[str]: ...

    @property
    def excluded_candidates(self) -> list[str]: ...


class DiagnosisKnowledgeSource(Protocol):
    """知识提炼所依赖的诊断应用服务最小契约。"""

    def get_diagnosis(self, diagnosis_id: str) -> SecurityDiagnosisCase: ...

    def infer_candidate_label(self, case: SecurityDiagnosisCase) -> CandidateInsight: ...


class KnowledgeCandidateApplicationService:
    """把 confirmed Diagnosis 转换为尚待审核的 KnowledgeCandidate。"""

    def __init__(self, diagnosis_service: DiagnosisKnowledgeSource) -> None:
        self._diagnosis_service = diagnosis_service

    def generate_from_diagnosis(self, diagnosis_id: str) -> KnowledgeCandidate:
        """提炼一条可追溯知识候选，不持久化也不自动确认。"""
        case = self._diagnosis_service.get_diagnosis(diagnosis_id)
        self._validate_source(case)
        conclusion = case.conclusion
        assert conclusion is not None  # 已由 _validate_source 收窄

        insight = self._diagnosis_service.infer_candidate_label(case)
        label = _label_value(insight.label)
        symptoms = _deduplicate_non_blank(
            [
                case.description,
                *(
                    evidence.summary
                    for evidence in case.evidence
                    if evidence.evidence_id in conclusion.cited_evidence_ids
                ),
            ],
            max_length=LIST_ITEM_MAX_LENGTH,
        )
        troubleshooting_steps = _deduplicate_non_blank(
            [*conclusion.next_steps, *insight.troubleshooting_order],
            max_length=LIST_ITEM_MAX_LENGTH,
        )

        # 领域模型要求这两组内容非空；缺失时使用受控摘要，不虚构现场事实。
        if not symptoms:
            symptoms = [_clip(conclusion.summary, LIST_ITEM_MAX_LENGTH)]
        if not troubleshooting_steps:
            troubleshooting_steps = ["由人工专家补充并审核可复用排查步骤"]

        return KnowledgeCandidate(
            fault_type=case.fault_type,
            candidate_label=label,
            title=_clip(f"{case.fault_type.value}: {label}", TITLE_MAX_LENGTH),
            summary=_clip(conclusion.summary, TEXT_MAX_LENGTH),
            symptoms=symptoms,
            root_cause=_clip(
                conclusion.root_cause or insight.explanation,
                TEXT_MAX_LENGTH,
            ),
            troubleshooting_steps=troubleshooting_steps,
            excluded_causes=_deduplicate_non_blank(
                insight.excluded_candidates,
                max_length=LIST_ITEM_MAX_LENGTH,
            ),
            source=KnowledgeCandidateSource.DIAGNOSIS_CONFIRMATION,
            source_diagnosis_id=case.diagnosis_id,
            source_conclusion_id=conclusion.conclusion_id,
            source_evidence_ids=list(conclusion.cited_evidence_ids),
            status=KnowledgeCandidateStatus.CANDIDATE,
            metadata={"source_review_id": _confirmation_review_id(case)},
        )

    @staticmethod
    def _validate_source(case: SecurityDiagnosisCase) -> None:
        if case.status is not SecurityDiagnosisStatus.CONFIRMED:
            raise KnowledgeCandidateGenerationError(
                f"只有 confirmed 诊断可以生成知识候选，当前状态 {case.status.value}"
            )
        if not any(
            review.action is HumanReviewAction.CONFIRM for review in case.reviews
        ):
            raise KnowledgeCandidateGenerationError(
                "诊断缺少人工 confirm 审核记录，不能生成知识候选"
            )
        if case.conclusion is None:
            raise KnowledgeCandidateGenerationError("诊断没有候选结论，不能生成知识候选")
        if not case.conclusion.cited_evidence_ids:
            raise KnowledgeCandidateGenerationError(
                "诊断结论没有引用 Evidence，不能生成知识候选"
            )
        known_ids = {evidence.evidence_id for evidence in case.evidence}
        if not set(case.conclusion.cited_evidence_ids).issubset(known_ids):
            raise KnowledgeCandidateGenerationError(
                "诊断结论引用了不存在的 Evidence，不能生成知识候选"
            )


def _confirmation_review_id(case: SecurityDiagnosisCase) -> str:
    return next(
        review.review_id
        for review in reversed(case.reviews)
        if review.action is HumanReviewAction.CONFIRM
    )


def _label_value(label: object) -> str:
    value = getattr(label, "value", label)
    return str(value)


def _clip(value: str, max_length: int) -> str:
    return value[:max_length]


def _deduplicate_non_blank(values: list[str], *, max_length: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        cleaned = _clip(cleaned, max_length)
        if cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result
