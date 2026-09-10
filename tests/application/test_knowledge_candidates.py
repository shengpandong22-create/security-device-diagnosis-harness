"""Phase 5B：confirmed 诊断提炼知识候选。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from security_diagnosis_harness.application.errors import (
    KnowledgeCandidateGenerationError,
)
from security_diagnosis_harness.application.knowledge_candidates import (
    KnowledgeCandidateApplicationService,
)
from security_diagnosis_harness.bootstrap.container import (
    build_phase1_container,
    build_phase2_container,
    build_phase3_container,
    build_phase4_container,
)
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.conclusion import DiagnosisConclusion
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
)
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidateSource,
    KnowledgeCandidateStatus,
)
from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction


class _DiagnosisServiceStub:
    def __init__(self, case: SecurityDiagnosisCase) -> None:
        self.case = case

    def get_diagnosis(self, diagnosis_id: str) -> SecurityDiagnosisCase:
        assert diagnosis_id == self.case.diagnosis_id
        return self.case

    def infer_candidate_label(self, case: SecurityDiagnosisCase):
        assert case is self.case
        return SimpleNamespace(
            label=f"{case.fault_type.value}_candidate",
            explanation="规则归纳的候选根因",
            troubleshooting_order=["检查设备事实", "完成现场复核"],
            excluded_candidates=["已排除的次要原因"],
        )


def _case(
    fault_type: SecurityFaultType = SecurityFaultType.CAMERA_BLACK_SCREEN,
    *,
    confirm: bool = True,
) -> SecurityDiagnosisCase:
    case = SecurityDiagnosisCase(
        fault_type=fault_type,
        device_id="device-1",
        reporter="operator",
        description="现场出现异常，password=plain-secret",
    )
    evidence = case.add_evidence(
        DiagnosisEvidence(
            diagnosis_id=case.diagnosis_id,
            evidence_type=EvidenceType.DEVICE_STATUS,
            source=EvidenceSource.DEVICE_GATEWAY,
            summary="设备事实摘要 Bearer abc.def.ghi",
            payload={"password": "must-not-be-copied", "large": [1, 2, 3]},
        )
    )
    case.transition_to(SecurityDiagnosisStatus.INVESTIGATING)
    case.set_conclusion(
        DiagnosisConclusion(
            diagnosis_id=case.diagnosis_id,
            fault_type=fault_type,
            summary="已定位问题，token=secret-value",
            root_cause=None,
            cited_evidence_ids=[evidence.evidence_id],
            next_steps=["检查设备事实"],
        )
    )
    case.transition_to(SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION)
    if confirm:
        case.apply_human_review(
            HumanReview(
                diagnosis_id=case.diagnosis_id,
                action=HumanReviewAction.CONFIRM,
                reviewer="expert",
                comment="现场确认",
            )
        )
    return case


def _generator(case: SecurityDiagnosisCase) -> KnowledgeCandidateApplicationService:
    return KnowledgeCandidateApplicationService(_DiagnosisServiceStub(case))


@pytest.mark.parametrize("fault_type", list(SecurityFaultType))
def test_all_four_confirmed_diagnosis_domains_generate_candidates(fault_type):
    case = _case(fault_type)

    candidate = _generator(case).generate_from_diagnosis(case.diagnosis_id)

    assert candidate.fault_type is fault_type
    assert candidate.status is KnowledgeCandidateStatus.CANDIDATE
    assert candidate.source is KnowledgeCandidateSource.DIAGNOSIS_CONFIRMATION
    assert candidate.candidate_label == f"{fault_type.value}_candidate"


def test_candidate_preserves_traceability_without_copying_evidence_payload():
    case = _case()
    conclusion = case.conclusion
    assert conclusion is not None

    candidate = _generator(case).generate_from_diagnosis(case.diagnosis_id)

    assert candidate.source_diagnosis_id == case.diagnosis_id
    assert candidate.source_conclusion_id == conclusion.conclusion_id
    assert candidate.source_evidence_ids == conclusion.cited_evidence_ids
    serialized = candidate.model_dump_json()
    assert "must-not-be-copied" not in serialized
    assert '"payload"' not in serialized


def test_generation_does_not_modify_diagnosis_or_conclusion():
    case = _case()
    before = case.model_dump(mode="json")

    _generator(case).generate_from_diagnosis(case.diagnosis_id)

    assert case.model_dump(mode="json") == before


def test_generated_text_is_redacted_and_steps_are_deduplicated():
    case = _case()
    case.description += " card_no=330100001 person_id=person-88 license_plate=浙A12345"

    candidate = _generator(case).generate_from_diagnosis(case.diagnosis_id)

    serialized = candidate.model_dump_json()
    assert "plain-secret" not in serialized
    assert "secret-value" not in serialized
    assert "abc.def.ghi" not in serialized
    assert "330100001" not in serialized
    assert "person-88" not in serialized
    assert "浙A12345" not in serialized
    assert candidate.redacted is True
    assert candidate.troubleshooting_steps == ["检查设备事实", "完成现场复核"]


def test_root_cause_falls_back_to_rule_explanation():
    case = _case()

    candidate = _generator(case).generate_from_diagnosis(case.diagnosis_id)

    assert candidate.root_cause == "规则归纳的候选根因"


def test_unconfirmed_diagnosis_is_rejected():
    case = _case(confirm=False)

    with pytest.raises(KnowledgeCandidateGenerationError, match="只有 confirmed"):
        _generator(case).generate_from_diagnosis(case.diagnosis_id)


def test_forged_confirmed_status_without_human_review_is_rejected():
    case = _case(confirm=False)
    case.status = SecurityDiagnosisStatus.CONFIRMED

    with pytest.raises(KnowledgeCandidateGenerationError, match="人工 confirm"):
        _generator(case).generate_from_diagnosis(case.diagnosis_id)


def test_confirmed_diagnosis_without_conclusion_is_rejected():
    case = _case()
    case.conclusion = None

    with pytest.raises(KnowledgeCandidateGenerationError, match="没有候选结论"):
        _generator(case).generate_from_diagnosis(case.diagnosis_id)


def test_confirmed_diagnosis_without_citations_is_rejected():
    case = _case()
    assert case.conclusion is not None
    case.conclusion.cited_evidence_ids = []

    with pytest.raises(KnowledgeCandidateGenerationError, match="没有引用 Evidence"):
        _generator(case).generate_from_diagnosis(case.diagnosis_id)


def test_candidate_is_not_automatically_confirmed():
    case = _case()

    candidate = _generator(case).generate_from_diagnosis(case.diagnosis_id)

    assert candidate.status is KnowledgeCandidateStatus.CANDIDATE
    assert candidate.reviews == []


@pytest.mark.parametrize(
    ("container_builder", "device_id", "fault_type"),
    [
        (build_phase1_container, "cam-offline-01", SecurityFaultType.CAMERA_BLACK_SCREEN),
        (
            build_phase2_container,
            "cam-rec-plan-disabled-01",
            SecurityFaultType.RECORDING_MISSING,
        ),
        (
            build_phase3_container,
            "access-credential-frozen-01",
            SecurityFaultType.ACCESS_CARD_FAILED,
        ),
        (
            build_phase4_container,
            "alarm-rule-sensitive-01",
            SecurityFaultType.ALARM_FALSE_POSITIVE,
        ),
    ],
)
def test_phase1_to_phase4_real_application_flows_generate_candidates(
    container_builder,
    device_id,
    fault_type,
):
    """经 Runner、工具、引用校验和人工确认后，四个域都能沉淀候选知识。"""
    container = container_builder()
    service = container.service
    case = service.create_diagnosis(
        device_id=device_id,
        fault_type=fault_type,
        reporter="phase5b-test",
        description=f"{fault_type.value} 固定案例",
    )
    run_result = service.run_diagnosis(case.diagnosis_id)
    assert run_result.status is SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
    service.review_diagnosis(
        case.diagnosis_id,
        HumanReviewAction.CONFIRM,
        reviewer="domain-expert",
        comment="固定案例人工复核通过",
    )

    candidate = KnowledgeCandidateApplicationService(
        service
    ).generate_from_diagnosis(case.diagnosis_id)

    assert candidate.fault_type is fault_type
    assert candidate.status is KnowledgeCandidateStatus.CANDIDATE
    assert candidate.source_evidence_ids
    assert candidate.candidate_label == service.infer_candidate_label(
        service.get_diagnosis(case.diagnosis_id)
    ).label.value
