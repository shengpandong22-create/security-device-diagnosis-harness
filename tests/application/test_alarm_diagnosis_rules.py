"""报警误报候选根因规则验收。

规则只输出候选根因，不产出 confirmed，也不绕过 CitationPolicy。
测试直接基于 SecurityDiagnosisCase.evidence 的 payload 推断，不读取样例 JSON。
"""

from __future__ import annotations

from security_diagnosis_harness.application.alarm_diagnosis_rules import (
    AlarmDiagnosisLabel,
    extract_alarm_facts,
    infer_alarm_false_positive_label,
)
from security_diagnosis_harness.application.reports import render_markdown_report
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
    Reliability,
)

DIAG_ID = "diag-alarm-1"


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


def _rule(
    *,
    sensitivity: str = "medium",
    threshold: int = 60,
    debounce_seconds: int = 8,
    is_over_sensitive: bool = False,
) -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.ALARM_RULE,
        {
            "device_id": "alarm-1",
            "rule_id": "rule-1",
            "alarm_type": "motion",
            "enabled": True,
            "severity": "warning",
            "sensitivity": sensitivity,
            "threshold": threshold,
            "debounce_seconds": debounce_seconds,
            "is_over_sensitive": is_over_sensitive,
        },
    )


def _signal(status: str = "stable", noise_level: float = 0.2) -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.ALARM_SIGNAL,
        {
            "device_id": "alarm-1",
            "alarm_id": "alarm-1",
            "sensor_id": "sensor-1",
            "signal_value": 70,
            "threshold": 60,
            "noise_level": noise_level,
            "status": status,
            "is_noisy": status == "noisy" or noise_level >= 0.7,
        },
    )


def _environment(*interference_types: str) -> DiagnosisEvidence:
    interferences = list(interference_types) or ["none"]
    return _evidence(
        EvidenceType.ALARM_ENVIRONMENT,
        {
            "device_id": "alarm-1",
            "alarm_id": "alarm-1",
            "interference_types": interferences,
            "visibility": "low" if interferences != ["none"] else "normal",
            "has_interference": interferences != ["none"],
        },
    )


def _verification(result: str = "inconclusive") -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.ALARM_VERIFICATION,
        {
            "device_id": "alarm-1",
            "alarm_id": "alarm-1",
            "result": result,
            "target_count": 0,
            "checked_by": "system",
            "indicates_false_alarm": result == "no_target_found",
        },
    )


def _correlation(
    pattern: str = "isolated",
    repeated_count: int = 1,
    neighbor_alarm_count: int = 0,
) -> DiagnosisEvidence:
    return _evidence(
        EvidenceType.ALARM_CORRELATION,
        {
            "device_id": "alarm-1",
            "alarm_id": "alarm-1",
            "pattern": pattern,
            "repeated_count": repeated_count,
            "neighbor_alarm_count": neighbor_alarm_count,
            "is_burst": pattern == "burst" or repeated_count >= 5,
            "has_neighbor_correlation": neighbor_alarm_count > 0,
        },
    )


def _case(*evidence: DiagnosisEvidence) -> SecurityDiagnosisCase:
    return SecurityDiagnosisCase(
        diagnosis_id=DIAG_ID,
        fault_type=SecurityFaultType.ALARM_FALSE_POSITIVE,
        device_id="alarm-1",
        reporter="tester",
        evidence=list(evidence),
    )


def test_alarm_rule_too_sensitive_detected():
    case = _case(
        _rule(sensitivity="high", threshold=20, debounce_seconds=1, is_over_sensitive=True),
        _signal(),
        _environment(),
        _verification(),
        _correlation(),
    )

    result = infer_alarm_false_positive_label(case)

    assert result.label is AlarmDiagnosisLabel.ALARM_RULE_TOO_SENSITIVE


def test_environment_interference_detected():
    case = _case(_rule(), _signal(), _environment("rain", "strong_light"), _verification())

    result = infer_alarm_false_positive_label(case)

    assert result.label is AlarmDiagnosisLabel.ENVIRONMENT_INTERFERENCE


def test_sensor_noise_or_stuck_detected():
    case = _case(_rule(), _signal(status="noisy", noise_level=0.92), _environment())

    result = infer_alarm_false_positive_label(case)

    assert result.label is AlarmDiagnosisLabel.SENSOR_NOISE_OR_STUCK


