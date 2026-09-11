"""Phase 6A 测试用的领域对象构造器。"""

from __future__ import annotations

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.conclusion import (
    ConclusionConfidence,
    DiagnosisConclusion,
)
from security_diagnosis_harness.domain.device import (
    REDACTED_VALUE,
    redact_sensitive_values,
)
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
    Reliability,
)
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeReview,
    KnowledgeReviewAction,
)
from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction


def build_confirmed_case(diagnosis_id: str = "diag-1") -> SecurityDiagnosisCase:
    """构造一条已走完「证据 -> 结论 -> 人工确认」全流程的诊断。"""
    case = SecurityDiagnosisCase(
        diagnosis_id=diagnosis_id,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        device_id="cam-1",
        reporter="tester",
        description="摄像头黑屏",
    )
    case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)

    status_evidence = DiagnosisEvidence(
        evidence_id=f"{diagnosis_id}-evd-status",
        diagnosis_id=diagnosis_id,
        evidence_type=EvidenceType.DEVICE_STATUS,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary="设备离线",
        payload={"online": False},
        reliability=Reliability.HIGH,
    )
    config_evidence = DiagnosisEvidence(
        evidence_id=f"{diagnosis_id}-evd-config",
        diagnosis_id=diagnosis_id,
        evidence_type=EvidenceType.DEVICE_CONFIG,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary="配置快照",
        payload={
            "encoding": "H264",
            "password": REDACTED_VALUE,
            "access_token": REDACTED_VALUE,
        },
    )
    case.add_evidence(status_evidence)
    case.add_evidence(config_evidence)

    case.set_conclusion(
        DiagnosisConclusion(
            conclusion_id=f"{diagnosis_id}-con",
            diagnosis_id=diagnosis_id,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            summary="设备离线导致黑屏",
            root_cause="设备离线",
            confidence=ConclusionConfidence.PROBABLE,
            cited_evidence_ids=[status_evidence.evidence_id, config_evidence.evidence_id],
        )
    )
    case.transition_to(SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION)
    case.apply_human_review(
        HumanReview(
            diagnosis_id=diagnosis_id,
            action=HumanReviewAction.CONFIRM,
            reviewer="expert",
            comment="与现场一致",
        )
    )
    return case


RAW_PASSWORD = "super-secret-password"
RAW_TOKEN = "raw-access-token"


def build_case_with_raw_credentials(diagnosis_id: str = "diag-secret") -> SecurityDiagnosisCase:
    """构造一条把明文凭证交给 Domain 脱敏的诊断。"""
    case = SecurityDiagnosisCase(
        diagnosis_id=diagnosis_id,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        device_id="cam-secret",
        reporter="tester",
        description="凭证脱敏入库检查",
    )
    redacted, _ = redact_sensitive_values(
        {"password": RAW_PASSWORD, "access_token": RAW_TOKEN, "encoding": "H264"}
    )
    case.add_evidence(
        DiagnosisEvidence(
            evidence_id=f"{diagnosis_id}-evd-config",
            diagnosis_id=diagnosis_id,
            evidence_type=EvidenceType.DEVICE_CONFIG,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary="配置快照",
            payload=redacted,
        )
    )
    return case


def build_knowledge_candidate(
    knowledge_id: str = "knw-1",
    *,
    confirmed: bool = False,
    fault_type: SecurityFaultType = SecurityFaultType.CAMERA_BLACK_SCREEN,
) -> KnowledgeCandidate:
    """构造一条知识候选，可选是否已人工确认。"""
    candidate = KnowledgeCandidate(
        knowledge_id=knowledge_id,
        fault_type=fault_type,
        candidate_label="device_offline",
        title="摄像头离线黑屏",
        summary="摄像头网络不可达导致预览黑屏",
        symptoms=["画面无法预览"],
        root_cause="设备离线或网络中断",
        troubleshooting_steps=["检查供电和网络"],
        excluded_causes=["编码参数异常"],
        source_diagnosis_id="diag-1",
        source_conclusion_id="con-1",
        source_evidence_ids=["evd-1", "evd-2"],
    )
    if confirmed:
        candidate.apply_review(
            KnowledgeReview(
                knowledge_id=candidate.knowledge_id,
                action=KnowledgeReviewAction.CONFIRM,
                reviewer="expert",
                comment="案例可复用",
            )
        )
    return candidate
