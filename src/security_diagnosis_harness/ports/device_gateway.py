"""DeviceGateway Port。

只定义只读契约，不涉及任何真实设备协议、SDK 或网络连接。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

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

    def query_channel_snapshot(self, device_id: str) -> ChannelSnapshot:
        """查询设备通道快照（通道在线、绑定、平台注册状态）。"""
        ...

    def query_stream_snapshot(
        self,
        device_id: str,
        stream_kind: StreamKind = StreamKind.MAIN,
    ) -> StreamSnapshot:
        """查询指定类型码流的取流快照。"""
        ...

    def query_platform_pull_status(self, device_id: str) -> PlatformPullStatus:
        """查询平台侧拉流状态。"""
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
