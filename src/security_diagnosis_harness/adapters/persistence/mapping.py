"""Domain 对象与持久化 JSON / ORM 行之间的双向转换。

职责单一：**只做序列化与反序列化**，不做业务判断，也不重新实现脱敏规则
（脱敏由 Domain 的 `model_validator` 保证，转换过程不得绕过）。

关键约束：

- 读回时必须重新构造真正的 Domain 对象（`model_validate`），枚举 / 时间自动恢复；
- 时间统一为 timezone-aware UTC，写入前把 naive 时间补成 UTC；
- 复杂子结构以 `model_dump(mode="json")` 落地，读到的是纯 JSON 可序列化数据。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.conclusion import DiagnosisConclusion
from security_diagnosis_harness.domain.evidence import DiagnosisEvidence
from security_diagnosis_harness.domain.knowledge import KnowledgeCandidate, KnowledgeReview
from security_diagnosis_harness.domain.redaction import redact_text
from security_diagnosis_harness.domain.review import HumanReview


def ensure_aware(value: datetime) -> datetime:
    """把 naive 时间按 UTC 处理，已是 aware 的原样返回。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def assert_redacted(value: str) -> str:
    """持久化边界的安全断言：写入前再过一次统一脱敏。

    这不是"第二套规则"，而是复用 Domain 同一个脱敏函数的兜底：
    Pydantic 允许在构造后直接赋值绕过 validator，本函数保证这种
    "事后赋值"也不会把明文写进数据库。
    """
    cleaned, _ = redact_text(value)
    return cleaned


def case_to_columns(case: SecurityDiagnosisCase) -> dict[str, Any]:
    """把诊断聚合拆成 ORM 列字典。"""
    return {
        "diagnosis_id": case.diagnosis_id,
        "fault_type": case.fault_type.value,
        "device_id": case.device_id,
        "reporter": case.reporter,
        "description": assert_redacted(case.description),
        "status": case.status.value,
        "created_at": ensure_aware(case.created_at),
        "updated_at": ensure_aware(case.updated_at),
        "evidence": [item.model_dump(mode="json") for item in case.evidence],
        "conclusion": (
            case.conclusion.model_dump(mode="json") if case.conclusion is not None else None
        ),
        "reviews": [item.model_dump(mode="json") for item in case.reviews],
    }


def columns_to_case(row: Any) -> SecurityDiagnosisCase:
    """把 ORM 行重新构造成真正的 `SecurityDiagnosisCase`。"""
    evidence = [DiagnosisEvidence.model_validate(item) for item in (row.evidence or [])]
    conclusion = (
        DiagnosisConclusion.model_validate(row.conclusion) if row.conclusion else None
    )
    reviews = [HumanReview.model_validate(item) for item in (row.reviews or [])]
    return SecurityDiagnosisCase(
        diagnosis_id=row.diagnosis_id,
        fault_type=row.fault_type,
        device_id=row.device_id,
        reporter=row.reporter,
        description=row.description,
        status=row.status,
        created_at=ensure_aware(row.created_at),
        updated_at=ensure_aware(row.updated_at),
        evidence=evidence,
        conclusion=conclusion,
        reviews=reviews,
    )


def knowledge_to_columns(candidate: KnowledgeCandidate) -> dict[str, Any]:
    """把知识候选拆成 ORM 列字典。"""
    return {
        "knowledge_id": candidate.knowledge_id,
        "fault_type": candidate.fault_type.value,
        "candidate_label": candidate.candidate_label,
        "title": assert_redacted(candidate.title),
        "summary": assert_redacted(candidate.summary),
        "root_cause": assert_redacted(candidate.root_cause),
        "status": candidate.status.value,
        "source_diagnosis_id": candidate.source_diagnosis_id,
        "source_conclusion_id": candidate.source_conclusion_id,
        "created_at": ensure_aware(candidate.created_at),
        "updated_at": ensure_aware(candidate.updated_at),
        "symptoms": list(candidate.symptoms),
        "troubleshooting_steps": list(candidate.troubleshooting_steps),
        "excluded_causes": list(candidate.excluded_causes),
        "source_evidence_ids": list(candidate.source_evidence_ids),
        "reviews": [item.model_dump(mode="json") for item in candidate.reviews],
        "source": candidate.source.value,
        "redacted": candidate.redacted,
        "metadata_json": dict(candidate.metadata),
    }


def columns_to_knowledge(row: Any) -> KnowledgeCandidate:
    """把 ORM 行重新构造成真正的 `KnowledgeCandidate`。"""
    reviews = [KnowledgeReview.model_validate(item) for item in (row.reviews or [])]
    return KnowledgeCandidate(
        knowledge_id=row.knowledge_id,
        fault_type=row.fault_type,
        candidate_label=row.candidate_label,
        title=row.title,
        summary=row.summary,
        symptoms=list(row.symptoms or []),
        root_cause=row.root_cause,
        troubleshooting_steps=list(row.troubleshooting_steps or []),
        excluded_causes=list(row.excluded_causes or []),
        source=row.source,
        source_diagnosis_id=row.source_diagnosis_id,
        source_conclusion_id=row.source_conclusion_id,
        source_evidence_ids=list(row.source_evidence_ids or []),
        status=row.status,
        reviews=reviews,
        redacted=row.redacted,
        metadata=dict(row.metadata_json or {}),
        created_at=ensure_aware(row.created_at),
        updated_at=ensure_aware(row.updated_at),
    )


__all__ = [
    "assert_redacted",
    "case_to_columns",
    "columns_to_case",
    "columns_to_knowledge",
    "ensure_aware",
    "knowledge_to_columns",
]
