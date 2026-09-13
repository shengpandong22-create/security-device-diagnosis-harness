"""低基数、无敏感信息的运行指标契约。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.device_integration import DeviceAdapterErrorKind
from security_diagnosis_harness.domain.redaction import redact_mapping

ALLOWED_METRIC_LABELS = frozenset(
    {"adapter", "capability", "failure_kind", "fault_type", "operation", "status"}
)


class MetricPoint(BaseModel):
    """单个聚合指标；标签不得包含标识、地址或自由文本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value: float = Field(ge=0)
    labels: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_safe_labels(self) -> MetricPoint:
        if not set(self.labels) <= ALLOWED_METRIC_LABELS:
            raise ValueError("指标包含未允许的标签")
        redacted, changed = redact_mapping(self.labels)
        if changed or redacted != self.labels:
            raise ValueError("指标标签不得包含敏感信息")
        if any(len(value) > 64 for value in self.labels.values()):
            raise ValueError("指标标签值过长")
        return self


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
        """兼容 Phase 9A 契约的设备调用入口。"""
        ...

    def record(self, point: MetricPoint) -> None: ...

    def snapshot(self) -> tuple[MetricPoint, ...]: ...
