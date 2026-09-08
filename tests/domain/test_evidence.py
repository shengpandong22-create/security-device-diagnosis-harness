"""Evidence 归属与去重验收。"""

from __future__ import annotations

import pydantic
import pytest

from security_diagnosis_harness.domain.errors import (
    EvidenceDiagnosisMismatch,
    UnknownEvidenceReference,
)
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
    Reliability,
)

from ..conftest import make_case, make_conclusion, make_evidence


def test_evidence_requires_diagnosis_id():
    with pytest.raises(pydantic.ValidationError):
        DiagnosisEvidence(
            diagnosis_id="",
            evidence_type=EvidenceType.DEVICE_STATUS,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary="设备在线",
        )


def test_evidence_has_type_source_hash_reliability_and_redaction():
    evidence = make_evidence("diag_a")

    assert evidence.evidence_type is EvidenceType.DEVICE_STATUS
    assert evidence.source is EvidenceSource.DEVICE_GATEWAY
    assert evidence.reliability is Reliability.HIGH
    assert evidence.redacted is False
    assert len(evidence.content_hash) == 64


def test_evidence_must_belong_to_the_diagnosis():
    case = make_case("diag_a")
    foreign = make_evidence("diag_b")

    with pytest.raises(EvidenceDiagnosisMismatch):
        case.add_evidence(foreign)

    assert case.evidence == []


def test_add_evidence_dedupes_same_content():
    case = make_case("diag_a")
    first = case.add_evidence(make_evidence("diag_a"))
    second = case.add_evidence(make_evidence("diag_a"))

    assert len(case.evidence) == 1
    assert first.content_hash == second.content_hash


def test_add_evidence_returns_existing_evidence_when_content_hash_is_duplicate():
    case = make_case("diag_a")
    first_evidence = make_evidence("diag_a")
    second_evidence = make_evidence("diag_a")

    saved_first = case.add_evidence(first_evidence)
    saved_second = case.add_evidence(second_evidence)

    assert saved_first.content_hash == saved_second.content_hash
    assert len(case.evidence) == 1
    assert saved_second.evidence_id == saved_first.evidence_id
    assert saved_second is case.evidence[0]
    assert saved_second.evidence_id == case.evidence[0].evidence_id


def test_duplicate_evidence_return_value_can_be_cited_by_conclusion():
    case = make_case("diag_a")
    saved_first = case.add_evidence(make_evidence("diag_a"))
    saved_second = case.add_evidence(make_evidence("diag_a"))

    case.set_conclusion(make_conclusion("diag_a", [saved_second.evidence_id]))

    assert case.conclusion is not None
    assert case.conclusion.cited_evidence_ids == [saved_first.evidence_id]


def test_add_evidence_keeps_different_content():
    case = make_case("diag_a")
    case.add_evidence(make_evidence("diag_a", summary="设备在线但主码流异常"))
    case.add_evidence(
        make_evidence(
            "diag_a",
            summary="存在 STREAM_PUBLISH_FAILED 告警",
            evidence_type=EvidenceType.DEVICE_ALARM,
        )
    )

    assert len(case.evidence) == 2


def test_conclusion_cannot_cite_foreign_evidence():
    case = make_case("diag_a")
    evidence = case.add_evidence(make_evidence("diag_a"))

    with pytest.raises(UnknownEvidenceReference):
        case.set_conclusion(make_conclusion("diag_a", ["evd_not_in_this_case"]))

    case.set_conclusion(make_conclusion("diag_a", [evidence.evidence_id]))
    assert case.conclusion is not None
    assert case.conclusion.cited_evidence_ids == [evidence.evidence_id]
