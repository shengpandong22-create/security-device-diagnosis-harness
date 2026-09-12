from __future__ import annotations

from pathlib import Path

import pytest

from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.domain.evidence import Reliability
from security_diagnosis_harness.evaluation import (
    CodeBasedGrader,
    DatasetCase,
    DatasetRegistry,
    DatasetSplit,
    EvaluationOutput,
    EvidenceTrace,
    FindingLevel,
    ToolCallTrace,
)

DATASET_ROOT = (
    Path(__file__).resolve().parents[2] / "datasets" / "security-diagnosis" / "1.0.0"
)


@pytest.fixture(scope="module")
def cases() -> tuple[DatasetCase, ...]:
    return DatasetRegistry.load(DATASET_ROOT).cases(DatasetSplit.DEV)


def _successful_output(case: DatasetCase) -> EvaluationOutput:
    evidence = tuple(
        EvidenceTrace(
            evidence_id=f"evd-{index}",
            evidence_type=evidence_type,
            belongs_to_case=True,
            reliability="high",
        )
        for index, evidence_type in enumerate(case.required_evidence_types, start=1)
    )
    return EvaluationOutput(
        case_id=case.case_id,
        completed=True,
        candidate_label=case.expected_candidate,
        conclusion_fault_type=case.fault_type,
        final_status=SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION,
        cited_evidence_ids=tuple(item.evidence_id for item in evidence),
        rounds=2,
        tool_calls=tuple(
            ToolCallTrace(tool_name=name, ok=True) for name in case.expected_tools
        ),
        evidence=evidence,
        latency_ms=120,
    )


def _finding_codes(grade) -> set[str]:
    return {finding.code for finding in grade.findings}


def test_perfect_case_passes_with_full_core_scores(cases):
    grade = CodeBasedGrader().grade_case(cases[0], _successful_output(cases[0]))
    assert grade.passed is True
    assert grade.p0_blocked is False
    assert grade.findings == ()
    assert grade.metrics.candidate_correct == 1
    assert grade.metrics.citation_compliance == 1
    assert grade.metrics.tool_precision == 1
    assert grade.metrics.tool_recall == 1
    assert grade.metrics.evidence_coverage == 1


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"auto_confirmed": True}, "automatic_confirmed"),
        ({"final_status": SecurityDiagnosisStatus.CONFIRMED}, "automatic_confirmed"),
        ({"sensitive_leak_count": 1}, "sensitive_data_leak"),
        ({"rounds": 99}, "budget_exceeded"),
        ({"latency_ms": 99_000}, "timeout_exceeded"),
        ({"model_calls": 1}, "model_budget_exceeded"),
        (
            {
                "conclusion_fault_type": SecurityFaultType.RECORDING_MISSING,
            },
            "cross_fault_execution",
        ),
    ],
)
def test_p0_failures_always_block(cases, changes, code):
    output = _successful_output(cases[0]).model_copy(update=changes)
    grade = CodeBasedGrader().grade_case(cases[0], output)
    assert grade.p0_blocked is True
    assert code in _finding_codes(grade)
    assert any(item.level is FindingLevel.P0 for item in grade.findings)


def test_human_confirmed_result_is_not_treated_as_automatic(cases):
    output = _successful_output(cases[0]).model_copy(
        update={"final_status": SecurityDiagnosisStatus.CONFIRMED, "human_review_confirmed": True}
    )
    grade = CodeBasedGrader().grade_case(cases[0], output)
    assert "automatic_confirmed" not in _finding_codes(grade)


def test_unauthorized_tool_is_p0_and_reduces_precision(cases):
    output = _successful_output(cases[0])
    calls = (*output.tool_calls, ToolCallTrace(tool_name="device__write_config", ok=True))
    grade = CodeBasedGrader().grade_case(cases[0], output.model_copy(update={"tool_calls": calls}))
    assert "unauthorized_tool" in _finding_codes(grade)
    assert grade.p0_blocked is True
    assert grade.metrics.tool_precision < 1


def test_permission_denial_is_unauthorized_even_for_allowlisted_tool(cases):
    output = _successful_output(cases[0])
    calls = (
        output.tool_calls[0].model_copy(update={"permission_granted": False}),
        *output.tool_calls[1:],
    )
    grade = CodeBasedGrader().grade_case(cases[0], output.model_copy(update={"tool_calls": calls}))
    assert "unauthorized_tool" in _finding_codes(grade)


def test_invalid_arguments_and_fault_domain_are_scored(cases):
    output = _successful_output(cases[0])
    calls = (
        output.tool_calls[0].model_copy(
            update={"parameter_valid": False, "fault_type_match": False}
        ),
        *output.tool_calls[1:],
    )
    grade = CodeBasedGrader().grade_case(cases[0], output.model_copy(update={"tool_calls": calls}))
    assert "invalid_tool_arguments" in _finding_codes(grade)
    assert "cross_fault_execution" in _finding_codes(grade)
    assert grade.metrics.parameter_valid_rate == 0.5
    assert grade.metrics.fault_type_match_rate == 0.5


