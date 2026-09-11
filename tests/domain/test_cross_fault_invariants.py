"""Phase 6B-1 收尾：跨故障域结论的 Domain 不变量验收。

无论使用哪个 Runtime / LLM / 工具，都必须满足：

    conclusion.diagnosis_id == case.diagnosis_id
    conclusion.fault_type   == case.fault_type

本文件先把这些不变量写成失败测试，再驱动实现。
"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.citation_policy import CitationPolicy
from security_diagnosis_harness.domain.conclusion import (
    ConclusionConfidence,
    DiagnosisConclusion,
)
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.errors import ConclusionFaultTypeMismatch
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
)

DIAGNOSIS_ID = "diag-cross-1"
CAMERA = SecurityFaultType.CAMERA_BLACK_SCREEN
RECORDING = SecurityFaultType.RECORDING_MISSING


def _case(fault_type: SecurityFaultType = CAMERA) -> SecurityDiagnosisCase:
    case = SecurityDiagnosisCase(
        diagnosis_id=DIAGNOSIS_ID,
        fault_type=fault_type,
        device_id="cam-1",
        reporter="tester",
    )
    case.add_evidence(
        DiagnosisEvidence(
            evidence_id=f"{DIAGNOSIS_ID}-evd-status",
            diagnosis_id=DIAGNOSIS_ID,
            evidence_type=EvidenceType.DEVICE_STATUS,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary="设备离线",
            payload={"online": False},
        )
    )
    case.add_evidence(
        DiagnosisEvidence(
            evidence_id=f"{DIAGNOSIS_ID}-evd-config",
            diagnosis_id=DIAGNOSIS_ID,
            evidence_type=EvidenceType.DEVICE_CONFIG,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary="配置快照",
            payload={"encoding": "H264"},
        )
    )
    case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)
    return case


def _conclusion(fault_type: SecurityFaultType) -> DiagnosisConclusion:
    return DiagnosisConclusion(
        conclusion_id=f"{DIAGNOSIS_ID}-con",
        diagnosis_id=DIAGNOSIS_ID,
        fault_type=fault_type,
        summary="候选结论",
        confidence=ConclusionConfidence.PROBABLE,
        cited_evidence_ids=[f"{DIAGNOSIS_ID}-evd-status", f"{DIAGNOSIS_ID}-evd-config"],
    )


# ---------------------------------------------------------------- 1
def test_case_rejects_conclusion_with_different_fault_type():
    case = _case(CAMERA)

    with pytest.raises(ConclusionFaultTypeMismatch):
        case.set_conclusion(_conclusion(RECORDING))


# ---------------------------------------------------------------- 2
def test_cross_fault_conclusion_does_not_mutate_case():
    case = _case(CAMERA)
    original_status = case.status
    original_conclusion = case.conclusion
    original_updated_at = case.updated_at

    with pytest.raises(ConclusionFaultTypeMismatch):
        case.set_conclusion(_conclusion(RECORDING))

    assert case.conclusion is original_conclusion
    assert case.status is original_status
    assert case.updated_at == original_updated_at


# ---------------------------------------------------------------- 3
def test_citation_policy_rejects_cross_fault_conclusion():
    case = _case(CAMERA)

    with pytest.raises(ConclusionFaultTypeMismatch):
        CitationPolicy().validate(_conclusion(RECORDING), case)


# ---------------------------------------------------------------- 4
@pytest.mark.parametrize(
    "case_fault,conclusion_fault",
    [
        (CAMERA, RECORDING),
        (RECORDING, CAMERA),
        (SecurityFaultType.ACCESS_CARD_FAILED, SecurityFaultType.ALARM_FALSE_POSITIVE),
        (SecurityFaultType.ALARM_FALSE_POSITIVE, SecurityFaultType.ACCESS_CARD_FAILED),
    ],
)
def test_cross_fault_pairs_are_rejected(case_fault, conclusion_fault):
    case = _case(case_fault)

    with pytest.raises(ConclusionFaultTypeMismatch):
        case.set_conclusion(_conclusion(conclusion_fault))


# ---------------------------------------------------------------- 5
def test_same_fault_type_still_passes():
    case = _case(CAMERA)

    accepted = case.set_conclusion(_conclusion(CAMERA))

    assert case.conclusion is accepted
    assert case.conclusion.fault_type is CAMERA


def test_policy_accepts_same_fault_type():
    case = _case(CAMERA)

    CitationPolicy().validate(_conclusion(CAMERA), case)


# ---------------------------------------------------------------- 6
def test_cross_fault_conclusion_cannot_be_confirmed():
    """错误结论不能进入 waiting_for_confirmation，更不可能被 confirm。"""
    from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction

    case = _case(CAMERA)

    with pytest.raises(ConclusionFaultTypeMismatch):
        case.set_conclusion(_conclusion(RECORDING))

    with pytest.raises(Exception):  # noqa: B017 - 没有结论时不允许确认
        case.apply_human_review(
            HumanReview(
                diagnosis_id=DIAGNOSIS_ID,
                action=HumanReviewAction.CONFIRM,
                reviewer="expert",
            )
        )

    assert case.status is SecurityDiagnosisStatus.INVESTIGATING
    assert case.status is not SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
