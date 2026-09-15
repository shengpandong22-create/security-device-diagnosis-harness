"""统一 Evidence 归并协议的确定性与冲突测试。"""

from datetime import UTC, datetime, timedelta

from security_diagnosis_harness.application.evidence_resolution import resolve_evidence
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
    Reliability,
)

NOW = datetime(2026, 9, 15, 12, tzinfo=UTC)


def _evidence(
    value: bool,
    *,
    captured_at: datetime = NOW,
    reliability: Reliability = Reliability.MEDIUM,
) -> DiagnosisEvidence:
    return DiagnosisEvidence(
        diagnosis_id="diag-resolution",
        evidence_type=EvidenceType.DEVICE_STATUS,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary="设备状态",
        payload={"online": value},
        captured_at=captured_at,
        reliability=reliability,
    )


def test_resolution_is_independent_of_input_order() -> None:
    older = _evidence(False, captured_at=NOW - timedelta(minutes=1))
    newer = _evidence(True)

    forward = resolve_evidence([older, newer], {EvidenceType.DEVICE_STATUS})
    reverse = resolve_evidence([newer, older], {EvidenceType.DEVICE_STATUS})

    assert forward.selected == reverse.selected
    assert forward.selected[EvidenceType.DEVICE_STATUS] is newer


def test_higher_reliability_wins_over_newer_low_reliability() -> None:
    trusted = _evidence(False, captured_at=NOW, reliability=Reliability.HIGH)
    newer_low = _evidence(
        True,
        captured_at=NOW + timedelta(minutes=1),
        reliability=Reliability.LOW,
    )

    result = resolve_evidence([newer_low, trusted], {EvidenceType.DEVICE_STATUS})

    assert result.selected[EvidenceType.DEVICE_STATUS] is trusted
    assert result.conflicts == {}


def test_simultaneous_conflicting_payloads_are_not_arbitrarily_selected() -> None:
    offline = _evidence(False)
    online = _evidence(True)

    result = resolve_evidence([online, offline], {EvidenceType.DEVICE_STATUS})

    assert EvidenceType.DEVICE_STATUS not in result.selected
    assert {item.evidence_id for item in result.conflicts[EvidenceType.DEVICE_STATUS]} == {
        offline.evidence_id,
        online.evidence_id,
    }


def test_identical_simultaneous_payloads_are_deduplicated_deterministically() -> None:
    first = _evidence(True)
    second = _evidence(True)

    forward = resolve_evidence([first, second], {EvidenceType.DEVICE_STATUS})
    reverse = resolve_evidence([second, first], {EvidenceType.DEVICE_STATUS})

    assert forward.conflicts == reverse.conflicts == {}
    assert forward.selected[EvidenceType.DEVICE_STATUS].evidence_id == reverse.selected[
        EvidenceType.DEVICE_STATUS
    ].evidence_id
