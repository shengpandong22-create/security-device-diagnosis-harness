"""StaticDeviceGateway：从本地 JSON 文件读取设备样例数据。

只读取构造参数指定的那一个文件，不访问任何真实设备网络，
也不读取系统上的真实设备配置。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    alarms: list[dict[str, Any]] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


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
