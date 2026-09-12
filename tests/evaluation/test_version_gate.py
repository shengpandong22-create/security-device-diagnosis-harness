from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripts.compare_phase7_runs import main as compare_main

from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus
from security_diagnosis_harness.evaluation import (
    CodeBasedGrader,
    ComparisonConfigurationError,
    DatasetCase,
    DatasetRegistry,
    DatasetSplit,
    EvaluationOutput,
    EvaluationRun,
    EvidenceTrace,
    GatePolicy,
    RunIdentity,
    ToolCallTrace,
    compare_runs,
    write_gate_report,
)

DATASET_ROOT = (
    Path(__file__).resolve().parents[2] / "datasets" / "security-diagnosis" / "1.0.0"
)
REFERENCE_BASELINE = (
    Path(__file__).resolve().parents[2]
    / "evaluation-baselines"
    / "phase7"
    / "dev-1.0.0-deterministic-reference.json"
)


@pytest.fixture(scope="module")
def cases() -> tuple[DatasetCase, ...]:
    return DatasetRegistry.load(DATASET_ROOT).cases(DatasetSplit.DEV)


def _output(case: DatasetCase, **changes) -> EvaluationOutput:
    evidence = tuple(
        EvidenceTrace(evidence_id=f"{case.case_id}-evd-{index}", evidence_type=item)
        for index, item in enumerate(case.required_evidence_types)
    )
    output = EvaluationOutput(
        case_id=case.case_id,
        completed=True,
        candidate_label=case.expected_candidate,
        conclusion_fault_type=case.fault_type,
        final_status=SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION,
        cited_evidence_ids=tuple(item.evidence_id for item in evidence),
        rounds=2,
        tool_calls=tuple(ToolCallTrace(tool_name=name, ok=True) for name in case.expected_tools),
        evidence=evidence,
        latency_ms=100,
    )
    return output.model_copy(update=changes)


def _run(
    cases: tuple[DatasetCase, ...],
    *,
    commit: str,
    run_id: str,
    outputs: tuple[EvaluationOutput, ...] | None = None,
    **identity_changes,
) -> EvaluationRun:
    identity = RunIdentity(
        code_commit=commit,
        dataset_name="security-diagnosis",
        dataset_version="1.0.0",
        split=DatasetSplit.DEV,
        runner_name="deterministic-reference",
        model_name="fake-llm",
        model_parameters={"temperature": 0},
        prompt_hash="1" * 64,
        configuration_hash="2" * 64,
        environment={"python": "3.12", "platform": "test"},
    ).model_copy(update=identity_changes)
    grade = CodeBasedGrader().grade_suite(
        cases, outputs or tuple(_output(case) for case in cases)
    )
    return EvaluationRun(
        run_id=run_id,
        created_at=datetime(2026, 9, 12, tzinfo=UTC),
        identity=identity,
        grade=grade,
    )


def _pair(cases, candidate_outputs=None):
    return (
        _run(cases, commit="a" * 40, run_id="baseline"),
        _run(
            cases,
            commit="b" * 40,
            run_id="candidate",
            outputs=candidate_outputs,
        ),
    )


def test_equal_quality_candidate_passes(cases):
    report = compare_runs(*_pair(cases))
    assert report.allowed is True
    assert report.blocked_by_p0 is False
    assert report.blocking_reasons == ()
    assert all(item.delta == 0 and not item.regressed for item in report.metric_deltas)


def test_committed_reference_baseline_is_valid_and_matches_dev_cases(cases):
    run = EvaluationRun.model_validate_json(REFERENCE_BASELINE.read_text(encoding="utf-8"))
    assert run.identity.dataset_version == "1.0.0"
    assert run.identity.split is DatasetSplit.DEV
    assert run.identity.runner_name == "deterministic-reference"
    assert {item.case_id for item in run.grade.cases} == {item.case_id for item in cases}
    assert run.grade.metrics.p0_failure_count == 0


def test_any_candidate_p0_blocks_release(cases):
    outputs = tuple(
        _output(case, auto_confirmed=index == 0) for index, case in enumerate(cases)
    )
    report = compare_runs(*_pair(cases, outputs))
    assert report.allowed is False
    assert report.blocked_by_p0 is True
    assert report.blocking_reasons[0] == "Candidate 存在 1 个 P0 失败"


def test_forged_zero_p0_summary_cannot_bypass_case_level_gate(cases):
    outputs = tuple(
        _output(case, auto_confirmed=index == 0) for index, case in enumerate(cases)
    )
    baseline, candidate = _pair(cases, outputs)
    forged_metrics = candidate.grade.metrics.model_copy(update={"p0_failure_count": 0})
    candidate = candidate.model_copy(
        update={"grade": candidate.grade.model_copy(update={"metrics": forged_metrics})}
    )
    report = compare_runs(baseline, candidate)
    assert report.allowed is False
    assert report.blocked_by_p0 is True


def test_core_metric_regression_blocks_release(cases):
    outputs = tuple(
        _output(case, candidate_label="wrong") if index == 0 else _output(case)
        for index, case in enumerate(cases)
    )
    report = compare_runs(*_pair(cases, outputs))
    assert report.allowed is False
    assert "核心指标退化: candidate_accuracy" in report.blocking_reasons
    assert "核心指标退化: candidate_macro_f1" in report.blocking_reasons


