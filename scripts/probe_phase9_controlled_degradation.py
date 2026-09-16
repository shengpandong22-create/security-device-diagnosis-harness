"""Phase 9C-3 确定性受控降级探针。"""

from __future__ import annotations

import json

from security_diagnosis_harness.adapters.device_gateway.simulator import (
    SimulatorBehavior,
    SimulatorDeviceGateway,
    SimulatorDirective,
    SimulatorScenario,
)
from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.bootstrap.container import DEFAULT_DEVICE_DATA_PATH
from security_diagnosis_harness.config import RuntimeSettings
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.runtime import (
    build_default_runtime_asset,
    build_local_static_sample_authorization,
    build_runtime_container,
)


def main() -> int:
    simulator = SimulatorDeviceGateway(
        StaticDeviceGateway(DEFAULT_DEVICE_DATA_PATH),
        SimulatorScenario(
            scenario_id="phase9-controlled-degradation",
            directives={
                "query_status": SimulatorDirective(behavior=SimulatorBehavior.TIMEOUT)
            },
        ),
    )
    with build_runtime_container(
        RuntimeSettings(repository_mode="memory"),
        device_adapter=simulator,
        # 显式注入的 Adapter 必须显式提供授权会话（本地静态样例包装）。
        authorization=build_local_static_sample_authorization(
            assets=(build_default_runtime_asset(),)
        ),
    ) as runtime:
        case = runtime.service.create_diagnosis(
            "camera-3f-001", SecurityFaultType.CAMERA_BLACK_SCREEN, "probe"
        )
        result = runtime.service.run_diagnosis(case.diagnosis_id)

    output = {
        "degraded": result.degraded,
        "failure_kinds": result.failure_kinds,
        "partial_evidence_preserved": result.evidence_count > 0,
        "failed_operation_called_once": sum(
            trace.operation == "query_status" for trace in simulator.traces
        )
        == 1,
        "external_access": False,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    checks = (
        output["degraded"],
        output["partial_evidence_preserved"],
        output["failed_operation_called_once"],
    )
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
