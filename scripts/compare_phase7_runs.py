"""比较 Phase 7 Baseline/Candidate 运行文件并执行发布门禁。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from security_diagnosis_harness.evaluation import (
    AuthenticatedEvaluationRun,
    ComparisonConfigurationError,
    DatasetRegistry,
    compare_runs,
    write_gate_report,
)

DEFAULT_DATASET_ROOT = Path("datasets")


def _load_dataset_cases(run: AuthenticatedEvaluationRun, dataset_root: Path):
    """从受控数据集加载 expected_candidate 锚；禁止隐式搜索任意路径。

    只按 RunIdentity 的 dataset_name/version/split 在显式 dataset_root 下定位，
    并与物理目录名、manifest 校验一致；不信任评分产物自带的 expected。
    """
    identity = run.run.identity
    version_directory = dataset_root / identity.dataset_name / identity.dataset_version
    if not version_directory.is_dir():
        raise ComparisonConfigurationError("受控数据集版本目录不存在")
    registry = DatasetRegistry.load(version_directory)
    return registry.cases(identity.split, allow_test=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("demo-output"))
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    args = parser.parse_args()
    try:
        integrity_key = os.environ["SECURITY_DIAGNOSIS_EVAL_INTEGRITY_KEY"].encode()
        baseline = AuthenticatedEvaluationRun.model_validate_json(
            args.baseline.read_text(encoding="utf-8")
        )
        candidate = AuthenticatedEvaluationRun.model_validate_json(
            args.candidate.read_text(encoding="utf-8")
        )
        dataset_cases = _load_dataset_cases(candidate, args.dataset_root)
        report = compare_runs(
            baseline,
            candidate,
            dataset_cases=dataset_cases,
            integrity_key=integrity_key,
        )
        json_path, markdown_path = write_gate_report(report, args.output_dir)
    except (KeyError, OSError, ValueError, ComparisonConfigurationError) as exc:
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
