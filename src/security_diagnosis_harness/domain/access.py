"""门禁刷卡异常相关设备事实模型。

这些模型只表达"门禁侧事实"，不表达诊断结论：

- `AccessControllerSnapshot`：控制器在线状态、健康状态、最近心跳与错误；
- `DoorSnapshot`：门当前状态、门锁状态、门磁 / 门锁错误；
- `CredentialSnapshot`：凭证类型、凭证有效性、过期时间；
- `AccessPolicySnapshot`：是否有门权限、授权有效期、授权时间段；
- `AccessEvent`：一次刷卡 / 人脸 / 二维码 / 指纹通行事件与拒绝原因。

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

# 星期取值：1=周一 ... 7=周日。
Weekday = Annotated[int, Field(ge=1, le=7)]
ALL_WEEKDAYS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)

_ACCESS_SENSITIVE_KEY_PATTERN = re.compile(
    r"card[_-]?(?:no|number|id)"
    r"|face[_-]?(?:id|feature|template)"
    r"|finger[_-]?(?:print|template)"
    r"|person[_-]?id"
    r"|id[_-]?card"
    r"|phone|mobile|pin",
    re.IGNORECASE,
)


def is_access_sensitive_key(key: str) -> bool:
    """判断键名是否属于门禁领域的个人标识或凭证类敏感字段。"""
    return is_sensitive_key(key) or _ACCESS_SENSITIVE_KEY_PATTERN.search(key) is not None


def _redact_value(value: Any) -> tuple[Any, bool]:
    """递归脱敏 dict/list 中的门禁敏感字段。"""
    if isinstance(value, dict):
        return redact_access_sensitive_values(value)
    if isinstance(value, list):
        changed = False
        items: list[Any] = []
        for item in value:
            cleaned, item_changed = _redact_value(item)
            items.append(cleaned)
            changed = changed or item_changed
        return items, changed
    return value, False


def redact_access_sensitive_values(values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """把门禁领域敏感字段替换为占位符，返回 (替换后内容, 是否发生脱敏)。"""
    first_pass, changed = redact_sensitive_values(values)
    redacted: dict[str, Any] = {}
    for key, value in first_pass.items():
        if value in (None, ""):
            redacted[key] = value
        elif is_access_sensitive_key(str(key)):
            redacted[key] = REDACTED_VALUE
            changed = True
        else:
            cleaned, item_changed = _redact_value(value)
            redacted[key] = cleaned
            changed = changed or item_changed
    return redacted, changed


class AccessControllerStatus(StrEnum):
    """门禁控制器在线状态。"""

    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class AccessControllerHealth(StrEnum):
    """门禁控制器健康状态。"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    ERROR = "error"
    UNKNOWN = "unknown"


class DoorStatus(StrEnum):
    """门当前开合状态。"""

    CLOSED = "closed"
    OPEN = "open"
    FORCED_OPEN = "forced_open"
    HELD_OPEN = "held_open"
    UNKNOWN = "unknown"


class DoorLockStatus(StrEnum):
    """门锁状态。"""

    LOCKED = "locked"
    UNLOCKED = "unlocked"
    JAMMED = "jammed"
    UNKNOWN = "unknown"


class CredentialType(StrEnum):
    """凭证类型。"""

    CARD = "card"
    FACE = "face"
    QR_CODE = "qr_code"
    FINGERPRINT = "fingerprint"
    UNKNOWN = "unknown"


class CredentialStatus(StrEnum):
    """凭证有效性状态。"""

    ACTIVE = "active"
    FROZEN = "frozen"
    EXPIRED = "expired"
    LOST = "lost"
    UNKNOWN = "unknown"


class AccessDecision(StrEnum):
    """一次门禁通行请求的处理结果。"""

    GRANTED = "granted"
    DENIED = "denied"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class AccessDenyReason(StrEnum):
    """常见刷卡拒绝 / 失败原因。"""

    PERMISSION_DENIED = "permission_denied"
    EXPIRED_CREDENTIAL = "expired_credential"
    FROZEN_CREDENTIAL = "frozen_credential"
    TIME_WINDOW_DENIED = "time_window_denied"
    CONTROLLER_OFFLINE = "controller_offline"
    CONTROLLER_TIMEOUT = "controller_timeout"
    DOOR_LOCK_ERROR = "door_lock_error"
    UNKNOWN = "unknown"


class AccessTimeRange(BaseModel):
    """授权时间段。

    允许跨天，例如 22:00 -> 06:00；`start == end` 视为全天，不算跨天。
    """

    model_config = ConfigDict(extra="forbid")

    start: time
    end: time
    weekdays: list[Weekday] = Field(default_factory=lambda: list(ALL_WEEKDAYS))

    @property
    def crosses_midnight(self) -> bool:
        """是否为跨天授权时间段。"""
        return self.start > self.end

    @property
    def is_all_day(self) -> bool:
        """是否为全天授权。"""
        return self.start == self.end


