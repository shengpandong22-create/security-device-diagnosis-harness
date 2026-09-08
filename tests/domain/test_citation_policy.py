"""CitationPolicy 验收。"""

from __future__ import annotations

import pydantic
import pytest

from security_diagnosis_harness.domain.citation_policy import (
    DEVICE_FACT_EVIDENCE_TYPES,
    CitationPolicy,
)
from security_diagnosis_harness.domain.conclusion import (
    ConclusionConfidence,
    DiagnosisConclusion,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.errors import CitationPolicyViolation
from security_diagnosis_harness.domain.evidence import (
    EvidenceSource,
    EvidenceType,
    Reliability,
)

from ..conftest import make_case, make_evidence

DIAG_A = "diag_a"
DIAG_B = "diag_b"


def _add(case, evidence_type: EvidenceType):
    return case.add_evidence(
        make_evidence(
            case.diagnosis_id,
            summary=f"{evidence_type.value} 证据",
            evidence_type=evidence_type,
        )
    )


def _conclusion(diagnosis_id: str, evidence_ids: list[str], confidence: str) -> DiagnosisConclusion:
    return DiagnosisConclusion(
        diagnosis_id=diagnosis_id,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        summary="摄像头黑屏根因候选",
        confidence=confidence,
        cited_evidence_ids=evidence_ids,
    )


def test_policy_accepts_probable_citing_device_fact():
    case = make_case(DIAG_A)
    evidence = _add(case, EvidenceType.DEVICE_STATUS)
    policy = CitationPolicy()

    policy.validate(_conclusion(DIAG_A, [evidence.evidence_id], "probable"), case)


def test_policy_accepts_probable_citing_alarm_or_config():
    for evidence_type in (EvidenceType.DEVICE_ALARM, EvidenceType.DEVICE_CONFIG):
        case = make_case(DIAG_A)
        evidence = _add(case, evidence_type)
        CitationPolicy().validate(
            _conclusion(DIAG_A, [evidence.evidence_id], "probable"), case
        )


def test_policy_rejects_probable_citing_only_knowledge():
    case = make_case(DIAG_A)
    evidence = _add(case, EvidenceType.KNOWLEDGE_SOP)

    with pytest.raises(CitationPolicyViolation, match="probable"):
        CitationPolicy().validate(
            _conclusion(DIAG_A, [evidence.evidence_id], "probable"), case
        )


def test_policy_accepts_possible_citing_only_knowledge():
    case = make_case(DIAG_A)
    evidence = _add(case, EvidenceType.KNOWLEDGE_SOP)

    CitationPolicy().validate(_conclusion(DIAG_A, [evidence.evidence_id], "possible"), case)


def test_policy_rejects_citation_from_other_diagnosis():
    case_a = make_case(DIAG_A)
    case_b = make_case(DIAG_B)
    foreign_evidence = _add(case_b, EvidenceType.DEVICE_STATUS)

    with pytest.raises(CitationPolicyViolation, match="不属于诊断"):
        CitationPolicy().validate(
            _conclusion(DIAG_A, [foreign_evidence.evidence_id], "probable"), case_a
        )


def test_policy_rejects_conclusion_from_other_diagnosis():
    case_a = make_case(DIAG_A)
    case_b = make_case(DIAG_B)
    own_evidence = _add(case_b, EvidenceType.DEVICE_STATUS)

    with pytest.raises(CitationPolicyViolation, match="不能用于诊断"):
        CitationPolicy().validate(
            _conclusion(DIAG_B, [own_evidence.evidence_id], "possible"), case_a
        )


def test_policy_rejects_unknown_evidence_id():
    case = make_case(DIAG_A)

    with pytest.raises(CitationPolicyViolation):
        CitationPolicy().validate(_conclusion(DIAG_A, ["evd_missing"], "possible"), case)


def test_confirmed_is_not_a_valid_confidence():
    assert not hasattr(ConclusionConfidence, "CONFIRMED")
    assert "confirmed" not in {item.value for item in ConclusionConfidence}

    with pytest.raises(pydantic.ValidationError):
        _conclusion(DIAG_A, [], "confirmed")


def test_policy_rejects_confirmed_like_confidence():
    case = make_case(DIAG_A)
    conclusion = _conclusion(DIAG_A, [], "possible")
    object.__setattr__(conclusion, "confidence", "confirmed")

    with pytest.raises(CitationPolicyViolation, match="confirmed"):
        CitationPolicy().validate(conclusion, case)


def test_device_fact_evidence_types_exclude_knowledge():
    expected = frozenset(
        {
            EvidenceType.DEVICE_STATUS,
            EvidenceType.DEVICE_ALARM,
            EvidenceType.DEVICE_CONFIG,
        }
    )

    assert expected == DEVICE_FACT_EVIDENCE_TYPES
    assert EvidenceType.KNOWLEDGE_SOP not in DEVICE_FACT_EVIDENCE_TYPES


def test_policy_rejects_possible_without_citation():
    """可信诊断不允许零引用结论，任何候选结论都必须至少引用一条 Evidence。"""
    case = make_case(DIAG_A)

    with pytest.raises(CitationPolicyViolation, match="至少引用一条 Evidence"):
        CitationPolicy().validate(_conclusion(DIAG_A, [], "possible"), case)


def test_policy_rejects_probable_without_citation():
    case = make_case(DIAG_A)

    with pytest.raises(CitationPolicyViolation, match="至少引用一条 Evidence"):
        CitationPolicy().validate(_conclusion(DIAG_A, [], "probable"), case)


def test_policy_accepts_possible_after_citation_added():
    """同一条结论补上引用后即可通过校验。"""
    case = make_case(DIAG_A)
    evidence = _add(case, EvidenceType.KNOWLEDGE_SOP)
    conclusion = _conclusion(DIAG_A, [], "possible")

    with pytest.raises(CitationPolicyViolation):
        CitationPolicy().validate(conclusion, case)

    conclusion.cited_evidence_ids.append(evidence.evidence_id)

    CitationPolicy().validate(conclusion, case)


def test_policy_still_checks_reliability_of_evidence_source():
    case = make_case(DIAG_A)
    evidence = case.add_evidence(
        make_evidence(
            DIAG_A,
            summary="设备状态证据",
            evidence_type=EvidenceType.DEVICE_STATUS,
        )
    )

    assert evidence.source is EvidenceSource.DEVICE_GATEWAY
    assert evidence.reliability is Reliability.HIGH
    CitationPolicy().validate(_conclusion(DIAG_A, [evidence.evidence_id], "probable"), case)
