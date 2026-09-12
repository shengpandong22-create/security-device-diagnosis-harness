"""手动运行 Phase 7D 受限真实模型评测；默认拒绝执行外部调用。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from security_diagnosis_harness.evaluation import (
    DatasetRegistry,
    DatasetSplit,
    OpenAICompatibleEvaluationClient,
    RealModelConfigurationError,
    RealModelEvaluationRunner,
    RealModelSettings,
    write_real_model_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = REPO_ROOT / "datasets" / "security-diagnosis" / "1.0.0"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    parser.add_argument("--output-dir", type=Path, default=Path("demo-output"))
    parser.add_argument("--execute-real-model", action="store_true")
    args = parser.parse_args()
    if not args.execute_real_model:
        print(
            json.dumps(
                {
                    "executed": False,
                    "reason": "必须显式传入 --execute-real-model 才允许外部模型调用",
                },
                ensure_ascii=False,
            )
        )
        return 2
    try:
        settings = RealModelSettings.from_env()
        registry = DatasetRegistry.load(DATASET_ROOT)
        split = DatasetSplit(args.split)
        cases = registry.cases(split, allow_test=split is DatasetSplit.TEST)
        with OpenAICompatibleEvaluationClient(settings) as client:
            report = RealModelEvaluationRunner(client, settings).run(cases)
        json_path, markdown_path = write_real_model_report(report, args.output_dir)
    except RealModelConfigurationError as exc:
        print(json.dumps({"executed": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "executed": True,
                "report_kind": report.report_kind,
                "total_calls": report.total_calls,
                "failed_cases": report.failed_cases,
                "candidate_accuracy": report.candidate_accuracy,
                "tool_precision": report.tool_precision,
                "tool_recall": report.tool_recall,
                "evidence_compliance": report.evidence_compliance,
                "evidence_recall": report.evidence_recall,
                "review_pending_cases": report.review_pending_cases,
                "json_report": str(json_path),
                "markdown_report": str(markdown_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.failed_cases == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
