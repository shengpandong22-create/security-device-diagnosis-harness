"""严格只读的供应商无关 Security Platform HTTP Adapter。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.camera import (
    ChannelSnapshot,
    PlatformPullStatus,
    StreamKind,
    StreamSnapshot,
)
from security_diagnosis_harness.domain.device import (
    DeviceAlarmEvent,
    DeviceConfigSnapshot,
    DeviceSnapshot,
)
from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
)
from security_diagnosis_harness.ports.credentials import CredentialResolverPort


class SecurityPlatformHttpSettings(BaseModel):
    """HTTP Adapter 安全配置。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    allowed_hosts: frozenset[str]
    credential_reference: str = Field(min_length=1)
    connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    read_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    total_timeout_seconds: float = Field(default=15.0, gt=0, le=90)
    max_response_bytes: int = Field(default=256_000, ge=1_024, le=2_000_000)
    max_json_depth: int = Field(default=8, ge=1, le=20)
    max_list_items: int = Field(default=200, ge=1, le=2_000)

    @model_validator(mode="after")
    def _validate_endpoint(self) -> SecurityPlatformHttpSettings:
        parsed = urlsplit(self.base_url)
        host = (parsed.hostname or "").lower()
        localhost = host in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and localhost):
            raise ValueError("base_url 只允许 HTTPS 或本机 HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url 禁止 userinfo、查询参数和 fragment")
        if not host or host not in {item.lower() for item in self.allowed_hosts}:
            raise ValueError("base_url host 不在 allowlist")
        if any(
            token in self.credential_reference.lower() for token in ("=", "bearer ", "://", "@")
        ):
            raise ValueError("credential_reference 必须是安全存储引用，不能是实际凭证")
        return self


