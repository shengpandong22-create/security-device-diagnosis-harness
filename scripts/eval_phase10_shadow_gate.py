"""Phase 10A 影子历史与门禁固定脚本：baseline → candidate → gate。

全部历史数据只写入临时目录，不向仓库写入任何运行记录；不调用真实模型、
BGE、真实设备或外部网络。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_phase9d_simulator_shadow import shadow_run  # noqa: E402

from security_diagnosis_harness.evaluation import (  # noqa: E402
    JsonShadowHistory,
    ShadowGatePolicy,
    compare_shadow_runs,
)

BASELINE_COMMIT = "a" * 40
CANDIDATE_COMMIT = "b" * 40
BASELINE_RUN_ID = "phase10a-simulator-baseline"
CANDIDATE_RUN_ID = "phase10a-simulator-candidate"
CREATED_AT = datetime(2026, 9, 14, tzinfo=UTC)


def run_fixed_gate() -> dict:
    """在临时历史中完成一次 baseline→candidate→gate 固定闭环。"""
    baseline = shadow_run(
        run_id=BASELINE_RUN_ID, created_at=CREATED_AT, code_commit=BASELINE_COMMIT
    )
    candidate = shadow_run(
        run_id=CANDIDATE_RUN_ID,
        created_at=CREATED_AT + timedelta(seconds=1),
        code_commit=CANDIDATE_COMMIT,
    )
    with TemporaryDirectory(prefix="phase10a-shadow-history-") as directory:
        history = JsonShadowHistory(Path(directory) / "shadow-history.json")
        history.append(baseline)
        record = history.append(candidate, baseline=baseline, policy=ShadowGatePolicy())
        document = history.load()
    # 独立重算逐项 Gate，确认记录里的布尔值确实来自逐项比较。
    report = compare_shadow_runs(
        document.records[0].summary, document.records[-1].summary, ShadowGatePolicy()
    )
    return {
        "report_kind": record.summary.identity.report_kind,
        "adapter_kind": record.summary.identity.adapter_kind,
        "baseline_run_id": report.baseline_run_id,
        "candidate_run_id": report.candidate_run_id,
        "history_records": len(document.records),
        "metric_deltas": [item.model_dump(mode="json") for item in report.metric_deltas],
        "safety_checks": [item.model_dump(mode="json") for item in report.safety_checks],
        "blocking_reasons": list(report.blocking_reasons),
        "gate_allowed": report.allowed,
        "per_item_gate_matches_record": report.allowed == record.gate_allowed,
        "history_written_to_repository": False,
        "external_model_called": False,
    }


def main() -> int:
    payload = run_fixed_gate()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["gate_allowed"] and payload["per_item_gate_matches_record"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
