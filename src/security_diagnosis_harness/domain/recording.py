"""录像缺失 / 录像异常相关设备事实模型。

这些模型只表达"录像侧事实"，不表达诊断结论：

- `RecordingPlanSnapshot`：录像计划是否启用、录像模式、计划时间段、保留天数；
- `RecordingTimeRange`：录像计划的单个时间段，支持跨天；
- `StorageSnapshot`：录像存储池状态、容量、错误信息；
- `PlaybackCheckResult`：指定时间段是否存在录像、是否可回放、失败原因。

领域层只依赖标准库和 pydantic，不访问任何外部系统。
"""

from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import new_id, utc_now
from security_diagnosis_harness.domain.device import redact_sensitive_values

# 剩余容量低于该百分比即视为容量不足。
LOW_FREE_PERCENT_THRESHOLD: float = 10.0

# 星期取值：1=周一 ... 7=周日。
Weekday = Annotated[int, Field(ge=1, le=7)]
ALL_WEEKDAYS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)


class RecordingPlanStatus(StrEnum):
    """录像计划状态。"""

    ENABLED = "enabled"
    DISABLED = "disabled"
    MISCONFIGURED = "misconfigured"


class RecordingMode(StrEnum):
    """录像模式。"""

    CONTINUOUS = "continuous"
    EVENT_TRIGGERED = "event_triggered"
    MANUAL = "manual"


class StorageStatus(StrEnum):
    """录像存储状态。"""

    NORMAL = "normal"
    FULL = "full"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class PlaybackStatus(StrEnum):
    """指定时间段录像的回放检查结果。"""

    AVAILABLE = "available"
    MISSING = "missing"
    CORRUPTED = "corrupted"
    INDEX_MISSING = "index_missing"


# 各回放状态对应的可回放性，用于校验 status 与 playable 是否自相矛盾。
EXPECTED_PLAYABLE_BY_STATUS: dict[PlaybackStatus, bool] = {
    PlaybackStatus.AVAILABLE: True,
    PlaybackStatus.MISSING: False,
    PlaybackStatus.CORRUPTED: False,
    PlaybackStatus.INDEX_MISSING: False,
}


def _redact_extra(extra: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """extra 中的凭证类字段统一替换，返回 (extra, 是否发生脱敏)。"""
    return redact_sensitive_values(extra)


class RecordingTimeRange(BaseModel):
    """录像计划的单个时间段。

    允许跨天，例如 22:00 -> 06:00；`start == end` 视为全天，不算跨天。
    """

    model_config = ConfigDict(extra="forbid")

    start: time
    end: time
    weekdays: list[Weekday] = Field(default_factory=lambda: list(ALL_WEEKDAYS))

    @property
    def crosses_midnight(self) -> bool:
        """是否为跨天时间段。"""
        return self.start > self.end

    @property
    def is_all_day(self) -> bool:
        """是否为全天时间段。"""
        return self.start == self.end


class RecordingPlanSnapshot(BaseModel):
    """录像计划快照。

    `time_ranges` 允许为空；`status=enabled` 但时间段为空时，
    通过 `has_schedule_gap` 表达"计划启用但无有效时间段"的异常状态。
    """

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("rplan"))
    device_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    status: RecordingPlanStatus
    mode: RecordingMode
    time_ranges: list[RecordingTimeRange] = Field(default_factory=list)
    retention_days: int | None = Field(default=None, ge=0)
    last_updated_at: datetime | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_credentials(self) -> RecordingPlanSnapshot:
        cleaned, changed = _redact_extra(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @property
    def has_time_ranges(self) -> bool:
        """是否配置了时间段。"""
        return bool(self.time_ranges)

    @property
    def is_plan_active(self) -> bool:
        """计划是否真正生效：启用且有时间段。"""
        return self.status is RecordingPlanStatus.ENABLED and self.has_time_ranges

    @property
    def has_schedule_gap(self) -> bool:
        """计划启用但没有有效时间段：看似配置正常，实际不会录像。"""
        return self.status is RecordingPlanStatus.ENABLED and not self.has_time_ranges

    @property
    def crossing_time_ranges(self) -> list[RecordingTimeRange]:
        """其中跨天的时间段，便于排查"边界时间段没有录像"。 """
        return [item for item in self.time_ranges if item.crosses_midnight]


class StorageSnapshot(BaseModel):
    """录像存储快照。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("stor"))
    storage_id: str = Field(min_length=1)
    status: StorageStatus
    total_gb: float | None = Field(default=None, ge=0)
    free_gb: float | None = Field(default=None, ge=0)
    used_percent: float | None = Field(default=None, ge=0, le=100)
    last_error: str | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_credentials(self) -> StorageSnapshot:
        cleaned, changed = _redact_extra(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @property
    def free_percent(self) -> float | None:
        """剩余容量百分比；信息不足时返回 None。"""
        if self.used_percent is not None:
            return round(100.0 - self.used_percent, 4)
        if self.total_gb and self.free_gb is not None:
            return round(self.free_gb / self.total_gb * 100, 4)
        return None

    @property
    def is_capacity_low(self) -> bool:
        """容量是否不足：状态为 full，或剩余比例低于阈值。"""
        if self.status is StorageStatus.FULL:
            return True
        free_percent = self.free_percent
        if free_percent is None:
            return False
        return free_percent < LOW_FREE_PERCENT_THRESHOLD

    @property
    def is_available(self) -> bool:
        """存储池是否可用：正常且容量充足。"""
        return self.status is StorageStatus.NORMAL and not self.is_capacity_low


class PlaybackCheckResult(BaseModel):
    """指定时间段的录像回放检查结果。"""

    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(default_factory=lambda: new_id("pbk"))
    device_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    start_at: datetime
    end_at: datetime
    status: PlaybackStatus
    file_count: int = Field(default=0, ge=0)
    playable: bool
    failure_reason: str | None = None
    checked_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_credentials(self) -> PlaybackCheckResult:
        cleaned, changed = _redact_extra(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @model_validator(mode="after")
    def _validate_window_and_playable(self) -> PlaybackCheckResult:
        if self.end_at <= self.start_at:
            raise ValueError(
                f"end_at 必须大于 start_at，当前 start_at={self.start_at.isoformat()}，"
                f"end_at={self.end_at.isoformat()}"
            )
        expected = EXPECTED_PLAYABLE_BY_STATUS[self.status]
        if self.playable is not expected:
            raise ValueError(
                f"status={self.status.value} 时 playable 应为 {expected}，当前为 {self.playable}"
            )
        return self

    @property
    def has_files(self) -> bool:
        """该时间段是否检索到录像文件。"""
        return self.file_count > 0

    @property
    def is_missing(self) -> bool:
        """录像是否缺失。"""
        return self.status is PlaybackStatus.MISSING
