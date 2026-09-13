"""Phase 9A 高保真模拟器端到端固定评测。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.adapters.device_gateway.simulator import (  # noqa: E402
    SimulatorBehavior,
    SimulatorDirective,
    SimulatorScenario,
)
from security_diagnosis_harness.bootstrap.container import (  # noqa: E402
    CAMERA_CASES_DATA_PATH,
    build_phase9a_simulator_container,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType  # noqa: E402
from security_diagnosis_harness.domain.review import HumanReviewAction  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "demo-output" / "phase9a-simulator-e2e.json"

SCENARIOS = (
    SimulatorScenario(scenario_id="success"),
    SimulatorScenario(
        scenario_id="status_timeout",
        directives={
            "query_status": SimulatorDirective(
                behavior=SimulatorBehavior.TIMEOUT,
                simulated_latency_ms=3_000,
            )
        },
    ),
    SimulatorScenario(
        scenario_id="stream_invalid_response",
        directives={
            "query_stream_snapshot": SimulatorDirective(
                behavior=SimulatorBehavior.INVALID_RESPONSE,
                simulated_latency_ms=25,
            )
        },
    ),
)


def evaluate() -> dict:
    """运行三种确定性场景，验证完整受控闭环。"""
    rows: list[dict] = []
    for scenario in SCENARIOS:
        container = build_phase9a_simulator_container(scenario, CAMERA_CASES_DATA_PATH)
        ref = container.static_gateway.list_cases()[0]
        case = container.service.create_diagnosis(
            device_id=ref.device_id,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="phase9a-eval",
            description="高保真模拟器端到端诊断",
        )
        run = container.service.run_diagnosis(case.diagnosis_id)
        if run.ok:
            container.service.review_diagnosis(
                case.diagnosis_id,
                HumanReviewAction.CONFIRM,
                "phase9a-human-review",
                "模拟评测人工确认",
            )
        final_case = container.service.get_diagnosis(case.diagnosis_id)
        failed_traces = [trace for trace in container.gateway.traces if not trace.ok]
        expected_evidence_count = 7 - len(failed_traces)
        rows.append(
            {
                "scenario_id": scenario.scenario_id,
                "run_ok": run.ok,
                "status": final_case.status.value,
                "tool_calls": run.tool_calls,
                "gateway_calls": container.gateway.call_count,
                "evidence_count": len(final_case.evidence),
                "failed_gateway_calls": len(failed_traces),
                "failed_result_evidence_count": max(
                    0, len(final_case.evidence) - expected_evidence_count
                ),
                "external_model_called": container.external_model_called,
            }
        )
    summary = {
        "report_kind": "simulator_e2e",
        "total": len(rows),
        "completed": sum(row["run_ok"] for row in rows),
        "controlled_failure_scenarios": sum(row["failed_gateway_calls"] > 0 for row in rows),
        "failed_result_evidence_violations": sum(
            row["failed_result_evidence_count"] for row in rows
        ),
        "external_model_called": any(row["external_model_called"] for row in rows),
    }
    return {"summary": summary, "scenarios": rows}


def main() -> int:
    payload = evaluate()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    summary = payload["summary"]
    return (
        0
        if summary["completed"] == summary["total"] and not summary["external_model_called"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
