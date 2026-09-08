"""DeviceGateway Port。

只定义只读契约，不涉及任何真实设备协议、SDK 或网络连接。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from security_diagnosis_harness.domain.device import (
    DeviceAlarmEvent,
    DeviceConfigSnapshot,
    DeviceSnapshot,
)


class DeviceGatewayError(Exception):
    """设备网关异常基类。"""


class DeviceNotFoundError(DeviceGatewayError):
    """设备不存在。"""


class DeviceGatewayDataError(DeviceGatewayError):
    """本地设备数据缺失或格式错误。"""


@runtime_checkable
class DeviceGateway(Protocol):
    """只读设备事实来源。"""

    def query_status(self, device_id: str) -> DeviceSnapshot:
        """查询设备状态快照。"""
        ...

    def search_alarm_events(
        self,
        device_id: str,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[DeviceAlarmEvent]:
        """查询设备告警事件，可按关键字过滤。"""
        ...

    def read_config_snapshot(self, device_id: str) -> DeviceConfigSnapshot:
        """读取设备配置快照（凭证字段必须已脱敏）。"""
        ...
