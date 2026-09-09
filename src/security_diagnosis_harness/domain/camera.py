"""摄像头黑屏相关设备事实模型。

这些模型只表达"设备侧事实"，不表达诊断结论：

- `ChannelSnapshot`：通道在线、是否绑定、是否注册到平台；
- `StreamSnapshot`：主/子码流取流状态、编码、分辨率、帧率、码率、错误码；
- `PlatformPullStatus`：平台侧拉流状态、错误码、最近失败时间。

领域层只依赖标准库和 pydantic，不访问任何外部系统。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import new_id, utc_now
from security_diagnosis_harness.domain.device import redact_sensitive_values


class ChannelStatus(StrEnum):
    """通道状态。"""

    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class StreamKind(StrEnum):
    """码流类型。"""

    MAIN = "main"
    SUB = "sub"


class PullStatus(StrEnum):
    """取流/拉流状态。"""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


def _redact_extra(extra: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """extra 中的凭证类字段统一替换，返回 (extra, 是否发生脱敏)。"""
    return redact_sensitive_values(extra)


class ChannelSnapshot(BaseModel):
    """通道快照。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("chan"))
    device_id: str = Field(min_length=1)
    channel_id: str = "1"
    channel_status: ChannelStatus = ChannelStatus.UNKNOWN
    bound: bool | None = None
    platform_registered: bool | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_credentials(self) -> ChannelSnapshot:
        cleaned, changed = _redact_extra(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self


class StreamSnapshot(BaseModel):
    """码流快照。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("strm"))
    device_id: str = Field(min_length=1)
    stream_kind: StreamKind = StreamKind.MAIN
    pull_status: PullStatus = PullStatus.UNKNOWN
    encoding: str | None = None
    resolution: str | None = None
    frame_rate: int | None = None
    bitrate_kbps: int | None = None
    error_code: str | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_credentials(self) -> StreamSnapshot:
        cleaned, changed = _redact_extra(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self


class PlatformPullStatus(BaseModel):
    """平台侧拉流状态。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("pull"))
    device_id: str = Field(min_length=1)
    platform: str = "default-platform"
    pull_status: PullStatus = PullStatus.UNKNOWN
    error_code: str | None = None
    last_failed_at: datetime | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_credentials(self) -> PlatformPullStatus:
        cleaned, changed = _redact_extra(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self
