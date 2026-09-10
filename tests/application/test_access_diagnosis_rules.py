"""门禁刷卡异常候选根因规则验收。

规则只输出候选根因，不产出 confirmed，也不绕过 CitationPolicy。
测试直接基于 SecurityDiagnosisCase.evidence 的 payload 推断，不读取样例 JSON。
"""

from __future__ import annotations

from security_diagnosis_harness.application.access_diagnosis_rules import (
    AccessDiagnosisLabel,
    extract_access_facts,
    infer_access_card_failed_label,
)
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
    Reliability,
)

DIAG_ID = "diag-access-1"


def _evidence(evidence_type: EvidenceType, payload: dict) -> DiagnosisEvidence:
    return DiagnosisEvidence(
        diagnosis_id=DIAG_ID,
        evidence_type=evidence_type,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary=f"{evidence_type.value} 证据",
        payload=payload,
        reliability=Reliability.HIGH,
        redacted=True,
    )


def _controller(status: str = "online", health: str = "healthy") -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.ACCESS_CONTROLLER,
        {
            "device_id": "access-1",
            "controller_id": "ctrl-1",
            "status": status,
            "health": health,
            "last_error": "" if health == "healthy" else "controller error",
        },
    )


def _door(door_status: str = "closed", lock_status: str = "locked") -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.ACCESS_DOOR,
        {
            "device_id": "access-1",
            "door_id": "door-1",
            "door_status": door_status,
            "lock_status": lock_status,
            "has_lock_error": lock_status == "jammed",
            "last_error": "" if lock_status != "jammed" else "lock motor blocked",
        },
    )


def _credential(status: str = "active") -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.ACCESS_CREDENTIAL,
        {
            "device_id": "access-1",
            "credential_id": "***REDACTED***",
            "credential_type": "card",
            "status": status,
            "is_valid": status == "active",
        },
    )


def _policy(allowed: bool = True) -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.ACCESS_POLICY,
        {
            "device_id": "access-1",
            "door_id": "door-1",
            "person_id": "***REDACTED***",
            "credential_id": "***REDACTED***",
            "allowed": allowed,
            "time_ranges": [
                {"start": "00:00:00", "end": "00:00:00", "weekdays": [1, 2, 3, 4, 5, 6, 7]}
            ],
        },
    )


def _event(
    decision: str = "denied",
    deny_reason: str = "permission_denied",
) -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.ACCESS_EVENT,
        {
            "events": [
                {
                    "device_id": "access-1",
                    "door_id": "door-1",
                    "credential_id": "***REDACTED***",
                    "person_id": "***REDACTED***",
                    "credential_type": "card",
                    "decision": decision,
                    "deny_reason": deny_reason,
                    "occurred_at": "2026-09-10T08:10:00+08:00",
                }
            ]
        },
    )


def _case(*evidence: DiagnosisEvidence) -> SecurityDiagnosisCase:
    return SecurityDiagnosisCase(
        diagnosis_id=DIAG_ID,
        fault_type=SecurityFaultType.ACCESS_CARD_FAILED,
        device_id="access-1",
        reporter="tester",
        evidence=list(evidence),
    )


def test_credential_invalid_or_frozen_detected():
    case = _case(
        _controller(),
        _door(),
        _credential(status="frozen"),
        _policy(),
        _event(deny_reason="frozen_credential"),
    )

    result = infer_access_card_failed_label(case)

    assert result.label is AccessDiagnosisLabel.CREDENTIAL_INVALID_OR_FROZEN


def test_permission_not_granted_detected():
    case = _case(_controller(), _door(), _credential(), _policy(False), _event())

    result = infer_access_card_failed_label(case)

    assert result.label is AccessDiagnosisLabel.PERMISSION_NOT_GRANTED


def test_access_time_window_denied_detected():
    case = _case(
        _controller(),
        _door(),
        _credential(),
        _policy(),
        _event(deny_reason="time_window_denied"),
    )

    result = infer_access_card_failed_label(case)

    assert result.label is AccessDiagnosisLabel.ACCESS_TIME_WINDOW_DENIED


