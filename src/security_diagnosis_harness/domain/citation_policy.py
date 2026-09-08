"""最小 Citation Policy。

校验模型候选结论的引用是否成立。Phase 0B 还没有 Evidence Store，
因此直接用 `SecurityDiagnosisCase.evidence` 列表做校验。

核心口径：

- 任何候选结论都必须至少引用一条 Evidence，不允许零引用结论；
- `probable` 必须至少引用一条设备事实 Evidence（状态 / 告警 / 配置）；
- `possible` 可以只引用 knowledge_sop；
- 每个被引用 ID 都必须属于当前诊断；
- 模型不能产生 `confirmed`。
"""

from __future__ import annotations

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.conclusion import (
    ConclusionConfidence,
    DiagnosisConclusion,
)
from security_diagnosis_harness.domain.errors import CitationPolicyViolation
from security_diagnosis_harness.domain.evidence import EvidenceType

# 设备事实类证据：设备状态、告警事件、配置快照。
DEVICE_FACT_EVIDENCE_TYPES: frozenset[EvidenceType] = frozenset(
    {
        EvidenceType.DEVICE_STATUS,
        EvidenceType.DEVICE_ALARM,
        EvidenceType.DEVICE_CONFIG,
    }
)


class CitationPolicy:
    """结论引用校验。"""

    def validate(self, conclusion: DiagnosisConclusion, case: SecurityDiagnosisCase) -> None:
        """校验结论引用，不通过则抛出 `CitationPolicyViolation`。

        校验顺序：诊断归属 -> 可信度合法 -> 至少一条引用 -> 引用归属 -> probable 设备事实。
        """
        if conclusion.diagnosis_id != case.diagnosis_id:
            raise CitationPolicyViolation(
                f"结论属于诊断 {conclusion.diagnosis_id}，不能用于诊断 {case.diagnosis_id}"
            )

        if conclusion.confidence not in tuple(ConclusionConfidence):
            raise CitationPolicyViolation(
                f"结论可信度非法: {conclusion.confidence}（模型不能产生 confirmed）"
            )

        # 可信诊断不允许"无证据结论"：任何候选结论都必须至少引用一条 Evidence。
        if not conclusion.cited_evidence_ids:
            raise CitationPolicyViolation(
                f"结论必须至少引用一条 Evidence，当前结论 {conclusion.conclusion_id} 没有任何引用"
            )

        known_evidence = {evidence.evidence_id: evidence for evidence in case.evidence}
        cited = [
            known_evidence[evidence_id]
            for evidence_id in conclusion.cited_evidence_ids
            if evidence_id in known_evidence
        ]

        unknown_ids = [
            evidence_id
            for evidence_id in conclusion.cited_evidence_ids
            if evidence_id not in known_evidence
        ]
        if unknown_ids:
            raise CitationPolicyViolation(
                f"结论引用了不属于诊断 {case.diagnosis_id} 的 evidence: {unknown_ids}"
            )

        has_device_fact = any(
            evidence.evidence_type in DEVICE_FACT_EVIDENCE_TYPES for evidence in cited
        )
        if conclusion.confidence is ConclusionConfidence.PROBABLE and not has_device_fact:
            raise CitationPolicyViolation(
                "probable 结论必须至少引用一条设备事实 Evidence"
                f"（{sorted(item.value for item in DEVICE_FACT_EVIDENCE_TYPES)}）"
            )
