"""StaticDeviceGateway：从本地 JSON 文件读取设备样例数据。

只读取构造参数指定的那一个文件，不访问任何真实设备网络，
也不读取系统上的真实设备配置。

兼容两种数据形态：

- Phase 0 `static_devices.sample.json`：只有 snapshot / alarms / config；
- Phase 1 `camera_black_screen_cases.json`：额外包含 channel / streams /
  platform_pull / case_id / expected_label。

旧文件仍然可用于 Phase 0 demo；读取旧文件里不存在的摄像头事实时，
抛出受控的 `DeviceGatewayDataError`，而不是返回伪造数据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
from security_diagnosis_harness.ports.device_gateway import (
    DeviceGatewayDataError,
    DeviceNotFoundError,
)


class StaticDeviceEntry(BaseModel):
    """单个设备的静态样例数据。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    channel: dict[str, Any] = Field(default_factory=dict)
    streams: dict[str, dict[str, Any]] = Field(default_factory=dict)
    platform_pull: dict[str, Any] = Field(default_factory=dict)
    alarms: list[dict[str, Any]] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    case_id: str | None = None
    expected_label: str | None = None


class DeviceCaseRef(BaseModel):
    """评测用例引用。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    case_id: str | None = None
    expected_label: str | None = None


class StaticDeviceDataset(BaseModel):
    """静态设备数据文件结构。"""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    devices: list[StaticDeviceEntry] = Field(default_factory=list)


class StaticDeviceGateway:
    """只读静态设备网关。"""

    def __init__(self, data_path: str | Path) -> None:
        self._data_path = Path(data_path)
        self._entries: dict[str, StaticDeviceEntry] = self._load()

    @property
    def data_path(self) -> Path:
        return self._data_path

    def _load(self) -> dict[str, StaticDeviceEntry]:
        try:
            raw = self._data_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DeviceGatewayDataError(f"设备数据文件不可读: {self._data_path}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeviceGatewayDataError(f"设备数据文件不是合法 JSON: {self._data_path}") from exc

        try:
            dataset = StaticDeviceDataset.model_validate(payload)
        except ValidationError as exc:
            raise DeviceGatewayDataError(f"设备数据结构不符合预期: {exc}") from exc

        return {entry.device_id: entry for entry in dataset.devices}

    def _require_entry(self, device_id: str) -> StaticDeviceEntry:
        try:
            return self._entries[device_id]
        except KeyError as exc:
            raise DeviceNotFoundError(f"设备 {device_id} 不存在于静态样例数据中") from exc

    def query_status(self, device_id: str) -> DeviceSnapshot:
        entry = self._require_entry(device_id)
        if not entry.snapshot:
            raise DeviceGatewayDataError(f"设备 {device_id} 缺少状态快照数据")
        return DeviceSnapshot.model_validate({"device_id": device_id, **entry.snapshot})

    def query_channel_snapshot(self, device_id: str) -> ChannelSnapshot:
        entry = self._require_entry(device_id)
        if not entry.channel:
            raise DeviceGatewayDataError(f"设备 {device_id} 缺少通道快照数据")
        return ChannelSnapshot.model_validate({"device_id": device_id, **entry.channel})

    def query_stream_snapshot(
        self,
        device_id: str,
        stream_kind: StreamKind = StreamKind.MAIN,
    ) -> StreamSnapshot:
        entry = self._require_entry(device_id)
        stream = entry.streams.get(stream_kind.value)
        if stream is None:
            raise DeviceGatewayDataError(
                f"设备 {device_id} 缺少 {stream_kind.value} 码流快照数据"
            )
        return StreamSnapshot.model_validate(
            {"device_id": device_id, "stream_kind": stream_kind.value, **stream}
        )

    def query_platform_pull_status(self, device_id: str) -> PlatformPullStatus:
        entry = self._require_entry(device_id)
        if not entry.platform_pull:
            raise DeviceGatewayDataError(f"设备 {device_id} 缺少平台拉流状态数据")
        return PlatformPullStatus.model_validate({"device_id": device_id, **entry.platform_pull})

    def search_alarm_events(
        self,
        device_id: str,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[DeviceAlarmEvent]:
        entry = self._require_entry(device_id)
        events = [
            DeviceAlarmEvent.model_validate({"device_id": device_id, **alarm})
            for alarm in entry.alarms
        ]
        if keyword:
            needle = keyword.lower()
            events = [
                event
                for event in events
                if needle in event.event_type.lower() or needle in event.message.lower()
            ]
        return events[:limit]

    def read_config_snapshot(self, device_id: str) -> DeviceConfigSnapshot:
        entry = self._require_entry(device_id)
        if not entry.config:
            raise DeviceGatewayDataError(f"设备 {device_id} 缺少配置快照数据")
        # 凭证字段脱敏由 DeviceConfigSnapshot 的校验器保证。
        return DeviceConfigSnapshot.model_validate({"device_id": device_id, **entry.config})

    def list_cases(self) -> list[DeviceCaseRef]:
        """列出带 case_id 的评测用例；没有 case_id 的设备会被跳过。"""
        return [
            DeviceCaseRef(
                device_id=entry.device_id,
                case_id=entry.case_id,
                expected_label=entry.expected_label,
            )
            for entry in self._entries.values()
            if entry.case_id
        ]
