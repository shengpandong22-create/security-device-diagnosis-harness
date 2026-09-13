"""确定性内存指标 Adapter；不访问网络、不持久化原始事实。"""

from security_diagnosis_harness.domain.device_integration import DeviceAdapterErrorKind
from security_diagnosis_harness.ports.observability import MetricPoint


class InMemoryObservabilityAdapter:
    def __init__(self) -> None:
        self._points: list[MetricPoint] = []

    def record(self, point: MetricPoint) -> None:
        self._points.append(MetricPoint.model_validate(point.model_dump(mode="python")))

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
        """把既有设备观测契约归一为安全 MetricPoint。"""
        self.record(
            MetricPoint(
                name="adapter_call_latency_ms",
                value=duration_ms,
                labels={
                    "adapter": adapter_key,
                    "capability": capability,
                    "operation": operation,
                    "failure_kind": error_kind.value if error_kind else "none",
                },
            )
        )
        self.record(
            MetricPoint(
                name="tool_calls_succeeded" if ok else "tool_calls_failed",
                value=1,
                labels={"adapter": adapter_key, "capability": capability},
            )
        )

    def snapshot(self) -> tuple[MetricPoint, ...]:
        return tuple(self._points)