def test_controller_offline_or_no_response_detected():
    case = _case(
        _controller(status="offline", health="error"),
        _door(),
        _credential(),
        _policy(),
        _event(decision="timeout", deny_reason="controller_timeout"),
    )

    result = infer_access_card_failed_label(case)

    assert result.label is AccessDiagnosisLabel.CONTROLLER_OFFLINE_OR_NO_RESPONSE


def test_door_lock_or_sensor_issue_detected():
    case = _case(
        _controller(health="degraded"),
        _door(lock_status="jammed"),
        _credential(),
        _policy(),
        _event(deny_reason="door_lock_error"),
    )

    result = infer_access_card_failed_label(case)

    assert result.label is AccessDiagnosisLabel.DOOR_LOCK_OR_SENSOR_ISSUE


def test_insufficient_when_access_event_missing():
    case = _case(_controller(), _door(), _credential(), _policy())

    result = infer_access_card_failed_label(case)

    assert result.label is AccessDiagnosisLabel.INSUFFICIENT_ACCESS_EVIDENCE
    assert "access_event" in result.evidence_chain[0]


def test_extract_access_facts_reads_payload():
    case = _case(
        _controller(status="offline", health="error"),
        _door(lock_status="jammed"),
        _credential(status="expired"),
        _policy(False),
        _event(decision="timeout", deny_reason="controller_timeout"),
    )

    facts = extract_access_facts(case)

    assert facts.controller_evidence is not None
    assert facts.door_evidence is not None
    assert facts.credential_evidence is not None
    assert facts.policy_evidence is not None
    assert facts.event_evidence is not None
    assert facts.controller_abnormal is True
    assert facts.door_lock_issue is True
    assert facts.credential_abnormal is True
    assert facts.permission_denied is True


def test_access_rules_never_emit_confirmed_label():
    for label in AccessDiagnosisLabel:
        assert "confirmed" not in label.value

    case = _case(_controller(), _door(), _credential(status="frozen"), _policy(), _event())
    result = infer_access_card_failed_label(case)
    assert result.label in set(AccessDiagnosisLabel)


def test_evidence_chain_contains_key_evidence_ids():
    controller = _controller()
    door = _door()
    credential = _credential(status="frozen")
    policy = _policy()
    event = _event(deny_reason="frozen_credential")
    case = _case(controller, door, credential, policy, event)

    result = infer_access_card_failed_label(case)

    assert controller.evidence_id in result.evidence_chain
    assert door.evidence_id in result.evidence_chain
    assert credential.evidence_id in result.evidence_chain
    assert policy.evidence_id in result.evidence_chain
    assert event.evidence_id in result.evidence_chain


def test_troubleshooting_order_and_excluded_candidates_nonempty():
    case = _case(_controller(), _door(), _credential(status="frozen"), _policy(), _event())

    result = infer_access_card_failed_label(case)

    assert result.troubleshooting_order
    assert result.excluded_candidates
    assert all("credential_invalid_or_frozen" not in item for item in result.excluded_candidates)


def test_report_contains_access_candidate_and_summaries():
    from security_diagnosis_harness.application.reports import render_markdown_report

    case = _case(
        _controller(),
        _door(),
        _credential(status="frozen"),
        _policy(),
        _event(deny_reason="frozen_credential"),
    )

    markdown = render_markdown_report(case)

    assert "门禁诊断（候选）" in markdown
    assert "credential_invalid_or_frozen" in markdown
    assert "门禁控制器摘要" in markdown
    assert "门 / 锁摘要" in markdown
    assert "凭证摘要" in markdown
    assert "授权策略摘要" in markdown
    assert "刷卡事件摘要" in markdown


def test_report_redacts_access_sensitive_payload_values():
    from security_diagnosis_harness.application.reports import render_markdown_report
    from security_diagnosis_harness.domain.device import REDACTED_VALUE

    sensitive = _event(deny_reason="frozen_credential")
    sensitive.payload = {
        "events": [
            {
                "credential_id": "plain-card-no",
                "person_id": "plain-person-id",
                "phone": "13800000000",
                "decision": "denied",
                "deny_reason": "frozen_credential",
            }
        ]
    }
    case = _case(_controller(), _door(), _credential(status="frozen"), _policy(), sensitive)

    markdown = render_markdown_report(case)

    assert "plain-card-no" not in markdown
    assert "plain-person-id" not in markdown
    assert "13800000000" not in markdown
    assert REDACTED_VALUE in markdown
