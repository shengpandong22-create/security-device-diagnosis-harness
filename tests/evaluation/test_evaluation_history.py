from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus
from security_diagnosis_harness.evaluation import (
    CodeBasedGrader,
    DatasetCase,
    DatasetRegistry,
    DatasetSplit,
    EvaluationHistoryError,
    EvaluationOutput,
    EvaluationRun,
    EvidenceTrace,
    JsonEvaluationHistory,
    RunIdentity,
    ToolCallTrace,
    TrendComparabilityError,
    write_evaluation_trend_report,
)

ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "datasets/security-diagnosis/1.0.0"


@pytest.fixture(scope="module")
def cases() -> tuple[DatasetCase, ...]:
    return DatasetRegistry.load(DATASET_ROOT).cases(DatasetSplit.DEV)


def _output(case: DatasetCase, *, wrong: bool = False, p0: bool = False) -> EvaluationOutput:
    evidence = tuple(
        EvidenceTrace(evidence_id=f"{case.case_id}-{index}", evidence_type=evidence_type)
        for index, evidence_type in enumerate(case.required_evidence_types)
    )
    return EvaluationOutput(
        case_id=case.case_id,
        completed=True,
        candidate_label="wrong" if wrong else case.expected_candidate,
        conclusion_fault_type=case.fault_type,
        final_status=(
            SecurityDiagnosisStatus.CONFIRMED
            if p0 else SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
        ),
        auto_confirmed=p0,
        cited_evidence_ids=tuple(item.evidence_id for item in evidence),
        rounds=2,
        tool_calls=tuple(ToolCallTrace(tool_name=name, ok=True) for name in case.expected_tools),
        evidence=evidence,
    )


def _run(
    cases: tuple[DatasetCase, ...],
    commit: str,
    *,
    wrong: bool = False,
    p0: bool = False,
    **identity_changes,
) -> EvaluationRun:
    identity = RunIdentity(
        code_commit=commit,
        dataset_name="security-diagnosis",
        dataset_version="1.0.0",
        split=DatasetSplit.DEV,
        runner_name="phase8c-fixed",
        model_name="fake-llm",
        model_parameters={"temperature": 0},
        prompt_hash="1" * 64,
        configuration_hash="2" * 64,
        environment={"python": "3.12"},
    ).model_copy(update=identity_changes)
    return EvaluationRun(
        run_id=f"run-{commit[:7]}",
        created_at=datetime(2026, 9, 13, tzinfo=UTC) + timedelta(seconds=len(commit)),
        identity=identity,
        grade=CodeBasedGrader().grade_suite(
            cases, tuple(_output(case, wrong=wrong, p0=p0) for case in cases)
        ),
    )


def test_first_run_is_persisted_as_baseline_summary(tmp_path, cases):
    history = JsonEvaluationHistory(tmp_path / "history.json")
    record = history.append(_run(cases, "a" * 40))
    assert record.baseline_run_id is None
    assert record.gate_allowed is None
    assert history.load().records == (record,)


def test_history_json_excludes_cases_findings_traces_and_environment_values(tmp_path, cases):
    path = tmp_path / "history.json"
    JsonEvaluationHistory(path).append(_run(cases, "a" * 40))
    raw = path.read_text(encoding="utf-8")
    for forbidden in ('"cases"', '"findings"', '"tool_calls"', '"evidence"', '"grade"'):
        assert forbidden not in raw
    assert '"python": "3.12"' not in raw
    assert '"environment_hash"' in raw


