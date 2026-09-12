"""用合成盲标对验证 Phase 8A 一致性报告协议。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from security_diagnosis_harness.evaluation import (
    BlindAnnotation,
    DatasetRegistry,
    DatasetSplit,
    build_annotation_agreement_report,
    build_annotation_task,
    compare_blind_annotations,
    write_annotation_agreement_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = REPO_ROOT / "datasets" / "security-diagnosis" / "1.0.0"
FIXTURE_PATH = (
    REPO_ROOT / "evaluation-fixtures" / "phase8" / "annotation-pairs-1.0.0.json"
)


def evaluate(output_directory: Path) -> dict[str, Any]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture.get("fixture_kind") != "synthetic_protocol_fixture":
        raise ValueError("Phase 8A 固定输入必须明确标记为合成协议夹具")
    registry = DatasetRegistry.load(DATASET_ROOT)
    cases = {
        case.case_id: case
        for split in (DatasetSplit.DEV, DatasetSplit.VALIDATION)
        for case in registry.cases(split)
    }
    disagreements = []
    for pair in fixture["pairs"]:
        case = cases[pair["case_id"]]
        task = build_annotation_task(case).model_copy(
            update={"task_id": f"annotation-task-{case.case_id}"}
        )
        first = BlindAnnotation(
            annotation_id=f"annotation-{case.case_id}-a",
            task_id=task.task_id,
            **pair["first"],
        )
        second = BlindAnnotation(
            annotation_id=f"annotation-{case.case_id}-b",
            task_id=task.task_id,
            **pair["second"],
        )
        disagreements.append(compare_blind_annotations(task, first, second))
    report = build_annotation_agreement_report(tuple(disagreements))
    json_path, markdown_path = write_annotation_agreement_report(report, output_directory)
    return {
        "fixture_kind": fixture["fixture_kind"],
        "task_count": report.task_count,
        "label_agreement_rate": report.label_agreement_rate,
        "mean_tool_jaccard": report.mean_tool_jaccard,
        "mean_evidence_jaccard": report.mean_evidence_jaccard,
        "label_kappa": report.label_kappa,
        "adjudication_rate": report.adjudication_rate,
        "json_report": str(json_path),
        "markdown_report": str(markdown_path),
    }


def main() -> int:
    result = evaluate(Path("demo-output"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