def test_regression_within_explicit_tolerance_can_pass(cases):
    outputs = tuple(
        _output(case, candidate_label="wrong") if index == 0 else _output(case)
        for index, case in enumerate(cases)
    )
    policy = GatePolicy(candidate_accuracy_tolerance=0.5, candidate_macro_f1_tolerance=1)
    report = compare_runs(*_pair(cases, outputs), policy)
    assert report.allowed is True


def test_tool_recall_uses_default_five_percent_tolerance(cases):
    outputs = tuple(
        _output(case, tool_calls=_output(case).tool_calls[:1]) for case in cases
    )
    report = compare_runs(*_pair(cases, outputs))
    delta = next(item for item in report.metric_deltas if item.metric == "tool_recall")
    assert delta.tolerance == 0.05
    assert delta.regressed is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_version", "2.0.0"),
        ("model_name", "another-model"),
        ("model_parameters", {"temperature": 0.2}),
        ("prompt_hash", "3" * 64),
        ("configuration_hash", "4" * 64),
        ("environment", {"python": "3.13"}),
    ],
)
def test_changed_controlled_variable_rejects_comparison(cases, field, value):
    baseline, candidate = _pair(cases)
    candidate = candidate.model_copy(
        update={"identity": candidate.identity.model_copy(update={field: value})}
    )
    with pytest.raises(ComparisonConfigurationError, match="变量"):
        compare_runs(baseline, candidate)


def test_same_commit_is_not_a_version_comparison(cases):
    baseline, candidate = _pair(cases)
    candidate = candidate.model_copy(
        update={"identity": candidate.identity.model_copy(update={"code_commit": "a" * 40})}
    )
    with pytest.raises(ComparisonConfigurationError, match="不同代码 commit"):
        compare_runs(baseline, candidate)


def test_same_run_id_is_rejected(cases):
    baseline, candidate = _pair(cases)
    candidate = candidate.model_copy(update={"run_id": baseline.run_id})
    with pytest.raises(ComparisonConfigurationError, match="run_id"):
        compare_runs(baseline, candidate)


def test_case_set_must_be_identical(cases):
    baseline, candidate = _pair(cases)
    candidate = candidate.model_copy(
        update={"grade": candidate.grade.model_copy(update={"cases": candidate.grade.cases[:1]})}
    )
    with pytest.raises(ComparisonConfigurationError, match="案例集合"):
        compare_runs(baseline, candidate)


def test_report_lists_new_and_fixed_findings(cases):
    baseline_outputs = (_output(cases[0], candidate_label="wrong"), _output(cases[1]))
    candidate_outputs = (_output(cases[0]), _output(cases[1], unsupported_claim_count=1))
    baseline = _run(cases, commit="a" * 40, run_id="baseline", outputs=baseline_outputs)
    candidate = _run(cases, commit="b" * 40, run_id="candidate", outputs=candidate_outputs)
    report = compare_runs(baseline, candidate, GatePolicy(candidate_macro_f1_tolerance=1))
    first = next(item for item in report.case_diffs if item.case_id == cases[0].case_id)
    second = next(item for item in report.case_diffs if item.case_id == cases[1].case_id)
    assert first.fixed_findings == ("candidate_mismatch",)
    assert second.new_findings == ("unsupported_claim",)


def test_non_core_latency_change_does_not_block(cases):
    outputs = tuple(_output(case, latency_ms=200) for case in cases)
    report = compare_runs(*_pair(cases, outputs))
    assert report.allowed is True


def test_run_identity_rejects_sensitive_metadata():
    with pytest.raises(ValidationError, match="敏感信息"):
        RunIdentity(
            code_commit="a" * 40,
            dataset_name="dataset",
            dataset_version="1.0.0",
            split="dev",
            runner_name="runner",
            model_name="model",
            model_parameters={"api_key": "plain-secret-value"},
            prompt_hash="1" * 64,
            configuration_hash="2" * 64,
        )


def test_run_content_hash_is_stable_and_commit_sensitive(cases):
    baseline, candidate = _pair(cases)
    assert baseline.content_hash() == baseline.content_hash()
    assert baseline.content_hash() != candidate.content_hash()


def test_json_and_markdown_reports_are_written_without_raw_inputs(cases, tmp_path):
    report = compare_runs(*_pair(cases))
    json_path, markdown_path = write_gate_report(report, tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload["allowed"] is True
    assert "发布门禁" in markdown
    assert "input_facts" not in markdown
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(("blocked", "expected_exit"), [(False, 0), (True, 1)])
def test_cli_exit_code_is_usable_as_release_gate(
    cases, tmp_path, monkeypatch, blocked, expected_exit
):
    outputs = tuple(
        _output(case, sensitive_leak_count=int(blocked and index == 0))
        for index, case in enumerate(cases)
    )
    baseline, candidate = _pair(cases, outputs)
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(baseline.model_dump_json(), encoding="utf-8")
    candidate_path.write_text(candidate.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare_phase7_runs.py",
            "--baseline",
            str(baseline_path),
            "--candidate",
            str(candidate_path),
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )
    assert compare_main() == expected_exit
