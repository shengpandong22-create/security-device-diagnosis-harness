"""Phase 9C-3 正式 Runtime 降级验收。"""

from security_diagnosis_harness.adapters.device_gateway.simulator import (
    SimulatorBehavior,
    SimulatorDeviceGateway,
    SimulatorDirective,
    SimulatorScenario,
)
from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.bootstrap.container import DEFAULT_DEVICE_DATA_PATH
from security_diagnosis_harness.config import RuntimeSettings
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.runtime import (
    build_default_runtime_asset,
    build_local_static_sample_authorization,
    build_runtime_container,
)


def _run(scenario: SimulatorScenario):
    simulator = SimulatorDeviceGateway(
        StaticDeviceGateway(DEFAULT_DEVICE_DATA_PATH), scenario
    )
    with build_runtime_container(
        RuntimeSettings(repository_mode="memory"),
        device_adapter=simulator,
        # 显式注入的 Adapter 必须显式提供授权会话；此处的 Adapter 包装的是
        # 本地静态样例，因此使用明确标记的本地静态样例授权。
        authorization=build_local_static_sample_authorization(
            assets=(build_default_runtime_asset(),)
        ),
    ) as runtime:
        case = runtime.service.create_diagnosis(
            "camera-3f-001", SecurityFaultType.CAMERA_BLACK_SCREEN, "probe"
        )
        result = runtime.service.run_diagnosis(case.diagnosis_id)
        evidence = runtime.service.list_evidence(case.diagnosis_id)
    return result, evidence, simulator


def test_partial_timeout_finishes_with_real_evidence_and_no_retry():
    result, evidence, simulator = _run(
        SimulatorScenario(
            scenario_id="partial-timeout",
            directives={
                "query_status": SimulatorDirective(behavior=SimulatorBehavior.TIMEOUT)
            },
        )
    )

    assert result.ok is True
    assert result.degraded is True
    assert "timeout" in result.failure_kinds
    assert evidence
    assert simulator.call_count == 6
    assert sum(trace.operation == "query_status" for trace in simulator.traces) == 1


def test_all_device_timeouts_keep_only_non_device_evidence_without_retry():
    operations = (
        "query_status",
        "search_alarm_events",
        "read_config_snapshot",
        "query_channel_snapshot",
        "query_stream_snapshot",
        "query_platform_pull_status",
    )
    result, evidence, simulator = _run(
        SimulatorScenario(
            scenario_id="all-timeout",
            directives={
                operation: SimulatorDirective(behavior=SimulatorBehavior.TIMEOUT)
                for operation in operations
            },
        )
    )

    assert result.ok is True
    assert result.status is SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
    assert result.failure_kinds == ["timeout"]
    assert evidence
    assert all(item.source.value != "device_gateway" for item in evidence)
    assert simulator.call_count == 6
    assert {trace.operation for trace in simulator.traces} == set(operations)
