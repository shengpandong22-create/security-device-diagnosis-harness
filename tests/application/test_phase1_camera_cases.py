"""Phase 1 应用服务：多摄像头黑屏子案例。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.application.camera_diagnosis_rules import CameraDiagnosisLabel
from security_diagnosis_harness.bootstrap.container import (
    CAMERA_CASES_DATA_PATH,
    build_phase1_container,
)
from security_diagnosis_harness.domain.citation_policy import (
    DEVICE_FACT_EVIDENCE_TYPES,
    MIN_DEVICE_FACT_TYPES_FOR_PROBABLE,
)
from security_diagnosis_harness.domain.conclusion import ConclusionConfidence
from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.domain.errors import ReviewNotAllowed
from security_diagnosis_harness.domain.evidence import EvidenceType
from security_diagnosis_harness.domain.review import HumanReviewAction

EXPECTED_LABELS = {
    "camera_offline": CameraDiagnosisLabel.DEVICE_OFFLINE_OR_NETWORK_UNREACHABLE,
    "channel_offline": CameraDiagnosisLabel.CHANNEL_BINDING_OR_PLATFORM_ACCESS_ISSUE,
    "stream_publish_failed": CameraDiagnosisLabel.STREAM_PUBLISH_OR_ENCODER_ISSUE,
    "high_bitrate_encoder_timeout": CameraDiagnosisLabel.OVERLOADED_ENCODING_CONFIGURATION,
    "platform_pull_failed": CameraDiagnosisLabel.PLATFORM_PULL_OR_ACCESS_PATH_ISSUE,
}

SENSITIVE_MARKER = "sample-admin-pwd-not-real"


@pytest.fixture
def container():
    return build_phase1_container()


@pytest.fixture
def service(container):
    return container.service


def create_case(service, device_id: str):
    return service.create_diagnosis(
        device_id=device_id,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter="eval-harness",
        description="摄像头预览黑屏",
    )


@pytest.mark.parametrize("case_id", sorted(EXPECTED_LABELS))
def test_each_phase1_case_runs(service, container, case_id):
    ref = next(item for item in container.gateway.list_cases() if item.case_id == case_id)

    case = create_case(service, ref.device_id)
    result = service.run_diagnosis(case.diagnosis_id)

    assert result.ok is True, result.error
    assert result.status is SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
    assert result.conclusion is not None


@pytest.mark.parametrize("case_id", sorted(EXPECTED_LABELS))
def test_each_phase1_case_produces_expected_label(service, container, case_id):
    expected = EXPECTED_LABELS[case_id]
    ref = next(item for item in container.gateway.list_cases() if item.case_id == case_id)

    case = create_case(service, ref.device_id)
    result = service.run_diagnosis(case.diagnosis_id)

    assert result.candidate_label is expected
    assert result.candidate_explanation
    assert result.evidence_chain
    assert result.excluded_candidates
    assert result.troubleshooting_order


@pytest.mark.parametrize("case_id", sorted(EXPECTED_LABELS))
def test_each_phase1_case_collects_camera_facts(service, container, case_id):
    ref = next(item for item in container.gateway.list_cases() if item.case_id == case_id)

    case = create_case(service, ref.device_id)
    service.run_diagnosis(case.diagnosis_id)
    evidence = service.list_evidence(case.diagnosis_id)

    types = {item.evidence_type for item in evidence}
    assert EvidenceType.DEVICE_STATUS in types
    assert EvidenceType.DEVICE_CHANNEL in types
    assert EvidenceType.DEVICE_STREAM in types
    assert EvidenceType.PLATFORM_PULL in types
    assert len(evidence) >= 3


@pytest.mark.parametrize("case_id", sorted(EXPECTED_LABELS))
def test_probable_cites_at_least_two_device_fact_types(service, container, case_id):
    ref = next(item for item in container.gateway.list_cases() if item.case_id == case_id)

    case = create_case(service, ref.device_id)
    result = service.run_diagnosis(case.diagnosis_id)
    evidence = service.list_evidence(case.diagnosis_id)
    by_id = {item.evidence_id: item for item in evidence}

    assert result.conclusion.confidence is ConclusionConfidence.PROBABLE
    cited_types = {
        by_id[evidence_id].evidence_type
        for evidence_id in result.conclusion.cited_evidence_ids
    }
    device_fact_types = cited_types & DEVICE_FACT_EVIDENCE_TYPES
    assert len(device_fact_types) >= MIN_DEVICE_FACT_TYPES_FOR_PROBABLE


def test_confirmed_still_requires_human_review(service, container):
    ref = container.gateway.list_cases()[0]
    case = create_case(service, ref.device_id)
    service.run_diagnosis(case.diagnosis_id)

    assert service.get_diagnosis(case.diagnosis_id).status is (
        SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
    )

    review = service.review_diagnosis(
        diagnosis_id=case.diagnosis_id,
        action=HumanReviewAction.CONFIRM,
        reviewer="ops-li",
        comment="现场核实",
    )

    assert review.status is SecurityDiagnosisStatus.CONFIRMED
    with pytest.raises(ReviewNotAllowed):
        service.review_diagnosis(
            diagnosis_id=case.diagnosis_id,
            action=HumanReviewAction.REJECT,
            reviewer="ops-li",
        )


def test_report_contains_label_and_excludes_sensitive_values(service, container):
    ref = next(item for item in container.gateway.list_cases() if item.case_id == "camera_offline")
    case = create_case(service, ref.device_id)
    service.run_diagnosis(case.diagnosis_id)

    report = service.render_report(case.diagnosis_id)

    assert CameraDiagnosisLabel.DEVICE_OFFLINE_OR_NETWORK_UNREACHABLE.value in report
    assert "候选根因与证据链" in report
    assert "为什么不是其他候选原因" in report
    assert "建议排查顺序" in report
    assert SENSITIVE_MARKER not in report
    assert REDACTED_VALUE in report


def test_phase0_demo_still_runs():
    """Phase 0 demo 使用的旧样例 + 四个工具仍然可跑通。"""
    from security_diagnosis_harness.bootstrap.container import build_container

    service = build_container().service
    case = service.create_diagnosis(
        device_id="camera-3f-001",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter="ops-zhang",
        description="3 号楼大厅摄像头预览黑屏",
    )
    result = service.run_diagnosis(case.diagnosis_id)

    assert result.ok is True
    assert result.status is SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
    assert result.conclusion.confidence is ConclusionConfidence.PROBABLE
    assert len(result.conclusion.cited_evidence_ids) >= MIN_DEVICE_FACT_TYPES_FOR_PROBABLE


def test_camera_cases_file_path_exists():
    assert CAMERA_CASES_DATA_PATH.exists()