class AccessControllerSnapshot(BaseModel):
    """门禁控制器运行状态快照。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("actrl"))
    device_id: str = Field(min_length=1)
    controller_id: str = Field(min_length=1)
    status: AccessControllerStatus = AccessControllerStatus.UNKNOWN
    health: AccessControllerHealth = AccessControllerHealth.UNKNOWN
    last_seen_at: datetime | None = None
    last_error: str | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_extra(self) -> AccessControllerSnapshot:
        cleaned, changed = redact_access_sensitive_values(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self


class DoorSnapshot(BaseModel):
    """门状态与门锁状态快照。"""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("door"))
    device_id: str = Field(min_length=1)
    door_id: str = Field(min_length=1)
    controller_id: str | None = Field(default=None, min_length=1)
    door_status: DoorStatus = DoorStatus.UNKNOWN
    lock_status: DoorLockStatus = DoorLockStatus.UNKNOWN
    last_error: str | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_extra(self) -> DoorSnapshot:
        cleaned, changed = redact_access_sensitive_values(self.extra)
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @property
    def has_lock_error(self) -> bool:
        """门锁是否处于异常状态。"""
        return self.lock_status is DoorLockStatus.JAMMED


class CredentialSnapshot(BaseModel):
    """凭证状态快照。

    `credential_id` 是个人 / 凭证标识，构造时要求非空，但保存到模型中会脱敏。
    """

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("cred"))
    device_id: str = Field(min_length=1)
    credential_id: str = Field(min_length=1)
    credential_type: CredentialType = CredentialType.UNKNOWN
    status: CredentialStatus = CredentialStatus.UNKNOWN
    expires_at: datetime | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_sensitive_fields(self) -> CredentialSnapshot:
        cleaned, changed = redact_access_sensitive_values(self.extra)
        if self.credential_id != REDACTED_VALUE:
            self.credential_id = REDACTED_VALUE
            changed = True
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @property
    def is_valid(self) -> bool:
        """凭证当前是否可用。"""
        return self.status is CredentialStatus.ACTIVE


class AccessPolicySnapshot(BaseModel):
    """门禁授权策略快照。

    `person_id` 与 `credential_id` 都是敏感标识，构造时要求非空，
    保存到模型中统一脱敏。
    """

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(default_factory=lambda: new_id("apol"))
    device_id: str = Field(min_length=1)
    door_id: str = Field(min_length=1)
    person_id: str = Field(min_length=1)
    credential_id: str = Field(min_length=1)
    allowed: bool
    time_ranges: list[AccessTimeRange] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_sensitive_fields(self) -> AccessPolicySnapshot:
        cleaned, changed = redact_access_sensitive_values(self.extra)
        if self.person_id != REDACTED_VALUE:
            self.person_id = REDACTED_VALUE
            changed = True
        if self.credential_id != REDACTED_VALUE:
            self.credential_id = REDACTED_VALUE
            changed = True
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @property
    def has_time_ranges(self) -> bool:
        """是否配置授权时段。"""
        return bool(self.time_ranges)

    @property
    def crossing_time_ranges(self) -> list[AccessTimeRange]:
        """其中跨天的授权时间段。"""
        return [item for item in self.time_ranges if item.crosses_midnight]


class AccessEvent(BaseModel):
    """一次门禁通行事件。

    `decision=denied` 必须携带 `deny_reason`（原因未知时使用 `unknown`）；
    `decision=granted` 不允许携带 `deny_reason`，避免放行与拒绝原因自相矛盾。
    `credential_id` 与 `person_id` 是敏感标识，保存时统一脱敏。
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: new_id("aevt"))
    device_id: str = Field(min_length=1)
    door_id: str = Field(min_length=1)
    credential_id: str = Field(min_length=1)
    person_id: str | None = Field(default=None, min_length=1)
    credential_type: CredentialType = CredentialType.UNKNOWN
    decision: AccessDecision = AccessDecision.UNKNOWN
    deny_reason: AccessDenyReason | None = None
    occurred_at: datetime = Field(default_factory=utc_now)
    source: str = "static_device_gateway"
    redacted: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _redact_sensitive_fields(self) -> AccessEvent:
        cleaned, changed = redact_access_sensitive_values(self.extra)
        if self.credential_id != REDACTED_VALUE:
            self.credential_id = REDACTED_VALUE
            changed = True
        if self.person_id not in (None, REDACTED_VALUE):
            self.person_id = REDACTED_VALUE
            changed = True
        if changed:
            self.extra = cleaned
            self.redacted = True
        return self

    @model_validator(mode="after")
    def _validate_decision_and_reason(self) -> AccessEvent:
        if self.decision is AccessDecision.GRANTED and self.deny_reason is not None:
            raise ValueError("decision=granted 不允许携带 deny_reason")
        if self.decision is AccessDecision.DENIED and self.deny_reason is None:
            raise ValueError("decision=denied 必须携带 deny_reason")
        return self
