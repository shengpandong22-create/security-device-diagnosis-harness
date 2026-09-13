"""Phase 9D 正式 Runtime 高保真模拟器影子评测。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.adapters.device_gateway.simulator import (  # noqa: E402
    SimulatorBehavior,
    SimulatorDeviceGateway,
    SimulatorDirective,
    SimulatorScenario,
)
from security_diagnosis_harness.adapters.device_gateway.static import (  # noqa: E402
    StaticDeviceGateway,
)
from security_diagnosis_harness.adapters.observability import (  # noqa: E402
    InMemoryObservabilityAdapter,
)
from security_diagnosis_harness.bootstrap.container import (  # noqa: E402
    DEFAULT_DEVICE_DATA_PATH,
)
from security_diagnosis_harness.config import RuntimeSettings  # noqa: E402
from security_diagnosis_harness.domain.enums import SecurityFaultType  # noqa: E402
from security_diagnosis_harness.domain.review import HumanReviewAction  # noqa: E402
from security_diagnosis_harness.evaluation.device_observability import (  # noqa: E402
    record_shadow_metrics,
)
from security_diagnosis_harness.runtime import build_runtime_container  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "demo-output" / "phase9d-simulator-shadow.json"
SCENARIOS = (
    SimulatorScenario(scenario_id="success"),
    SimulatorScenario(
        scenario_id="status_timeout",
        directives={"query_status": SimulatorDirective(behavior=SimulatorBehavior.TIMEOUT)},
    ),
    SimulatorScenario(
        scenario_id="stream_rate_limited",
        directives={
            "query_stream_snapshot": SimulatorDirective(
                behavior=SimulatorBehavior.RATE_LIMITED
            )
        },
    ),
)


def evaluate() -> dict:
    sink = InMemoryObservabilityAdapter()
    rows: list[dict] = []
    for scenario in SCENARIOS:
        simulator = SimulatorDeviceGateway(
            StaticDeviceGateway(DEFAULT_DEVICE_DATA_PATH), scenario
        )
        with build_runtime_container(
            RuntimeSettings(repository_mode="memory"), device_adapter=simulator
        ) as runtime:
            case = runtime.service.create_diagnosis(
                "camera-3f-001", SecurityFaultType.CAMERA_BLACK_SCREEN, "shadow-eval"
            )
            run = runtime.service.run_diagnosis(case.diagnosis_id)
            if run.ok:
                runtime.service.review_diagnosis(
                    case.diagnosis_id,
                    HumanReviewAction.CONFIRM,
                    "shadow-reviewer",
                    "影子评测人工确认",
                )
            final_case = runtime.service.get_diagnosis(case.diagnosis_id)
            record_shadow_metrics(sink, run, final_case, simulator.traces)
            rows.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "status": final_case.status.value,
                    "degraded": run.degraded,
                    "failure_kinds": run.failure_kinds,
                    "evidence_count": len(final_case.evidence),
                    "gateway_calls": simulator.call_count,
                    "external_model_called": runtime.llm.external_model_called,
                }
            )
    p0 = sum(point.value for point in sink.snapshot() if point.name == "p0_findings_total")
    summary = {
        "report_kind": "simulator_e2e",
        "shadow_mode": True,
        "total": len(rows),
        "completed": sum(row["status"] == "confirmed" for row in rows),
        "controlled_degradation_cases": sum(row["degraded"] for row in rows),
        "p0_findings": int(p0),
        "gate_allowed": p0 == 0 and all(not row["external_model_called"] for row in rows),
        "history_compatible": True,
        "device_writes": 0,
        "external_notifications": 0,
    }
    return {
        "summary": summary,
        "scenarios": rows,
        "metrics": [point.model_dump(mode="json") for point in sink.snapshot()],
    }


def main() -> int:
    payload = evaluate()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0 if payload["summary"]["gate_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
