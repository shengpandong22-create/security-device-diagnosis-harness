"""Generate the deterministic Phase 8B failure attribution report."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus
from security_diagnosis_harness.evaluation import (
    CodeBasedGrader,
    DatasetRegistry,
    DatasetSplit,
    EvaluationOutput,
    EvaluationRun,
    RunIdentity,
    attribute_evaluation_run,
    write_failure_attribution_report,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = ROOT / "datasets/security-diagnosis/1.0.0"


def main() -> int:
    case = DatasetRegistry.load(DATASET_ROOT).cases(DatasetSplit.DEV)[0]
    output = EvaluationOutput(
        case_id=case.case_id,
        completed=False,
        candidate_label="phase8b-intentional-mismatch",
        conclusion_fault_type=case.fault_type,
        final_status=SecurityDiagnosisStatus.CONFIRMED,
        auto_confirmed=True,
    )
    identity = RunIdentity(
        code_commit="8" * 40,
        dataset_name="security-diagnosis",
        dataset_version="1.0.0",
        split=DatasetSplit.DEV,
        runner_name="phase8b-fixed-attribution",
        model_name="fake-llm",
        prompt_hash="1" * 64,
        configuration_hash="2" * 64,
    )
    run = EvaluationRun(
        run_id="phase8b-fixed-attribution-1.0.0",
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
        identity=identity,
        grade=CodeBasedGrader().grade_suite((case,), (output,)),
    )
    report = attribute_evaluation_run(run)
    json_path, markdown_path = write_failure_attribution_report(report, ROOT / "demo-output")
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "finding_count": len(report.attributions),
                "affected_case_count": len(report.affected_cases),
                "p0_count": report.p0_count,
                "json_report": str(json_path.relative_to(ROOT)),
                "markdown_report": str(markdown_path.relative_to(ROOT)),
                "external_model_called": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