class SecurityPlatformHttpAdapter:
    """通过受控 endpoint 读取设备事实；不提供任何写操作。"""

    adapter_key = "security_platform_http"

    def __init__(
        self,
        settings: SecurityPlatformHttpSettings,
        credential_resolver: CredentialResolverPort,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._credential_resolver = credential_resolver
        timeout = httpx.Timeout(
            settings.total_timeout_seconds,
            connect=settings.connect_timeout_seconds,
            read=settings.read_timeout_seconds,
        )
        self._client = httpx.Client(
            base_url=settings.base_url.rstrip("/"),
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SecurityPlatformHttpAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, operation: str, path: str, params: dict[str, Any] | None = None) -> Any:
        credential = self._credential_resolver.resolve(self._settings.credential_reference)
        try:
            response = self._client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {credential.value.get_secret_value()}"},
            )
        except httpx.TimeoutException as exc:
            raise DeviceAdapterError(DeviceAdapterErrorKind.TIMEOUT, operation) from exc
        except httpx.RequestError as exc:
            raise DeviceAdapterError(DeviceAdapterErrorKind.UNAVAILABLE, operation) from exc
        finally:
            del credential

        kind = self._status_error_kind(response.status_code)
        if kind is not None:
            raise DeviceAdapterError(kind, operation)
        if response.is_redirect:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self._settings.max_response_bytes:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)
        if len(response.content) > self._settings.max_response_bytes:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)
        try:
            payload = response.json()
        except ValueError as exc:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation) from exc
        self._validate_json_shape(payload, depth=1)
        return payload

    @staticmethod
    def _segment(value: str) -> str:
        """把业务标识编码为单一路径段，禁止其改变受控路由。"""
        return quote(value, safe="")

    @staticmethod
    def _status_error_kind(status: int) -> DeviceAdapterErrorKind | None:
        if status in {401, 403}:
            return DeviceAdapterErrorKind.AUTHENTICATION
        if status in {408, 504}:
            return DeviceAdapterErrorKind.TIMEOUT
        if status == 429:
            return DeviceAdapterErrorKind.RATE_LIMITED
        if status >= 500:
            return DeviceAdapterErrorKind.UNAVAILABLE
        if status >= 400:
            return DeviceAdapterErrorKind.INVALID_RESPONSE
        return None

    def _validate_json_shape(self, value: Any, *, depth: int) -> None:
        if depth > self._settings.max_json_depth:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, "json_shape")
        if isinstance(value, list):
            if len(value) > self._settings.max_list_items:
                raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, "json_shape")
            for item in value:
                self._validate_json_shape(item, depth=depth + 1)
        elif isinstance(value, dict):
            for item in value.values():
                self._validate_json_shape(item, depth=depth + 1)

    @staticmethod
    def _model(model_type: type[BaseModel], payload: Any, operation: str) -> Any:
        try:
            return model_type.model_validate(payload)
        except Exception as exc:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation) from exc

    def query_status(self, device_id: str) -> DeviceSnapshot:
        device = self._segment(device_id)
        return self._model(
            DeviceSnapshot,
            self._get("query_status", f"/v1/devices/{device}/status"),
            "query_status",
        )

    def query_channel_snapshot(self, device_id: str) -> ChannelSnapshot:
        device = self._segment(device_id)
        return self._model(
            ChannelSnapshot,
            self._get("query_channel_snapshot", f"/v1/devices/{device}/channel"),
            "query_channel_snapshot",
        )

    def query_stream_snapshot(
        self, device_id: str, stream_kind: StreamKind = StreamKind.MAIN
    ) -> StreamSnapshot:
        device = self._segment(device_id)
        return self._model(
            StreamSnapshot,
            self._get("query_stream_snapshot", f"/v1/devices/{device}/streams/{stream_kind.value}"),
            "query_stream_snapshot",
        )

    def query_platform_pull_status(self, device_id: str) -> PlatformPullStatus:
        device = self._segment(device_id)
        return self._model(
            PlatformPullStatus,
            self._get("query_platform_pull_status", f"/v1/devices/{device}/platform-pull"),
            "query_platform_pull_status",
        )

    def search_alarm_events(
        self, device_id: str, keyword: str | None = None, limit: int = 10
    ) -> list[DeviceAlarmEvent]:
        device = self._segment(device_id)
        payload = self._get(
            "search_alarm_events",
            f"/v1/devices/{device}/alarms",
            {"keyword": keyword, "limit": limit},
        )
        if not isinstance(payload, list):
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, "search_alarm_events")
        return [self._model(DeviceAlarmEvent, item, "search_alarm_events") for item in payload]

    def read_config_snapshot(self, device_id: str) -> DeviceConfigSnapshot:
        device = self._segment(device_id)
        return self._model(
            DeviceConfigSnapshot,
            self._get("read_config_snapshot", f"/v1/devices/{device}/config"),
            "read_config_snapshot",
        )

    @staticmethod
    def _unsupported(operation: str) -> Any:
        raise DeviceAdapterError(DeviceAdapterErrorKind.UNSUPPORTED_CAPABILITY, operation)

    def query_recording_plan(self, device_id: str, channel_id: str) -> Any:
        return self._unsupported("query_recording_plan")

    def query_storage_status(self, device_id: str, channel_id: str) -> Any:
        return self._unsupported("query_storage_status")

    def check_recording_playback(
        self,
        device_id: str,
        channel_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> Any:
        return self._unsupported("check_recording_playback")

    def query_access_controller(self, device_id: str) -> Any:
        return self._unsupported("query_access_controller")

    def query_door(self, device_id: str, door_id: str) -> Any:
        return self._unsupported("query_door")

    def query_credential(self, credential_id: str) -> Any:
        return self._unsupported("query_credential")

    def query_access_policy(self, person_id: str, door_id: str) -> Any:
        return self._unsupported("query_access_policy")

    def search_access_events(
        self,
        device_id: str,
        door_id: str,
        credential_id: str,
        limit: int = 10,
    ) -> Any:
        return self._unsupported("search_access_events")

    def query_alarm_rule(self, device_id: str, rule_id: str) -> Any:
        return self._unsupported("query_alarm_rule")

    def query_alarm_signal(self, device_id: str, alarm_id: str) -> Any:
        return self._unsupported("query_alarm_signal")

    def query_alarm_environment(self, device_id: str, alarm_id: str) -> Any:
        return self._unsupported("query_alarm_environment")

    def query_alarm_verification(self, device_id: str, alarm_id: str) -> Any:
        return self._unsupported("query_alarm_verification")

    def query_alarm_correlation(self, device_id: str, alarm_id: str) -> Any:
        return self._unsupported("query_alarm_correlation")
