"""报警误报 / 误触发相关设备事实模型。

这些模型只表达"报警侧事实"，不表达诊断结论：

- `AlarmRuleSnapshot`：报警规则、灵敏度、阈值、防抖时间和布防时段；
- `AlarmSignalSnapshot`：触发时信号值、阈值、噪声等级和信号状态；
- `AlarmEnvironmentSnapshot`：雨、雾、强光、风、夜间、阴影等环境干扰；
- `AlarmVerificationSnapshot`：视频 / 人工复核是否发现真实目标；
- `AlarmCorrelationSnapshot`：短时间重复告警、相邻设备告警和关联模式。

领域层只依赖标准库和 pydantic，不访问 API、Tool、Runner、Adapter、
数据库、真实设备或真实模型。
"""

from __future__ import annotations

import re
from datetime import datetime, time
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import new_id, utc_now
from security_diagnosis_harness.domain.device import (
    REDACTED_VALUE,
    is_sensitive_key,
    redact_sensitive_values,
)

Weekday = Annotated[int, Field(ge=1, le=7)]
ALL_WEEKDAYS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)

LOW_RULE_THRESHOLD = 30.0
SHORT_DEBOUNCE_SECONDS = 3
HIGH_NOISE_LEVEL = 0.7
BURST_ALARM_COUNT = 5

_ALARM_SENSITIVE_KEY_PATTERN = re.compile(
    r"snapshot[_-]?url"
    r"|video[_-]?url"
    r"|image[_-]?url"
    r"|stream[_-]?url"
    r"|face[_-]?(?:id|feature|template)"
    r"|person[_-]?id"
    r"|card[_-]?(?:no|number|id)"
    r"|license[_-]?plate"
    r"|phone|mobile|id[_-]?card",
    re.IGNORECASE,
)


def is_alarm_sensitive_key(key: str) -> bool:
    """判断键名是否属于报警域敏感字段。"""
    return is_sensitive_key(key) or _ALARM_SENSITIVE_KEY_PATTERN.search(key) is not None


def _redact_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, dict):
        return redact_alarm_sensitive_values(value)
    if isinstance(value, list):
        changed = False
        items: list[Any] = []
        for item in value:
            cleaned, item_changed = _redact_value(item)
            items.append(cleaned)
            changed = changed or item_changed
        return items, changed
    return value, False


