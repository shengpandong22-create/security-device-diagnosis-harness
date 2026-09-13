"""供应商无关的设备接入领域契约。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class DeviceCapability(StrEnum):
    """设备 Adapter 可提供的只读能力。"""

    STATUS = "status"
    CHANNEL = "channel"
    STREAM = "stream"
    ALARM = "alarm"
    CONFIG = "config"
    RECORDING = "recording"
    ACCESS = "access"
    ALARM_DIAGNOSIS = "alarm_diagnosis"


class DeviceAdapterErrorKind(StrEnum):
    """跨 Adapter 稳定错误分类，不暴露底层异常。"""

    AUTHENTICATION = "authentication"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INVALID_RESPONSE = "invalid_response"


class DeviceAsset(BaseModel):
    """资产目录中的非敏感设备描述。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: str = Field(min_length=1)
    device_type: str = Field(min_length=1)
    site_alias: str | None = None
    region_alias: str | None = None
    adapter_key: str = Field(min_length=1)
    capabilities: frozenset[DeviceCapability] = frozenset()
    enabled: bool = True


class DeviceConnectionProfile(BaseModel):
    """非敏感连接引用；不允许保存实际凭证或 URL userinfo。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol: str = Field(min_length=1)
    endpoint_alias: str = Field(min_length=1)
    credential_reference: str = Field(min_length=1)
    connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    read_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    @field_validator("endpoint_alias")
    @classmethod
    def _reject_endpoint_credentials(cls, value: str) -> str:
        lowered = value.lower()
        if (
            "://" in value
            or "@" in value
            or any(token in lowered for token in ("token=", "password=", "secret="))
        ):
            raise ValueError("endpoint_alias 只能是受控别名，不能包含 URL 或凭证")
        return value


class DeviceRequestContext(BaseModel):
    """一次只读设备调用的追踪与预算上下文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    diagnosis_id: str = Field(min_length=1)
    deadline: datetime
    source: str = Field(min_length=1)
    permissions: frozenset[str] = frozenset()

    @field_validator("deadline")
    @classmethod
    def _require_aware_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline 必须包含时区")
        return value


class ResolvedCredential(BaseModel):
    """只在 Adapter 边界短暂持有、默认不可打印的凭证。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: SecretStr


class DeviceAdapterError(Exception):
    """可安全跨边界传递的设备 Adapter 错误。"""

    def __init__(self, kind: DeviceAdapterErrorKind, operation: str) -> None:
        self.kind = kind
        self.operation = operation if operation.replace("_", "").isalnum() else "unknown"
        super().__init__(f"设备只读操作失败: kind={kind.value}, operation={self.operation}")
