"""手动运行 Phase 7D 受限真实模型评测；默认拒绝执行外部调用。"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from pathlib import Path

from security_diagnosis_harness.domain.common import canonical_json, sha256_text
from security_diagnosis_harness.evaluation import (
    REAL_MODEL_SYSTEM_PROMPT,
    DatasetRegistry,
    DatasetSplit,
    OpenAICompatibleEvaluationClient,
    PublishedDatasetAnchor,
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
    parser.add_argument("--dataset-anchor", type=Path)
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
        if args.dataset_anchor is None:
            raise RealModelConfigurationError("真实模型评测必须提供预签 --dataset-anchor")
        integrity_key = os.environ["SECURITY_DIAGNOSIS_EVAL_INTEGRITY_KEY"].encode()
        settings = RealModelSettings.from_env()
        registry = DatasetRegistry.load(DATASET_ROOT)
        split = DatasetSplit(args.split)
        cases = registry.cases(split, allow_test=split is DatasetSplit.TEST)
        published_anchor = PublishedDatasetAnchor.model_validate_json(
            args.dataset_anchor.read_text(encoding="utf-8")
        )
        trusted_anchor = published_anchor.verify(integrity_key)
        if trusted_anchor.dataset_name != "security-diagnosis":
            raise RealModelConfigurationError("Dataset Anchor 名称不匹配")
        if trusted_anchor.cases != cases:
            raise RealModelConfigurationError("Dataset Anchor 与本次 split 案例不匹配")
        with OpenAICompatibleEvaluationClient(settings) as client:
            report = RealModelEvaluationRunner(client, settings).run(cases)
        safe_configuration = {
            "model_name": settings.model_name,
            "timeout_seconds": settings.timeout_seconds,
            "max_cases": settings.max_cases,
            "input_cost_per_million": settings.input_cost_per_million,
            "output_cost_per_million": settings.output_cost_per_million,
            "max_response_bytes": settings.max_response_bytes,
            "max_content_chars": settings.max_content_chars,
        }
        environment = {
            "python": platform.python_version(),
            "platform": platform.system(),
            "implementation": platform.python_implementation(),
        }
        code_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        report = report.model_copy(
            update={
                "dataset_release_id": published_anchor.release_id,
                "dataset_anchor_hash": sha256_text(
                    canonical_json(published_anchor.model_dump(mode="json"))
                ),
                "code_commit": code_commit,
                "prompt_hash": sha256_text(REAL_MODEL_SYSTEM_PROMPT),
                "configuration_hash": sha256_text(canonical_json(safe_configuration)),
                "environment_hash": sha256_text(canonical_json(environment)),
            }
        )
        json_path, markdown_path = write_real_model_report(report, args.output_dir)
    except (
        KeyError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
        RealModelConfigurationError,
    ) as exc:
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