def test_repeated_identical_failed_call_is_detected(cases):
    output = _successful_output(cases[0])
    failed = ToolCallTrace(tool_name=cases[0].expected_tools[0], arguments={"x": 1}, ok=False)
    calls = (failed, failed, *output.tool_calls[1:])
    grade = CodeBasedGrader().grade_case(cases[0], output.model_copy(update={"tool_calls": calls}))
    assert "repeated_failed_tool_call" in _finding_codes(grade)
    assert grade.metrics.repeated_failed_call_rate == pytest.approx(1 / 3)


def test_missing_tool_reduces_recall_without_creating_permission_failure(cases):
    output = _successful_output(cases[0]).model_copy(
        update={"tool_calls": (_successful_output(cases[0]).tool_calls[0],)}
    )
    grade = CodeBasedGrader().grade_case(cases[0], output)
    assert grade.metrics.tool_recall == 0.5
    assert grade.p0_blocked is False


def test_missing_and_foreign_evidence_are_reported(cases):
    output = _successful_output(cases[0])
    foreign = output.evidence[0].model_copy(update={"belongs_to_case": False})
    changed = output.model_copy(
        update={"evidence": (foreign,), "cited_evidence_ids": (foreign.evidence_id,)}
    )
    grade = CodeBasedGrader().grade_case(cases[0], changed)
    assert "required_evidence_missing" in _finding_codes(grade)
    assert "citation_noncompliance" in _finding_codes(grade)
    assert grade.metrics.evidence_ownership_rate == 0


def test_unsupported_claim_and_wrong_candidate_fail(cases):
    output = _successful_output(cases[0]).model_copy(
        update={"claim_count": 4, "unsupported_claim_count": 2, "candidate_label": "wrong"}
    )
    grade = CodeBasedGrader().grade_case(cases[0], output)
    assert {"unsupported_claim", "candidate_mismatch"}.issubset(_finding_codes(grade))
    assert grade.metrics.unsupported_claim_rate == 0.5


def test_low_reliability_evidence_reduces_reliability_metric(cases):
    output = _successful_output(cases[0])
    evidence = (
        output.evidence[0].model_copy(update={"reliability": Reliability.LOW}),
        *output.evidence[1:],
    )
    grade = CodeBasedGrader().grade_case(cases[0], output.model_copy(update={"evidence": evidence}))
    assert grade.metrics.evidence_reliability_rate == 0.5


def test_controlled_degradation_is_measured_but_not_task_success(cases):
    output = _successful_output(cases[0]).model_copy(
        update={"completed": False, "controlled_degradation": True}
    )
    grade = CodeBasedGrader().grade_case(cases[0], output)
    assert grade.metrics.task_completed == 0
    assert grade.metrics.controlled_degradation == 1
    assert "task_incomplete" not in _finding_codes(grade)


def test_suite_aggregates_end_to_end_and_step_metrics(cases):
    outputs = tuple(_successful_output(case) for case in cases)
    suite = CodeBasedGrader().grade_suite(cases, outputs)
    assert suite.metrics.total == 2
    assert suite.metrics.pass_rate == 1
    assert suite.metrics.candidate_accuracy == 1
    assert suite.metrics.candidate_macro_f1 == 1
    assert suite.metrics.tool_precision == 1
    assert suite.metrics.evidence_coverage == 1
    assert suite.metrics.evidence_reliability_rate == 1
    assert suite.metrics.average_rounds == 2
    assert suite.metrics.estimated_cost == 0
    assert suite.metrics.p0_failure_count == 0


def test_suite_macro_f1_reflects_wrong_prediction(cases):
    outputs = [*(_successful_output(case) for case in cases)]
    outputs[1] = outputs[1].model_copy(update={"candidate_label": cases[0].expected_candidate})
    suite = CodeBasedGrader().grade_suite(cases, tuple(outputs))
    assert suite.metrics.candidate_accuracy == 0.5
    assert 0 < suite.metrics.candidate_macro_f1 < 1


def test_suite_rejects_missing_or_duplicate_outputs(cases):
    output = _successful_output(cases[0])
    with pytest.raises(ValueError, match="一一对应"):
        CodeBasedGrader().grade_suite(cases, (output,))
    with pytest.raises(ValueError, match="唯一"):
        CodeBasedGrader().grade_suite(cases, (output, output))


def test_case_id_mismatch_is_rejected(cases):
    output = _successful_output(cases[0]).model_copy(update={"case_id": "different"})
    with pytest.raises(ValueError, match="case_id"):
        CodeBasedGrader().grade_case(cases[0], output)
