"""DeviceGateway Port。

只定义只读契约，不涉及任何真实设备协议、SDK 或网络连接。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from security_diagnosis_harness.domain.access import (
    AccessControllerSnapshot,
    AccessEvent,
    AccessPolicySnapshot,
    CredentialSnapshot,
    DoorSnapshot,
)
from security_diagnosis_harness.domain.alarm import (
    AlarmCorrelationSnapshot,
    AlarmEnvironmentSnapshot,
    AlarmRuleSnapshot,
    AlarmSignalSnapshot,
    AlarmVerificationSnapshot,
)
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
from security_diagnosis_harness.domain.recording import (
    PlaybackCheckResult,
    RecordingPlanSnapshot,
    StorageSnapshot,
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

    def query_recording_plan(self, device_id: str, channel_id: str) -> RecordingPlanSnapshot:
        """查询指定通道的录像计划（是否启用、录像模式、计划时间段）。"""
        ...

    def query_storage_status(self, device_id: str, channel_id: str) -> StorageSnapshot:
        """查询录像存储池状态与容量。"""
        ...

    def check_recording_playback(
        self,
        device_id: str,
        channel_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> PlaybackCheckResult:
        """检查指定时间段是否存在录像、是否可回放。"""
        ...

    def query_access_controller(self, device_id: str) -> AccessControllerSnapshot:
        """查询门禁控制器在线状态、健康状态与最近错误。"""
        ...

    def query_door(self, device_id: str, door_id: str) -> DoorSnapshot:
        """查询门状态、门锁状态与门磁异常。"""
        ...

    def query_credential(self, credential_id: str) -> CredentialSnapshot:
        """查询卡、人脸、二维码、指纹等凭证状态。"""
        ...

    def query_access_policy(self, person_id: str, door_id: str) -> AccessPolicySnapshot:
        """查询人员 / 凭证对指定门的权限与授权时间段。"""
        ...

    def search_access_events(
        self,
        device_id: str,
        door_id: str,
        credential_id: str,
        limit: int = 10,
    ) -> list[AccessEvent]:
        """查询近期门禁通行事件。"""
        ...

    def query_alarm_rule(self, device_id: str, rule_id: str) -> AlarmRuleSnapshot:
        """查询报警规则配置。"""
        ...

    def query_alarm_signal(self, device_id: str, alarm_id: str) -> AlarmSignalSnapshot:
        """查询报警触发时的信号快照。"""
        ...

    def query_alarm_environment(
        self, device_id: str, alarm_id: str
    ) -> AlarmEnvironmentSnapshot:
        """查询报警触发时的环境干扰事实。"""
        ...

    def query_alarm_verification(
        self, device_id: str, alarm_id: str
    ) -> AlarmVerificationSnapshot:
        """查询报警复核结果。"""
        ...

    def query_alarm_correlation(
        self, device_id: str, alarm_id: str
    ) -> AlarmCorrelationSnapshot:
        """查询重复 / 关联告警事实。"""
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
