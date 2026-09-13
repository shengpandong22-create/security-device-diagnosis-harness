"""确定性高保真 DeviceGateway 模拟器。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.camera import StreamKind
from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
    DeviceCapability,
)
from security_diagnosis_harness.ports.device_gateway import DeviceGateway

T = TypeVar("T")


class SimulatorBehavior(StrEnum):
    """单次操作的确定性行为。"""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INVALID_RESPONSE = "invalid_response"


_ERROR_KIND: dict[SimulatorBehavior, DeviceAdapterErrorKind] = {
    SimulatorBehavior.TIMEOUT: DeviceAdapterErrorKind.TIMEOUT,
    SimulatorBehavior.AUTHENTICATION: DeviceAdapterErrorKind.AUTHENTICATION,
    SimulatorBehavior.RATE_LIMITED: DeviceAdapterErrorKind.RATE_LIMITED,
    SimulatorBehavior.UNAVAILABLE: DeviceAdapterErrorKind.UNAVAILABLE,
    SimulatorBehavior.UNSUPPORTED_CAPABILITY: DeviceAdapterErrorKind.UNSUPPORTED_CAPABILITY,
    SimulatorBehavior.INVALID_RESPONSE: DeviceAdapterErrorKind.INVALID_RESPONSE,
}


class SimulatorDirective(BaseModel):
    """按 operation 配置的非随机故障指令。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    behavior: SimulatorBehavior = SimulatorBehavior.SUCCESS
    simulated_latency_ms: int = Field(default=0, ge=0, le=60_000)


class SimulatorScenario(BaseModel):
    """一个可复现的模拟场景。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)
    directives: dict[str, SimulatorDirective] = Field(default_factory=dict)


class SimulatorCallTrace(BaseModel):
    """不含设备 ID、参数、响应和凭证的低基数调用轨迹。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    operation: str
    capability: DeviceCapability
    ok: bool
    simulated_latency_ms: int = Field(ge=0)
    error_kind: DeviceAdapterErrorKind | None = None


