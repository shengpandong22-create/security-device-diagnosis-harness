import pytest

from security_diagnosis_harness.adapters.device_gateway.simulator import (
    SimulatorBehavior,
    SimulatorDeviceGateway,
    SimulatorDirective,
    SimulatorScenario,
)
from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.bootstrap.container import CAMERA_CASES_DATA_PATH
from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
    DeviceCapability,
)
from security_diagnosis_harness.ports.device_gateway import DeviceGateway


def test_simulator_satisfies_device_gateway(static_gateway) -> None:
    gateway = SimulatorDeviceGateway(static_gateway, SimulatorScenario(scenario_id="ok"))
    assert isinstance(gateway, DeviceGateway)


def test_success_delegates_and_records_safe_trace(static_gateway) -> None:
    gateway = SimulatorDeviceGateway(static_gateway, SimulatorScenario(scenario_id="ok"))
    snapshot = gateway.query_status("camera-3f-001")
    assert snapshot.device_id == "camera-3f-001"
    assert gateway.call_count == 1
    trace = gateway.traces[0]
    assert trace.ok is True
    assert trace.capability is DeviceCapability.STATUS
    assert "camera-3f-001" not in trace.model_dump_json()


@pytest.mark.parametrize(
    ("behavior", "kind"),
    [
        (SimulatorBehavior.TIMEOUT, DeviceAdapterErrorKind.TIMEOUT),
        (SimulatorBehavior.AUTHENTICATION, DeviceAdapterErrorKind.AUTHENTICATION),
        (SimulatorBehavior.RATE_LIMITED, DeviceAdapterErrorKind.RATE_LIMITED),
        (SimulatorBehavior.UNAVAILABLE, DeviceAdapterErrorKind.UNAVAILABLE),
        (SimulatorBehavior.UNSUPPORTED_CAPABILITY, DeviceAdapterErrorKind.UNSUPPORTED_CAPABILITY),
        (SimulatorBehavior.INVALID_RESPONSE, DeviceAdapterErrorKind.INVALID_RESPONSE),
    ],
)
def test_failure_is_deterministic_and_does_not_call_delegate(
    static_gateway, behavior, kind
) -> None:
    gateway = SimulatorDeviceGateway(
        static_gateway,
        SimulatorScenario(
            scenario_id="failure",
            directives={
                "query_status": SimulatorDirective(behavior=behavior, simulated_latency_ms=123)
            },
        ),
    )
    with pytest.raises(DeviceAdapterError) as excinfo:
        gateway.query_status("camera-3f-001")
    assert excinfo.value.kind is kind
    assert gateway.traces[0].simulated_latency_ms == 123
    assert gateway.traces[0].ok is False


def test_same_scenario_replays_same_result(static_gateway) -> None:
    scenario = SimulatorScenario(
        scenario_id="timeout",
        directives={"query_status": SimulatorDirective(behavior="timeout")},
    )
    gateway = SimulatorDeviceGateway(static_gateway, scenario)
    for _ in range(2):
        with pytest.raises(DeviceAdapterError, match="kind=timeout"):
            gateway.query_status("camera-3f-001")
    assert [trace.error_kind for trace in gateway.traces] == [
        DeviceAdapterErrorKind.TIMEOUT,
        DeviceAdapterErrorKind.TIMEOUT,
    ]


def test_unconfigured_operation_defaults_to_success(static_gateway) -> None:
    gateway = SimulatorDeviceGateway(
        static_gateway,
        SimulatorScenario(
            scenario_id="partial",
            directives={"query_channel_snapshot": SimulatorDirective(behavior="timeout")},
        ),
    )
    assert gateway.query_status("camera-3f-001").device_id == "camera-3f-001"


def test_simulator_preserves_offline_device_fact() -> None:
    gateway = SimulatorDeviceGateway(
        StaticDeviceGateway(CAMERA_CASES_DATA_PATH),
        SimulatorScenario(scenario_id="offline-fact"),
    )
    assert gateway.query_status("cam-offline-01").online is False