def test_second_run_requires_persisted_matching_baseline(tmp_path, cases):
    history = JsonEvaluationHistory(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    history.append(baseline)
    with pytest.raises(EvaluationHistoryError, match="必须提供"):
        history.append(_run(cases, "b" * 40))
    unknown = _run(cases, "c" * 40)
    with pytest.raises(EvaluationHistoryError, match="已入历史"):
        history.append(_run(cases, "b" * 40), baseline=unknown)


def test_baseline_run_id_with_changed_content_hash_is_rejected(tmp_path, cases):
    history = JsonEvaluationHistory(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    history.append(baseline)
    changed = baseline.model_copy(
        update={
            "grade": CodeBasedGrader().grade_suite(
                cases, tuple(_output(case, wrong=True) for case in cases)
            )
        }
    )
    with pytest.raises(EvaluationHistoryError, match="哈希一致"):
        history.append(_run(cases, "b" * 40), baseline=changed)


def test_duplicate_run_id_is_rejected(tmp_path, cases):
    history = JsonEvaluationHistory(tmp_path / "history.json")
    run = _run(cases, "a" * 40)
    history.append(run)
    with pytest.raises(EvaluationHistoryError, match="已存在"):
        history.append(run)


@pytest.mark.parametrize(
    "changes",
    [
        {"dataset_version": "1.1.0"},
        {"model_name": "another-model"},
        {"prompt_hash": "3" * 64},
        {"configuration_hash": "4" * 64},
        {"model_parameters": {"temperature": 0.1}},
        {"environment": {"python": "3.13"}},
    ],
)
def test_incomparable_runs_never_enter_trend(tmp_path, cases, changes):
    history = JsonEvaluationHistory(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    history.append(baseline)
    candidate = _run(cases, "b" * 40, **changes)
    with pytest.raises(TrendComparabilityError, match="变量") as excinfo:
        history.append(candidate, baseline=baseline)
    assert excinfo.value.changed_fields
    assert len(history.load().records) == 1


def test_regression_reuses_phase7_gate_and_is_recorded(tmp_path, cases):
    history = JsonEvaluationHistory(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40, wrong=True)
    history.append(baseline)
    record = history.append(candidate, baseline=baseline)
    assert record.gate_allowed is False
    assert any("candidate_accuracy" in reason for reason in record.blocking_reasons)
    assert history.trend().blocked_run_ids == (candidate.run_id,)


def test_p0_is_recomputed_from_cases_and_blocks_trend_run(tmp_path, cases):
    history = JsonEvaluationHistory(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40, p0=True)
    tampered_metrics = candidate.grade.metrics.model_copy(update={"p0_failure_count": 0})
    candidate = candidate.model_copy(
        update={"grade": candidate.grade.model_copy(update={"metrics": tampered_metrics})}
    )
    history.append(baseline)
    record = history.append(candidate, baseline=baseline)
    assert record.blocked_by_p0 is True
    assert record.summary.deterministic_p0_count == len(cases)
    assert record.summary.metrics.p0_failure_count == len(cases)


def test_trend_contains_only_core_aggregate_metrics(tmp_path, cases):
    history = JsonEvaluationHistory(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40)
    history.append(baseline)
    history.append(candidate, baseline=baseline)
    report = history.trend()
    assert len(report.points) == 2
    assert set(report.points[0].metrics) == {
        "task_completion_rate", "candidate_accuracy", "candidate_macro_f1",
        "citation_compliance", "tool_recall", "evidence_coverage",
    }


def test_trend_report_is_atomic_and_contains_lineage(tmp_path, cases):
    history = JsonEvaluationHistory(tmp_path / "history.json")
    baseline = _run(cases, "a" * 40)
    candidate = _run(cases, "b" * 40)
    history.append(baseline)
    history.append(candidate, baseline=baseline)
    json_path, markdown_path = write_evaluation_trend_report(history.trend(), tmp_path)
    assert baseline.run_id in markdown_path.read_text(encoding="utf-8")
    assert candidate.run_id in json_path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_invalid_or_mixed_history_is_rejected(tmp_path, cases):
    path = tmp_path / "history.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(EvaluationHistoryError, match="协议"):
        JsonEvaluationHistory(path).load()

    history = JsonEvaluationHistory(path)
    first = _run(cases, "a" * 40)
    path.unlink()
    history.append(first)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"][0]["summary"]["comparison_fingerprint"] = "f" * 64
    data["records"].append(data["records"][0] | {
        "summary": data["records"][0]["summary"] | {
            "run_id": "tampered-run", "comparison_fingerprint": "e" * 64
        }
    })
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvaluationHistoryError, match="不可比"):
        history.trend()


def test_model_copy_cannot_bypass_sensitive_identity_validation(tmp_path, cases):
    run = _run(cases, "a" * 40)
    unsafe_identity = run.identity.model_copy(
        update={"environment": {"api_key": "DO-NOT-PERSIST"}}
    )
    unsafe_run = run.model_copy(update={"identity": unsafe_identity})
    with pytest.raises(ValueError, match="敏感"):
        JsonEvaluationHistory(tmp_path / "history.json").append(unsafe_run)
    assert not (tmp_path / "history.json").exists()


def test_loaded_summary_rejects_sensitive_model_parameters(tmp_path, cases):
    path = tmp_path / "history.json"
    history = JsonEvaluationHistory(path)
    history.append(_run(cases, "a" * 40))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"][0]["summary"]["model_parameters"] = {
        "api_key": "DO-NOT-LOAD"
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvaluationHistoryError, match="协议"):
        history.load()
