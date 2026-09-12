"""比较 Phase 7 Baseline/Candidate 运行文件并执行发布门禁。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from security_diagnosis_harness.evaluation import (
    ComparisonConfigurationError,
    EvaluationRun,
    compare_runs,
    write_gate_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("demo-output"))
    args = parser.parse_args()
    try:
        baseline = EvaluationRun.model_validate_json(args.baseline.read_text(encoding="utf-8"))
        candidate = EvaluationRun.model_validate_json(args.candidate.read_text(encoding="utf-8"))
        report = compare_runs(baseline, candidate)
        json_path, markdown_path = write_gate_report(report, args.output_dir)
    except (OSError, ValueError, ComparisonConfigurationError) as exc:
        print(json.dumps({"allowed": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "allowed": report.allowed,
                "blocked_by_p0": report.blocked_by_p0,
                "blocking_reasons": report.blocking_reasons,
                "json_report": str(json_path),
                "markdown_report": str(markdown_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
