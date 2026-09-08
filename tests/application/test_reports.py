"""Markdown 报告验收。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.application.reports import redact_payload, render_markdown_report
from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
)
from security_diagnosis_harness.domain.review import HumanReviewAction

from .conftest import confirm, create_black_screen_case


def _raw_credential_evidence(diagnosis_id: str) -> DiagnosisEvidence:
    """一条包含未脱敏凭证字段的证据，用于验证报告输出前会再次脱敏。"""
    return DiagnosisEvidence(
        diagnosis_id=diagnosis_id,
        evidence_type=EvidenceType.DEVICE_CONFIG,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary="含敏感字段的配置证据",
        payload={
            "admin_password": "super-secret-password",
            "nested": {"api_token": "0123456789abcdef-token"},
            "bitrate_mode": "CBR",
        },
    )


def test_report_contains_basic_fields(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)
    report = render_markdown_report(app_service.get_diagnosis(diagnosis_id))

    assert report.startswith("# 安防设备诊断报告")
    assert diagnosis_id in report
    assert "camera-3f-001" in report
    assert SecurityFaultType.CAMERA_BLACK_SCREEN.value in report
    assert "摄像头黑屏" in report


def test_report_contains_evidence(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)
    case = app_service.get_diagnosis(diagnosis_id)

    report = render_markdown_report(case)

    assert f"共 {len(case.evidence)} 条 Evidence。" in report
    for evidence in case.evidence:
        assert evidence.evidence_id in report
        assert evidence.evidence_type.value in report


def test_report_contains_cited_evidence_ids(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)
    case = app_service.get_diagnosis(diagnosis_id)

    report = render_markdown_report(case)

    assert "cited_evidence_ids" in report
    for evidence_id in case.conclusion.cited_evidence_ids:
        assert evidence_id in report


def test_report_contains_conclusion_and_next_steps(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)
    case = app_service.get_diagnosis(diagnosis_id)

    report = render_markdown_report(case)

    assert case.conclusion.summary in report
    assert case.conclusion.confidence.value in report
    for step in case.conclusion.next_steps:
        assert step in report


def test_report_contains_human_review(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)
    confirm(app_service, diagnosis_id, reviewer="ops-li", comment="现场核实，同意结论")

    report = render_markdown_report(app_service.get_diagnosis(diagnosis_id))

    assert HumanReviewAction.CONFIRM.value in report
    assert "ops-li" in report
    assert "现场核实，同意结论" in report
    assert "已由人工确认" in report


def test_report_marks_rejected_state(app_service):
    diagnosis_id = create_black_screen_case(app_service)
    app_service.run_diagnosis(diagnosis_id)
    app_service.review_diagnosis(
        diagnosis_id=diagnosis_id,
        action=HumanReviewAction.REJECT,
        reviewer="ops-li",
        comment="不一致",
    )

    report = render_markdown_report(app_service.get_diagnosis(diagnosis_id))

    assert "已被人工驳回" in report


def test_report_without_conclusion_or_review(app_service):
    diagnosis_id = create_black_screen_case(app_service)

    report = render_markdown_report(app_service.get_diagnosis(diagnosis_id))

    assert "本次诊断暂无候选结论。" in report
    assert "（暂无人工审核）" in report


def test_report_does_not_leak_credentials(app_service):
    """报告不能出现未脱敏的密码/Token。"""
    diagnosis_id = create_black_screen_case(app_service)
    case = app_service.get_diagnosis(diagnosis_id)
    case.evidence.append(_raw_credential_evidence(case.diagnosis_id))
    app_service._repository.update(case)  # noqa: SLF001 - 测试内直接准备含敏数据的 Case

    report = render_markdown_report(app_service.get_diagnosis(diagnosis_id))

    assert "super-secret-password" not in report
    assert "0123456789abcdef-token" not in report
    assert REDACTED_VALUE in report


@pytest.mark.parametrize(
    ("payload", "forbidden"),
    [
        ({"password": "p@ss"}, "p@ss"),
        ({"token": "t0ken"}, "t0ken"),
        ({"nested": {"access_key": "ak-1"}}, "ak-1"),
        ({"items": [{"client_secret": "cs-1"}]}, "cs-1"),
    ],
)
def test_redact_payload_masks_sensitive_values(payload, forbidden):
    redacted = redact_payload(payload)

    assert forbidden not in str(redacted)
    assert REDACTED_VALUE in str(redacted)


def test_redact_payload_keeps_normal_values():
    assert redact_payload({"bitrate_mode": "CBR", "enabled": True}) == {
        "bitrate_mode": "CBR",
        "enabled": True,
    }
