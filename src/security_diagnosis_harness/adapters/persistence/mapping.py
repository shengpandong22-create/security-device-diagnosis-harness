"""Domain 对象与持久化 JSON / ORM 行之间的双向转换。

职责边界：

- **不做业务判断，也不另写一套脱敏正则**；
- 写入前对**整个聚合**做深层安全规范化（`sanitize_*`），
  复用 Domain 自己的 `model_validator` 作为唯一脱敏规则；
- 读回时必须重新构造真正的 Domain 对象（`model_validate`），枚举 / 时间自动恢复；
- 时间统一为 timezone-aware UTC，写入前把 naive 时间补成 UTC；
- 复杂子结构以 `model_dump(mode="json")` 落地，读到的是纯 JSON 可序列化数据。

为什么需要深层规范化：Pydantic 允许在构造之后就地修改字段
（例如 `evidence.payload["password"] = "..."`），这种修改不会再触发
`model_validator`。因此持久化前必须把聚合重新走一遍 Domain 校验。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.conclusion import DiagnosisConclusion
from security_diagnosis_harness.domain.evidence import DiagnosisEvidence
from security_diagnosis_harness.domain.knowledge import KnowledgeCandidate, KnowledgeReview
from security_diagnosis_harness.domain.review import HumanReview


def ensure_aware(value: datetime) -> datetime:
    """把 naive 时间按 UTC 处理，已是 aware 的原样返回。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def sanitize_case_for_persistence(case: SecurityDiagnosisCase) -> SecurityDiagnosisCase:
    """返回一个经 Domain 校验重新规范化的诊断副本。

    - 使用 `model_dump(mode="python")` 得到独立数据，**不修改调用方原对象**；
    - 重新 `model_validate` 会让嵌套的 Evidence / Conclusion / Review
      validators 全部再次执行（含脱敏与 content_hash 重算）；
    - Evidence 的 hash 会基于**脱敏后的最终内容**重新计算。
    """
    dumped = case.model_dump(mode="python")
    return SecurityDiagnosisCase.model_validate(dumped)


def sanitize_knowledge_for_persistence(
    candidate: KnowledgeCandidate,
) -> KnowledgeCandidate:
    """返回一个经 Domain 校验重新规范化的知识候选副本。

    覆盖 title / summary / root_cause / symptoms / troubleshooting_steps /
    excluded_causes / metadata 以及嵌套 KnowledgeReview。
    """
    dumped = candidate.model_dump(mode="python")
    return KnowledgeCandidate.model_validate(dumped)


def case_to_columns(case: SecurityDiagnosisCase) -> dict[str, Any]:
    """把诊断聚合拆成 ORM 列字典（先做深层安全规范化）。"""
    safe = sanitize_case_for_persistence(case)
    return {
        "diagnosis_id": safe.diagnosis_id,
        "fault_type": safe.fault_type.value,
        "device_id": safe.device_id,
        "reporter": safe.reporter,
        "description": safe.description,
        "status": safe.status.value,
        "created_at": ensure_aware(safe.created_at),
        "updated_at": ensure_aware(safe.updated_at),
        "version": safe.version,
        "evidence": [item.model_dump(mode="json") for item in safe.evidence],
        "conclusion": (
            safe.conclusion.model_dump(mode="json") if safe.conclusion is not None else None
        ),
        "reviews": [item.model_dump(mode="json") for item in safe.reviews],
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
        version=row.version,
    )


def knowledge_to_columns(candidate: KnowledgeCandidate) -> dict[str, Any]:
    """把知识候选拆成 ORM 列字典（先做深层安全规范化）。"""
    safe = sanitize_knowledge_for_persistence(candidate)
    return {
        "knowledge_id": safe.knowledge_id,
        "fault_type": safe.fault_type.value,
        "candidate_label": safe.candidate_label,
        "title": safe.title,
        "summary": safe.summary,
        "root_cause": safe.root_cause,
        "status": safe.status.value,
        "source_diagnosis_id": safe.source_diagnosis_id,
        "source_conclusion_id": safe.source_conclusion_id,
        "created_at": ensure_aware(safe.created_at),
        "updated_at": ensure_aware(safe.updated_at),
        "version": safe.version,
        "symptoms": list(safe.symptoms),
        "troubleshooting_steps": list(safe.troubleshooting_steps),
        "excluded_causes": list(safe.excluded_causes),
        "source_evidence_ids": list(safe.source_evidence_ids),
        "reviews": [item.model_dump(mode="json") for item in safe.reviews],
        "source": safe.source.value,
        "redacted": safe.redacted,
        "metadata_json": dict(safe.metadata),
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
        version=row.version,
    )


__all__ = [
    "case_to_columns",
    "columns_to_case",
    "columns_to_knowledge",
    "ensure_aware",
    "knowledge_to_columns",
    "sanitize_case_for_persistence",
    "sanitize_knowledge_for_persistence",
]