class SimulatorDeviceGateway:
    """包装任意只读 Gateway，并按场景确定性注入失败。"""

    def __init__(self, delegate: DeviceGateway, scenario: SimulatorScenario) -> None:
        self._delegate = delegate
        self._scenario = scenario
        self._traces: list[SimulatorCallTrace] = []

    @property
    def scenario_id(self) -> str:
        return self._scenario.scenario_id

    @property
    def traces(self) -> list[SimulatorCallTrace]:
        return list(self._traces)

    @property
    def call_count(self) -> int:
        return len(self._traces)

    def _call(
        self,
        operation: str,
        capability: DeviceCapability,
        callback: Callable[[], T],
    ) -> T:
        directive = self._scenario.directives.get(operation, SimulatorDirective())
        error_kind = _ERROR_KIND.get(directive.behavior)
        ok = error_kind is None
        self._traces.append(
            SimulatorCallTrace(
                sequence=len(self._traces) + 1,
                operation=operation,
                capability=capability,
                ok=ok,
                simulated_latency_ms=directive.simulated_latency_ms,
                error_kind=error_kind,
            )
        )
        if error_kind is not None:
            raise DeviceAdapterError(error_kind, operation)
        return callback()

    def query_status(self, device_id: str) -> Any:
        return self._call(
            "query_status", DeviceCapability.STATUS, lambda: self._delegate.query_status(device_id)
        )

    def query_channel_snapshot(self, device_id: str) -> Any:
        return self._call(
            "query_channel_snapshot",
            DeviceCapability.CHANNEL,
            lambda: self._delegate.query_channel_snapshot(device_id),
        )

    def query_stream_snapshot(
        self, device_id: str, stream_kind: StreamKind = StreamKind.MAIN
    ) -> Any:
        return self._call(
            "query_stream_snapshot",
            DeviceCapability.STREAM,
            lambda: self._delegate.query_stream_snapshot(device_id, stream_kind),
        )

    def query_platform_pull_status(self, device_id: str) -> Any:
        return self._call(
            "query_platform_pull_status",
            DeviceCapability.STREAM,
            lambda: self._delegate.query_platform_pull_status(device_id),
        )

    def query_recording_plan(self, device_id: str, channel_id: str) -> Any:
        return self._call(
            "query_recording_plan",
            DeviceCapability.RECORDING,
            lambda: self._delegate.query_recording_plan(device_id, channel_id),
        )

    def query_storage_status(self, device_id: str, channel_id: str) -> Any:
        return self._call(
            "query_storage_status",
            DeviceCapability.RECORDING,
            lambda: self._delegate.query_storage_status(device_id, channel_id),
        )

    def check_recording_playback(
        self, device_id: str, channel_id: str, start_at: datetime, end_at: datetime
    ) -> Any:
        return self._call(
            "check_recording_playback",
            DeviceCapability.RECORDING,
            lambda: self._delegate.check_recording_playback(
                device_id, channel_id, start_at, end_at
            ),
        )

    def query_access_controller(self, device_id: str) -> Any:
        return self._call(
            "query_access_controller",
            DeviceCapability.ACCESS,
            lambda: self._delegate.query_access_controller(device_id),
        )

    def query_door(self, device_id: str, door_id: str) -> Any:
        return self._call(
            "query_door",
            DeviceCapability.ACCESS,
            lambda: self._delegate.query_door(device_id, door_id),
        )

    def query_credential(self, credential_id: str) -> Any:
        return self._call(
            "query_credential",
            DeviceCapability.ACCESS,
            lambda: self._delegate.query_credential(credential_id),
        )

    def query_access_policy(self, person_id: str, door_id: str) -> Any:
        return self._call(
            "query_access_policy",
            DeviceCapability.ACCESS,
            lambda: self._delegate.query_access_policy(person_id, door_id),
        )

    def search_access_events(
        self, device_id: str, door_id: str, credential_id: str, limit: int = 10
    ) -> Any:
        return self._call(
            "search_access_events",
            DeviceCapability.ACCESS,
            lambda: self._delegate.search_access_events(device_id, door_id, credential_id, limit),
        )

    def query_alarm_rule(self, device_id: str, rule_id: str) -> Any:
        return self._call(
            "query_alarm_rule",
            DeviceCapability.ALARM_DIAGNOSIS,
            lambda: self._delegate.query_alarm_rule(device_id, rule_id),
        )

    def query_alarm_signal(self, device_id: str, alarm_id: str) -> Any:
        return self._call(
            "query_alarm_signal",
            DeviceCapability.ALARM_DIAGNOSIS,
            lambda: self._delegate.query_alarm_signal(device_id, alarm_id),
        )

    def query_alarm_environment(self, device_id: str, alarm_id: str) -> Any:
        return self._call(
            "query_alarm_environment",
            DeviceCapability.ALARM_DIAGNOSIS,
            lambda: self._delegate.query_alarm_environment(device_id, alarm_id),
        )

    def query_alarm_verification(self, device_id: str, alarm_id: str) -> Any:
        return self._call(
            "query_alarm_verification",
            DeviceCapability.ALARM_DIAGNOSIS,
            lambda: self._delegate.query_alarm_verification(device_id, alarm_id),
        )

    def query_alarm_correlation(self, device_id: str, alarm_id: str) -> Any:
        return self._call(
            "query_alarm_correlation",
            DeviceCapability.ALARM_DIAGNOSIS,
            lambda: self._delegate.query_alarm_correlation(device_id, alarm_id),
        )

    def search_alarm_events(
        self, device_id: str, keyword: str | None = None, limit: int = 10
    ) -> Any:
        return self._call(
            "search_alarm_events",
            DeviceCapability.ALARM,
            lambda: self._delegate.search_alarm_events(device_id, keyword, limit),
        )

    def read_config_snapshot(self, device_id: str) -> Any:
        return self._call(
            "read_config_snapshot",
            DeviceCapability.CONFIG,
            lambda: self._delegate.read_config_snapshot(device_id),
        )
