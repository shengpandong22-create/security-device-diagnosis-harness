"""低基数、无敏感信息的设备调用观测 Port。"""

from typing import Protocol, runtime_checkable

from security_diagnosis_harness.domain.device_integration import DeviceAdapterErrorKind


@runtime_checkable
class ObservabilityPort(Protocol):
    def record_device_call(
        self,
        *,
        adapter_key: str,
        capability: str,
        operation: str,
        ok: bool,
        duration_ms: int,
        error_kind: DeviceAdapterErrorKind | None = None,
    ) -> None:
        """记录一次不含资产标识、地址和自由文本的设备调用。"""
        ...

