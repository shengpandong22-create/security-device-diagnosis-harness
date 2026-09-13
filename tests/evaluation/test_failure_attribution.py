from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus
from security_diagnosis_harness.evaluation import (
    FAILURE_CATALOG,
    CodeBasedGrader,
    DatasetRegistry,
    DatasetSplit,
    EvaluationOutput,
    EvaluationRun,
    FailureAttributionError,
    FailureStage,
    FindingLevel,
    GraderFinding,
    ResponsibilityOwner,
    RunIdentity,
    attribute_evaluation_run,
    catalog_entry,
    write_failure_attribution_report,
)
from security_diagnosis_harness.evaluation import grader as grader_module

DATASET_ROOT = Path(__file__).resolve().parents[2] / "datasets/security-diagnosis/1.0.0"
EXPECTED_CODES = {
    "automatic_confirmed", "sensitive_data_leak", "unauthorized_tool",
    "cross_fault_execution", "budget_exceeded", "timeout_exceeded",
    "model_budget_exceeded", "invalid_tool_arguments", "repeated_failed_tool_call",
    "required_evidence_missing", "citation_noncompliance", "unsupported_claim",
    "candidate_mismatch", "task_incomplete",
}


def _run(commit: str = "a" * 40) -> EvaluationRun:
    case = DatasetRegistry.load(DATASET_ROOT).cases(DatasetSplit.DEV)[0]
    output = EvaluationOutput(
        case_id=case.case_id,
        completed=False,
        candidate_label="wrong-label",
        conclusion_fault_type=case.fault_type,
        final_status=SecurityDiagnosisStatus.CONFIRMED,
        auto_confirmed=True,
        sensitive_leak_count=1,
        unsupported_claim_count=1,
        claim_count=1,
    )
    return EvaluationRun(
        run_id=f"run-{commit[:7]}",
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
        identity=RunIdentity(
            code_commit=commit,
            dataset_name="security-diagnosis",
            dataset_version="1.0.0",
            split=DatasetSplit.DEV,
            runner_name="phase8b-fixed",
            model_name="fake-llm",
            prompt_hash="1" * 64,
            configuration_hash="2" * 64,
        ),
        grade=CodeBasedGrader().grade_suite((case,), (output,)),
    )


def test_catalog_covers_every_code_emitted_by_code_grader():
    assert set(FAILURE_CATALOG) == EXPECTED_CODES
    emitted_codes = set(re.findall(r'_finding\("([a-z_]+)"', inspect.getsource(grader_module)))
    assert emitted_codes == EXPECTED_CODES


def test_catalog_uses_only_stable_stages_and_governed_owners():
    assert {item.stage for item in FAILURE_CATALOG.values()} <= set(FailureStage)
    assert {item.owner for item in FAILURE_CATALOG.values()} <= set(ResponsibilityOwner)


def test_unknown_finding_is_rejected_instead_of_guessed():
    finding = GraderFinding(code="new_unknown", level=FindingLevel.ERROR, stage="x", message="x")
    with pytest.raises(FailureAttributionError, match="未治理"):
        catalog_entry(finding)


def test_p0_level_is_inherited_from_code_grader_and_remains_blocking():
    report = attribute_evaluation_run(_run())
    automatic = next(
        item for item in report.attributions if item.finding_code == "automatic_confirmed"
    )
    assert automatic.level is FindingLevel.P0
    assert automatic.p0_blocking is True
    assert report.p0_count == 2


def test_attribution_api_has_no_severity_override_parameter():
    with pytest.raises(TypeError):
        attribute_evaluation_run(_run(), severity_override={"automatic_confirmed": "warning"})


def test_previous_report_preserves_first_seen_version():
    first = attribute_evaluation_run(_run("a" * 40))
    current = attribute_evaluation_run(_run("b" * 40), (first,))
    assert {item.first_seen_commit for item in current.attributions} == {"a" * 40}
    assert {item.first_seen_dataset_version for item in current.attributions} == {"1.0.0"}


def test_earliest_supplied_history_wins_when_multiple_reports_exist():
    first = attribute_evaluation_run(_run("a" * 40))
    second = attribute_evaluation_run(_run("b" * 40))
    current = attribute_evaluation_run(_run("c" * 40), (first, second))
    assert {item.first_seen_commit for item in current.attributions} == {"a" * 40}


def test_report_lists_affected_cases_and_stable_counts():
    report = attribute_evaluation_run(_run())
    assert len(report.affected_cases) == 1
    assert sum(report.stage_counts.values()) == len(report.attributions)
    assert sum(report.owner_counts.values()) == len(report.attributions)


def test_report_uses_catalog_actions_not_untrusted_finding_messages(tmp_path):
    report = attribute_evaluation_run(_run())
    json_path, markdown_path = write_failure_attribution_report(report, tmp_path)
    combined = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")
    assert "password=DO-NOT-LEAK" not in combined
    assert "可能" not in combined
    assert "核验" in combined or "检查" in combined
    assert not list(tmp_path.glob("*.tmp"))