def test_verification_negative_false_alarm_detected():
    case = _case(_rule(), _signal(), _environment(), _verification("no_target_found"))

    result = infer_alarm_false_positive_label(case)

    assert result.label is AlarmDiagnosisLabel.VERIFICATION_NEGATIVE_FALSE_ALARM


def test_duplicate_alarm_burst_detected():
    case = _case(
        _rule(),
        _signal(),
        _environment(),
        _verification(),
        _correlation(pattern="burst", repeated_count=12),
    )

    result = infer_alarm_false_positive_label(case)

    assert result.label is AlarmDiagnosisLabel.DUPLICATE_ALARM_BURST


def test_insufficient_when_rule_or_signal_missing():
    case = _case(_environment(), _verification(), _correlation())

    result = infer_alarm_false_positive_label(case)

    assert result.label is AlarmDiagnosisLabel.INSUFFICIENT_ALARM_EVIDENCE
    assert "alarm_rule" in result.evidence_chain[0]
    assert "alarm_signal" in result.evidence_chain[0]


def test_inconclusive_zero_target_is_not_enough_for_verification_negative():
    case = _case(_rule(), _signal(), _environment(), _verification(), _correlation())

    result = infer_alarm_false_positive_label(case)

    assert result.label is AlarmDiagnosisLabel.INSUFFICIENT_ALARM_EVIDENCE


def test_extract_alarm_facts_reads_payload():
    case = _case(
        _rule(is_over_sensitive=True),
        _signal(status="stuck"),
        _environment("fog"),
        _verification("no_target_found"),
        _correlation(pattern="multi_device", neighbor_alarm_count=3),
    )

    facts = extract_alarm_facts(case)

    assert facts.rule_evidence is not None
    assert facts.signal_evidence is not None
    assert facts.environment_evidence is not None
    assert facts.verification_evidence is not None
    assert facts.correlation_evidence is not None
    assert facts.rule_over_sensitive is True
    assert facts.signal_noisy is True
    assert facts.environment_has_interference is True
    assert facts.verification_negative is True
    assert facts.correlation_burst is True


def test_alarm_rules_never_emit_confirmed_label():
    for label in AlarmDiagnosisLabel:
        assert "confirmed" not in label.value

    case = _case(_rule(is_over_sensitive=True), _signal())
    result = infer_alarm_false_positive_label(case)
    assert result.label in set(AlarmDiagnosisLabel)


def test_evidence_chain_contains_key_evidence_ids():
    rule = _rule(is_over_sensitive=True)
    signal = _signal()
    environment = _environment()
    verification = _verification()
    correlation = _correlation()
    case = _case(rule, signal, environment, verification, correlation)

    result = infer_alarm_false_positive_label(case)

    assert rule.evidence_id in result.evidence_chain
    assert signal.evidence_id in result.evidence_chain
    assert environment.evidence_id in result.evidence_chain
    assert verification.evidence_id in result.evidence_chain
    assert correlation.evidence_id in result.evidence_chain


def test_troubleshooting_order_and_excluded_candidates_nonempty():
    case = _case(_rule(is_over_sensitive=True), _signal())

    result = infer_alarm_false_positive_label(case)

    assert result.troubleshooting_order
    assert result.excluded_candidates
    assert all("alarm_rule_too_sensitive" not in item for item in result.excluded_candidates)


def test_report_contains_alarm_candidate_and_summaries():
    case = _case(
        _rule(is_over_sensitive=True),
        _signal(),
        _environment(),
        _verification(),
        _correlation(),
    )

    markdown = render_markdown_report(case)

    assert "报警诊断（候选）" in markdown
    assert "alarm_rule_too_sensitive" in markdown
    assert "报警规则摘要" in markdown
    assert "触发信号摘要" in markdown
    assert "环境干扰摘要" in markdown
    assert "复核结果摘要" in markdown
    assert "关联告警摘要" in markdown


def test_report_redacts_alarm_sensitive_payload_values():
    verification = _verification("no_target_found")
    verification.payload = {
        "result": "no_target_found",
        "target_count": 0,
        "extra": {
            "snapshot_url": "http://example.local/snapshot/not-real.jpg",
            "license_plate": "浙A12345",
        },
    }
    case = _case(_rule(), _signal(), _environment(), verification)

    markdown = render_markdown_report(case)

    assert "http://example.local/snapshot/not-real.jpg" not in markdown
    assert "浙A12345" not in markdown
    assert REDACTED_VALUE in markdown
