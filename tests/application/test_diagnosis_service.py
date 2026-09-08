"""ApplicationService 验收。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.application.errors import DiagnosisNotFoundError
from security_diagnosis_harness.domain.conclusion import ConclusionConfidence
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.domain.errors import (
    CitationPolicyViolation,
    InvalidStatusTransition,
    ReviewNotAllowed,
)
from security_diagnosis_harness.domain.evidence import EvidenceType
from security_diagnosis_harness.domain.review import HumanReviewAction

from .conftest import (
    build_service,
    confirm,
    create_black_screen_case,
    failing_llm,
    knowledge_only_llm,
    no_conclusion_llm,
    no_evidence_llm,
)


def test_create_diagnosis_creates_case(app_service):
    case = app_service.create_diagnosis(
        device_id="camera-3f-001",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter="ops-zhang",
        description="摄像头黑屏",
    )

    assert case.diagnosis_id
    assert case.status is SecurityDiagnosisStatus.CREATED
    assert case.evidence == []
    assert case.conclusion is None
    assert app_service.get_diagnosis(case.diagnosis_id).diagnosis_id == case.diagnosis_id


def test_get_unknown_diagnosis_raises(app_service):
    with pytest.raises(DiagnosisNotFoundError):
        app_service.get_diagnosis("diag_not_exist")


def test_run_diagnosis_moves_to_waiting_for_confirmation(app_service):
    diagnosis_id = create_black_screen_case(app_service)

    result = app_service.run_diagnosis(diagnosis_id)

    assert result.ok is True
    assert result.status is SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
    assert result.evidence_count >= 3
    assert result.conclusion is not None


def test_evidence_drafts_become_real_evidence(app_service):
    diagnosis_id = create_black_screen_case(app_service)

    app_service.run_diagnosis(diagnosis_id)
    evidence = app_service.list_evidence(diagnosis_id)

    assert len(evidence) >= 3
    types = {item.evidence_type for item in evidence}
    assert EvidenceType.DEVICE_STATUS in types
    assert EvidenceType.DEVICE_ALARM in types
    assert EvidenceType.DEVICE_CONFIG in types
    assert all(item.diagnosis_id == diagnosis_id for item in evidence)
    assert all(item.content_hash for item in evidence)


def test_empty_citations_are_repaired_to_real_evidence_ids(app_service):
    diagnosis_id = create_black_screen_case(app_service)

    result = app_service.run_diagnosis(diagnosis_id)
    evidence_ids = {item.evidence_id for item in app_service.list_evidence(diagnosis_id)}

    assert result.citations_repaired is True
    assert result.conclusion is not None
    assert result.conclusion.cited_evidence_ids
    assert set(result.conclusion.cited_evidence_ids) <= evidence_ids


def test_probable_cites_device_fact_evidence(app_service):
    diagnosis_id = create_black_screen_case(app_service)

    result = app_service.run_diagnosis(diagnosis_id)
    by_id = {item.evidence_id: item for item in app_service.list_evidence(diagnosis_id)}
    cited = [by_id[eid] for eid in result.conclusion.cited_evidence_ids]

    assert result.conclusion.confidence is ConclusionConfidence.PROBABLE
    assert any(item.evidence_type is EvidenceType.DEVICE_STATUS for item in cited)


def test_citation_policy_is_executed(app_service, monkeypatch):
    """结论落库前必须经过 CitationPolicy。"""
    calls: list[str] = []
    policy = app_service._citation_policy
    original_validate = policy.validate

    def spy(conclusion, case):
        calls.append(conclusion.conclusion_id)
        original_validate(conclusion, case)

    monkeypatch.setattr(policy, "validate", spy)
    diagnosis_id = create_black_screen_case(app_service)

    result = app_service.run_diagnosis(diagnosis_id)

    assert len(calls) == 1
    assert result.conclusion is not None
    assert calls[0] == result.conclusion.conclusion_id


def test_citation_policy_violation_blocks_conclusion(app_service, monkeypatch):
    """CitationPolicy 拒绝时，结论不能落库，Case 进入 inconclusive。"""

    def always_reject(conclusion, case):
        raise CitationPolicyViolation("测试拒绝")

    monkeypatch.setattr(app_service._citation_policy, "validate", always_reject)
    diagnosis_id = create_black_screen_case(app_service)

    result = app_service.run_diagnosis(diagnosis_id)

    assert result.ok is False
    assert result.status is SecurityDiagnosisStatus.INCONCLUSIVE
    assert result.conclusion is None
    assert app_service.get_diagnosis(diagnosis_id).conclusion is None


def test_probable_downgraded_to_possible_without_device_fact(sample_gateway):
    service, _ = build_service(knowledge_only_llm(), sample_gateway)
    diagnosis_id = create_black_screen_case(service)

    result = service.run_diagnosis(diagnosis_id)

    assert result.ok is True
    assert result.confidence_downgraded is True
    assert result.conclusion.confidence is ConclusionConfidence.POSSIBLE
    types = {item.evidence_type for item in service.list_evidence(diagnosis_id)}
    assert types == {EvidenceType.KNOWLEDGE_SOP}


def test_runner_failure_goes_to_waiting_for_input(sample_gateway):
    service, _ = build_service(failing_llm(), sample_gateway)
    diagnosis_id = create_black_screen_case(service)

    result = service.run_diagnosis(diagnosis_id)

    assert result.ok is False
    assert result.status is SecurityDiagnosisStatus.WAITING_FOR_INPUT
    assert result.conclusion is None
    assert result.evidence_count == 0
    assert result.error


def test_runner_without_conclusion_goes_to_waiting_for_input(sample_gateway):
    service, _ = build_service(no_conclusion_llm(), sample_gateway)
    diagnosis_id = create_black_screen_case(service)

    result = service.run_diagnosis(diagnosis_id)

    assert result.ok is False
    assert result.status is SecurityDiagnosisStatus.WAITING_FOR_INPUT


def test_no_evidence_goes_to_inconclusive(sample_gateway):
    service, _ = build_service(no_evidence_llm(), sample_gateway)
    diagnosis_id = create_black_screen_case(service)

    result = service.run_diagnosis(diagnosis_id)

    assert result.ok is False
    assert result.status is SecurityDiagnosisStatus.INCONCLUSIVE
    assert result.conclusion is None
    assert "没有任何可用 Evidence" in (result.error or "")


def test_review_confirm_produces_confirmed(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)

    result = confirm(app_service, diagnosis_id)

    assert result.status is SecurityDiagnosisStatus.CONFIRMED
    assert result.action is HumanReviewAction.CONFIRM
    assert app_service.get_diagnosis(diagnosis_id).status is SecurityDiagnosisStatus.CONFIRMED


def test_review_reject_produces_rejected(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)

    result = app_service.review_diagnosis(
        diagnosis_id=diagnosis_id,
        action=HumanReviewAction.REJECT,
        reviewer="ops-li",
        comment="现场复核不一致",
    )

    assert result.status is SecurityDiagnosisStatus.REJECTED


def test_review_request_more_info_produces_waiting_for_input(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)

    result = app_service.review_diagnosis(
        diagnosis_id=diagnosis_id,
        action=HumanReviewAction.REQUEST_MORE_INFO,
        reviewer="ops-li",
        comment="补充现场照片",
    )

    assert result.status is SecurityDiagnosisStatus.WAITING_FOR_INPUT


def test_review_unknown_diagnosis_fails(app_service):
    with pytest.raises(DiagnosisNotFoundError):
        confirm(app_service, "diag_not_exist")


def test_review_before_run_is_rejected(app_service):
    diagnosis_id = create_black_screen_case(app_service)

    with pytest.raises(ReviewNotAllowed):
        confirm(app_service, diagnosis_id)


def test_confirmed_case_cannot_be_reviewed_again(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)
    confirm(app_service, diagnosis_id)

    with pytest.raises(ReviewNotAllowed):
        app_service.review_diagnosis(
            diagnosis_id=diagnosis_id,
            action=HumanReviewAction.REJECT,
            reviewer="ops-li",
        )

    assert app_service.get_diagnosis(diagnosis_id).status is SecurityDiagnosisStatus.CONFIRMED


def test_run_on_confirmed_case_is_rejected(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)
    confirm(app_service, diagnosis_id)

    with pytest.raises(InvalidStatusTransition):
        app_service.run_diagnosis(diagnosis_id)


def test_list_diagnoses_returns_all(app_service):
    create_black_screen_case(app_service)
    create_black_screen_case(app_service)

    assert len(app_service.list_diagnoses()) == 2


def test_render_report_returns_markdown(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)
    confirm(app_service, diagnosis_id)

    report = app_service.render_report(diagnosis_id)

    assert report.startswith("# 安防设备诊断报告")
    assert diagnosis_id in report
    assert "confirmed" in report