def redact_alarm_sensitive_values(values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """把报警领域敏感字段替换为占位符，返回 (替换后内容, 是否发生脱敏)。"""
    first_pass, changed = redact_sensitive_values(values)
    redacted: dict[str, Any] = {}
    for key, value in first_pass.items():
        if value in (None, ""):
            redacted[key] = value
        elif is_alarm_sensitive_key(str(key)):
            redacted[key] = REDACTED_VALUE
            changed = True
        else:
            cleaned, item_changed = _redact_value(value)
            redacted[key] = cleaned
            changed = changed or item_changed
    return redacted, changed


class AlarmType(StrEnum):
    """报警类型。"""

    MOTION = "motion"
    INTRUSION = "intrusion"
    LINE_CROSSING = "line_crossing"
    TAMPER = "tamper"
    ACCESS_ABNORMAL = "access_abnormal"
    UNKNOWN = "unknown"


class AlarmSeverityLevel(StrEnum):
    """报警等级。"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlarmRuleSensitivity(StrEnum):
    """报警规则灵敏度。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AlarmSignalStatus(StrEnum):
    """报警触发信号状态。"""

    STABLE = "stable"
    NOISY = "noisy"
    STUCK = "stuck"
    MISSING = "missing"
    UNKNOWN = "unknown"


class EnvironmentInterferenceType(StrEnum):
    """环境干扰类型。"""

    NONE = "none"
    RAIN = "rain"
    FOG = "fog"
    STRONG_LIGHT = "strong_light"
    WIND = "wind"
    NIGHT = "night"
    SHADOW = "shadow"
    UNKNOWN = "unknown"


class VerificationResult(StrEnum):
    """报警复核结果。"""

    TARGET_FOUND = "target_found"
    NO_TARGET_FOUND = "no_target_found"
    INCONCLUSIVE = "inconclusive"
    NOT_CHECKED = "not_checked"


class CorrelationPattern(StrEnum):
    """报警关联模式。"""

    ISOLATED = "isolated"
    BURST = "burst"
    MULTI_DEVICE = "multi_device"
    UNKNOWN = "unknown"


class AlarmTimeRange(BaseModel):
    """布防时间段。

    允许跨天，例如 22:00 -> 06:00；`start == end` 视为全天。
    """

    model_config = ConfigDict(extra="forbid")

    start: time
    end: time
    weekdays: list[Weekday] = Field(default_factory=lambda: list(ALL_WEEKDAYS))

    @property
    def crosses_midnight(self) -> bool:
        return self.start > self.end

    @property
    def is_all_day(self) -> bool:
        return self.start == self.end


class AlarmRuleSnapshot(BaseModel):
    """报警规则配置快照。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("arule"))
    device_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    alarm_type: AlarmType = AlarmType.UNKNOWN
    enabled: bool = True
    severity: AlarmSeverityLevel = AlarmSeverityLevel.WARNING
    sensitivity: AlarmRuleSensitivity = AlarmRuleSensitivity.MEDIUM
    threshold: float = Field(ge=0)
    debounce_seconds: int = Field(default=5, ge=0)
    armed_time_ranges: list[AlarmTimeRange] = Field(default_factory=list)
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_extra(self) -> AlarmRuleSnapshot:
        cleaned, changed = redact_alarm_sensitive_values(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @property
    def has_armed_schedule(self) -> bool:
        return bool(self.armed_time_ranges)

    @property
    def is_over_sensitive(self) -> bool:
        return (
            self.sensitivity is AlarmRuleSensitivity.HIGH
            or self.threshold <= LOW_RULE_THRESHOLD
            or self.debounce_seconds < SHORT_DEBOUNCE_SECONDS
        )


class AlarmSignalSnapshot(BaseModel):
    """报警触发信号快照。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("asig"))
    device_id: str = Field(min_length=1)
    alarm_id: str = Field(min_length=1)
    sensor_id: str = Field(min_length=1)
    signal_value: float = Field(ge=0)
    threshold: float = Field(ge=0)
    noise_level: float = Field(default=0.0, ge=0, le=1)
    status: AlarmSignalStatus = AlarmSignalStatus.UNKNOWN
    triggered_at: datetime = Field(default_factory=utc_now)
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_extra(self) -> AlarmSignalSnapshot:
        cleaned, changed = redact_alarm_sensitive_values(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @property
    def exceeds_threshold(self) -> bool:
        return self.signal_value >= self.threshold

    @property
    def is_noisy(self) -> bool:
        return self.status in {
            AlarmSignalStatus.NOISY,
            AlarmSignalStatus.STUCK,
            AlarmSignalStatus.MISSING,
        } or self.noise_level >= HIGH_NOISE_LEVEL


class AlarmEnvironmentSnapshot(BaseModel):
    """报警发生时的环境事实快照。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("aenv"))
    device_id: str = Field(min_length=1)
    alarm_id: str = Field(min_length=1)
    interference_types: list[EnvironmentInterferenceType] = Field(default_factory=list)
    visibility: str | None = None
    illumination_lux: float | None = Field(default=None, ge=0)
    wind_speed: float | None = Field(default=None, ge=0)
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_extra(self) -> AlarmEnvironmentSnapshot:
        cleaned, changed = redact_alarm_sensitive_values(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @property
    def has_interference(self) -> bool:
        return any(item is not EnvironmentInterferenceType.NONE for item in self.interference_types)


class AlarmVerificationSnapshot(BaseModel):
    """报警复核结果快照。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("aver"))
    device_id: str = Field(min_length=1)
    alarm_id: str = Field(min_length=1)
    result: VerificationResult = VerificationResult.NOT_CHECKED
    target_count: int = Field(default=0, ge=0)
    checked_by: str = "system"
    checked_at: datetime | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_extra(self) -> AlarmVerificationSnapshot:
        cleaned, changed = redact_alarm_sensitive_values(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @property
    def indicates_false_alarm(self) -> bool:
        return self.result is VerificationResult.NO_TARGET_FOUND and self.target_count == 0


class AlarmCorrelationSnapshot(BaseModel):
    """重复 / 关联告警快照。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("acor"))
    device_id: str = Field(min_length=1)
    alarm_id: str = Field(min_length=1)
    repeated_count: int = Field(default=0, ge=0)
    neighbor_alarm_count: int = Field(default=0, ge=0)
    window_seconds: int = Field(default=300, ge=0)
    pattern: CorrelationPattern = CorrelationPattern.UNKNOWN
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_extra(self) -> AlarmCorrelationSnapshot:
        cleaned, changed = redact_alarm_sensitive_values(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @property
    def is_burst(self) -> bool:
        return self.pattern is CorrelationPattern.BURST or self.repeated_count >= BURST_ALARM_COUNT

    @property
    def has_neighbor_correlation(self) -> bool:
        return (
            self.pattern is CorrelationPattern.MULTI_DEVICE or self.neighbor_alarm_count > 0
        )
