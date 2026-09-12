"""真实模型评测使用的公开诊断分类目录，不包含案例标准答案。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from security_diagnosis_harness.application.access_diagnosis_rules import (
    AccessDiagnosisLabel,
)
from security_diagnosis_harness.application.alarm_diagnosis_rules import (
    AlarmDiagnosisLabel,
)
from security_diagnosis_harness.application.camera_diagnosis_rules import (
    CameraDiagnosisLabel,
)
from security_diagnosis_harness.application.recording_diagnosis_rules import (
    RecordingDiagnosisLabel,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceType


class EvaluationTaxonomy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_labels: tuple[str, ...]
    evidence_types: tuple[EvidenceType, ...]


_TAXONOMIES: dict[SecurityFaultType, EvaluationTaxonomy] = {
    SecurityFaultType.CAMERA_BLACK_SCREEN: EvaluationTaxonomy(
        candidate_labels=tuple(item.value for item in CameraDiagnosisLabel),
        evidence_types=(
            EvidenceType.DEVICE_STATUS,
            EvidenceType.DEVICE_ALARM,
            EvidenceType.DEVICE_CONFIG,
            EvidenceType.DEVICE_CHANNEL,
            EvidenceType.DEVICE_STREAM,
            EvidenceType.PLATFORM_PULL,
            EvidenceType.KNOWLEDGE_SOP,
        ),
    ),
    SecurityFaultType.RECORDING_MISSING: EvaluationTaxonomy(
        candidate_labels=tuple(item.value for item in RecordingDiagnosisLabel),
        evidence_types=(
            EvidenceType.RECORDING_PLAN,
            EvidenceType.STORAGE_STATUS,
            EvidenceType.PLAYBACK_CHECK,
            EvidenceType.KNOWLEDGE_SOP,
        ),
    ),
    SecurityFaultType.ACCESS_CARD_FAILED: EvaluationTaxonomy(
        candidate_labels=tuple(item.value for item in AccessDiagnosisLabel),
        evidence_types=(
            EvidenceType.ACCESS_CONTROLLER,
            EvidenceType.ACCESS_DOOR,
            EvidenceType.ACCESS_CREDENTIAL,
            EvidenceType.ACCESS_POLICY,
            EvidenceType.ACCESS_EVENT,
            EvidenceType.KNOWLEDGE_SOP,
        ),
    ),
    SecurityFaultType.ALARM_FALSE_POSITIVE: EvaluationTaxonomy(
        candidate_labels=tuple(item.value for item in AlarmDiagnosisLabel),
        evidence_types=(
            EvidenceType.ALARM_RULE,
            EvidenceType.ALARM_SIGNAL,
            EvidenceType.ALARM_ENVIRONMENT,
            EvidenceType.ALARM_VERIFICATION,
            EvidenceType.ALARM_CORRELATION,
            EvidenceType.KNOWLEDGE_SOP,
        ),
    ),
}


def evaluation_taxonomy(fault_type: SecurityFaultType) -> EvaluationTaxonomy:
    """返回完整故障域目录；目录本身不含任何案例期望答案。"""
    return _TAXONOMIES[fault_type]

