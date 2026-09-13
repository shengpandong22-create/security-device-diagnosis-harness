"""Run the fixed offline Phase 8C history, trend, and gate demonstration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus
from security_diagnosis_harness.evaluation import (
    CodeBasedGrader,
    DatasetCase,
    DatasetRegistry,
    DatasetSplit,
    EvaluationOutput,
    EvaluationRun,
    EvidenceTrace,
    JsonEvaluationHistory,
    RunIdentity,
    ToolCallTrace,
    write_evaluation_trend_report,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = ROOT / "datasets/security-diagnosis/1.0.0"


def _output(case: DatasetCase, *, mismatch: bool = False) -> EvaluationOutput:
    evidence = tuple(
        EvidenceTrace(evidence_id=f"{case.case_id}-{index}", evidence_type=evidence_type)
        for index, evidence_type in enumerate(case.required_evidence_types)
    )
    return EvaluationOutput(
        case_id=case.case_id,
        completed=True,
        candidate_label="intentional-mismatch" if mismatch else case.expected_candidate,
        conclusion_fault_type=case.fault_type,
        final_status=SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION,
        cited_evidence_ids=tuple(item.evidence_id for item in evidence),
        rounds=2,
        tool_calls=tuple(ToolCallTrace(tool_name=name, ok=True) for name in case.expected_tools),
        evidence=evidence,
    )


def _run(cases: tuple[DatasetCase, ...], commit: str, *, mismatch: bool) -> EvaluationRun:
    return EvaluationRun(
        run_id=f"phase8c-{commit[:7]}",
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
        identity=RunIdentity(
            code_commit=commit,
            dataset_name="security-diagnosis",
            dataset_version="1.0.0",
            split=DatasetSplit.DEV,
            runner_name="phase8c-fixed-trend",
            model_name="fake-llm",
            model_parameters={"temperature": 0},
            prompt_hash="1" * 64,
            configuration_hash="2" * 64,
            environment={"python": "3.12"},
        ),
        grade=CodeBasedGrader().grade_suite(
            cases, tuple(_output(case, mismatch=mismatch) for case in cases)
        ),
    )


def main() -> int:
    cases = DatasetRegistry.load(DATASET_ROOT).cases(DatasetSplit.DEV)
    baseline = _run(cases, "a" * 40, mismatch=False)
    candidate = _run(cases, "b" * 40, mismatch=True)
    with TemporaryDirectory(prefix="phase8c-") as directory:
        history = JsonEvaluationHistory(Path(directory) / "history.json")
        history.append(baseline)
        candidate_record = history.append(candidate, baseline=baseline)
        trend = history.trend()
    json_path, markdown_path = write_evaluation_trend_report(trend, ROOT / "demo-output")
    print(
        json.dumps(
            {
                "comparable_run_count": len(trend.points),
                "candidate_gate_allowed": candidate_record.gate_allowed,
                "blocked_run_ids": trend.blocked_run_ids,
                "history_contains_case_details": False,
                "external_model_called": False,
                "json_report": str(json_path.relative_to(ROOT)),
                "markdown_report": str(markdown_path.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
