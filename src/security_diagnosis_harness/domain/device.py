"""设备事实对象：设备主数据、状态快照、告警事件、配置快照。

这些对象只表达"设备侧事实"，不表达诊断结论。
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import new_id, utc_now

REDACTED_VALUE = "***REDACTED***"

_SENSITIVE_KEY_PATTERN = re.compile(
    r"password|passwd|pwd|token|secret|credential|access[_-]?key|private[_-]?key",
    re.IGNORECASE,
)


def is_sensitive_key(key: str) -> bool:
    """判断配置键名是否属于凭证类敏感字段。"""
    return _SENSITIVE_KEY_PATTERN.search(key) is not None


def redact_sensitive_values(config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """把配置中的凭证类字段替换为占位符，返回 (配置, 是否发生脱敏)。"""
    redacted: dict[str, Any] = {}
    changed = False
    for key, value in config.items():
        if is_sensitive_key(str(key)) and value not in (None, ""):
            redacted[key] = REDACTED_VALUE
            changed = True
        else:
            redacted[key] = value
    return redacted, changed


class DeviceType(StrEnum):
    """设备类型。"""

    CAMERA = "camera"
    NVR = "nvr"
    ACCESS_CONTROLLER = "access_controller"
    ALARM_PANEL = "alarm_panel"
    UNKNOWN = "unknown"


class StreamStatus(StrEnum):
    """码流状态。"""

    NORMAL = "normal"
    ABNORMAL = "abnormal"
    UNKNOWN = "unknown"


class RecordingStatus(StrEnum):
    """录像状态。"""

    RECORDING = "recording"
    STOPPED = "stopped"
    NOT_SUPPORTED = "not_supported"
    UNKNOWN = "unknown"


class AlarmSeverity(StrEnum):
    """告警级别。"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Device(BaseModel):
    """设备主数据。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    device_type: DeviceType = DeviceType.UNKNOWN
    vendor: str | None = None
    model: str | None = None
    location: str | None = None
    firmware_version: str | None = None


class DeviceSnapshot(BaseModel):
    """某一时刻的设备运行状态快照。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("snap"))
    device_id: str = Field(min_length=1)
    captured_at: datetime = Field(default_factory=utc_now)
    online: bool
    channel_online: bool | None = None
    stream_status: StreamStatus = StreamStatus.UNKNOWN
    recording_status: RecordingStatus = RecordingStatus.UNKNOWN
    source: str = "static_device_gateway"
    extra: dict[str, Any] = Field(default_factory=dict)


class DeviceAlarmEvent(BaseModel):
    """设备告警/事件记录。

    告警存在只构成根因候选，不等于根因成立。
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: new_id("evt"))
    device_id: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=utc_now)
    event_type: str = Field(min_length=1)
    severity: AlarmSeverity = AlarmSeverity.WARNING
    message: str = ""
    source: str = "static_device_gateway"
    extra: dict[str, Any] = Field(default_factory=dict)


class DeviceConfigSnapshot(BaseModel):
    """设备配置快照。

    凭证类字段在入库前被替换为占位符，真实密码/Token/secret 不得原样保存。
    """

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("cfg"))
    device_id: str = Field(min_length=1)
    captured_at: datetime = Field(default_factory=utc_now)
    enabled: bool = True
    encoding: str | None = None
    resolution: str | None = None
    frame_rate: int | None = None
    bitrate_kbps: int | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    redacted: bool = False
    source: str = "static_device_gateway"

    @model_validator(mode="after")
    def _redact_credentials(self) -> DeviceConfigSnapshot:
        cleaned, changed = redact_sensitive_values(self.config)
        if changed:
            self.config = cleaned
            self.redacted = True
        return self
