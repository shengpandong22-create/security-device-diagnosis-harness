"""RoutedDeviceGateway：以资产目录为唯一路由来源的确定性 DeviceGateway。

路由规则（每个含 device_id 的操作严格执行，顺序固定）：

1. `DeviceAssetCatalogPort.get(device_id)` 读取资产；
2. enabled 检查；
3. 方法到 `DeviceCapability` 的固定映射检查（资产必须声明该能力）；
4. Registry 根据资产中的 adapter_key 获取 ready Adapter；
5. 原样调用对应只读方法并返回同一领域对象契约。

约束：

- 调用参数中不存在 adapter_key / endpoint / credential，模型无法选择路由；
- 不在多个 Adapter 之间 fallback、广播或自动重试；
- 下游 `DeviceAdapterError` 保持稳定分类，路由层不捕获、不包装、不泄漏文本；
- Router 不把失败伪造成 Evidence，只返回领域对象或抛出受控异常；
- `query_credential` / `query_access_policy` 因缺少 device_id，在委派前抛
  `routing_context_required` 类受控异常，不猜测路由；
- 不访问网络、配置、环境变量或凭证。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType

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
from security_diagnosis_harness.domain.device_integration import DeviceCapability
from security_diagnosis_harness.domain.recording import (
    PlaybackCheckResult,
    RecordingPlanSnapshot,
    StorageSnapshot,
)
from security_diagnosis_harness.ports.device_adapters import DeviceAdapterRegistryPort
from security_diagnosis_harness.ports.device_assets import DeviceAssetCatalogPort
from security_diagnosis_harness.ports.device_gateway import DeviceGateway

__all__ = [
    "DeviceAssetDisabledError",
    "DeviceCapabilityMissingError",
    "DeviceRoutingError",
    "RoutedDeviceGateway",
    "RoutingContextRequiredError",
]


class DeviceRoutingError(Exception):
    """路由受控异常基类，携带稳定的机器可读 reason。"""

    reason: str = "routing_error"


class RoutingContextRequiredError(DeviceRoutingError):
    """操作缺少 device_id，无法确定唯一路由（禁止猜测）。"""

    reason = "routing_context_required"

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"操作 {operation} 缺少 device_id，无法确定路由")


class DeviceAssetDisabledError(DeviceRoutingError):
    """资产被禁用，禁止路由到 Adapter。

    异常文本不得包含 device_id（Phase 9C-3 异常安全契约）；
    内部属性保留供程序判断。
    """

    reason = "asset_disabled"

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        super().__init__("设备资产已禁用，禁止路由到 Adapter")


class DeviceCapabilityMissingError(DeviceRoutingError):
    """资产未声明操作所需的 DeviceCapability。

    异常文本不得包含 device_id（Phase 9C-3 异常安全契约）；
    capability 与 operation 是低基数稳定码，允许进入文本。
    """

    reason = "capability_missing"

    def __init__(self, device_id: str, operation: str, capability: DeviceCapability) -> None:
        self.device_id = device_id
        self.operation = operation
        self.capability = capability
        super().__init__(
            f"设备资产未声明能力 {capability.value}（操作 {operation}）"
        )


# 方法到 DeviceCapability 的固定映射；与 SimulatorDeviceGateway 的既有约定一致。
# 通过 MappingProxyType 冻结，任何调用方都无法替换映射内容。
_METHOD_CAPABILITIES: Mapping[str, DeviceCapability] = MappingProxyType(
    {
        "query_status": DeviceCapability.STATUS,
        "query_channel_snapshot": DeviceCapability.CHANNEL,
        "query_stream_snapshot": DeviceCapability.STREAM,
        "query_platform_pull_status": DeviceCapability.STREAM,
        "query_recording_plan": DeviceCapability.RECORDING,
        "query_storage_status": DeviceCapability.RECORDING,
        "check_recording_playback": DeviceCapability.RECORDING,
        "query_access_controller": DeviceCapability.ACCESS,
        "query_door": DeviceCapability.ACCESS,
        "search_access_events": DeviceCapability.ACCESS,
        "query_alarm_rule": DeviceCapability.ALARM_DIAGNOSIS,
        "query_alarm_signal": DeviceCapability.ALARM_DIAGNOSIS,
        "query_alarm_environment": DeviceCapability.ALARM_DIAGNOSIS,
        "query_alarm_verification": DeviceCapability.ALARM_DIAGNOSIS,
        "query_alarm_correlation": DeviceCapability.ALARM_DIAGNOSIS,
        "search_alarm_events": DeviceCapability.ALARM,
        "read_config_snapshot": DeviceCapability.CONFIG,
    }
)


class RoutedDeviceGateway:
    """只读确定性路由网关，满足 runtime-checkable `DeviceGateway` 契约。"""

    def __init__(
        self,
        asset_catalog: DeviceAssetCatalogPort,
        registry: DeviceAdapterRegistryPort,
    ) -> None:
        self._asset_catalog = asset_catalog
        self._registry = registry

    # ------------------------------------------------------------ 路由核心
    def _route(self, operation: str, device_id: str) -> DeviceGateway:
        """按固定顺序解析资产并返回唯一 ready Adapter；任何失败都受控。"""
        asset = self._asset_catalog.get(device_id)
        if not asset.enabled:
            raise DeviceAssetDisabledError(device_id)
        capability = _METHOD_CAPABILITIES[operation]
        if capability not in asset.capabilities:
            raise DeviceCapabilityMissingError(device_id, operation, capability)
        return self._registry.get_ready(asset.adapter_key)

    # ------------------------------------------------------------ 设备事实
    def query_status(self, device_id: str) -> DeviceSnapshot:
        adapter = self._route("query_status", device_id)
        return adapter.query_status(device_id)

    def query_channel_snapshot(self, device_id: str) -> ChannelSnapshot:
        adapter = self._route("query_channel_snapshot", device_id)
        return adapter.query_channel_snapshot(device_id)

    def query_stream_snapshot(
        self,
        device_id: str,
        stream_kind: StreamKind = StreamKind.MAIN,
    ) -> StreamSnapshot:
        adapter = self._route("query_stream_snapshot", device_id)
        return adapter.query_stream_snapshot(device_id, stream_kind)

    def query_platform_pull_status(self, device_id: str) -> PlatformPullStatus:
        adapter = self._route("query_platform_pull_status", device_id)
        return adapter.query_platform_pull_status(device_id)

    # ------------------------------------------------------------ 录像事实
    def query_recording_plan(self, device_id: str, channel_id: str) -> RecordingPlanSnapshot:
        adapter = self._route("query_recording_plan", device_id)
        return adapter.query_recording_plan(device_id, channel_id)

    def query_storage_status(self, device_id: str, channel_id: str) -> StorageSnapshot:
        adapter = self._route("query_storage_status", device_id)
        return adapter.query_storage_status(device_id, channel_id)

    def check_recording_playback(
        self,
        device_id: str,
        channel_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> PlaybackCheckResult:
        adapter = self._route("check_recording_playback", device_id)
        return adapter.check_recording_playback(device_id, channel_id, start_at, end_at)

    # ------------------------------------------------------------ 门禁事实
    def query_access_controller(self, device_id: str) -> AccessControllerSnapshot:
        adapter = self._route("query_access_controller", device_id)
        return adapter.query_access_controller(device_id)

    def query_door(self, device_id: str, door_id: str) -> DoorSnapshot:
        adapter = self._route("query_door", device_id)
        return adapter.query_door(device_id, door_id)

    def query_credential(self, credential_id: str) -> CredentialSnapshot:
        """缺少 device_id，路由上下文不足；在委派前受控失败。"""
        raise RoutingContextRequiredError("query_credential")

    def query_access_policy(self, person_id: str, door_id: str) -> AccessPolicySnapshot:
        """缺少 device_id，路由上下文不足；在委派前受控失败。"""
        raise RoutingContextRequiredError("query_access_policy")

    def search_access_events(
        self,
        device_id: str,
        door_id: str,
        credential_id: str,
        limit: int = 10,
    ) -> list[AccessEvent]:
        adapter = self._route("search_access_events", device_id)
        return adapter.search_access_events(device_id, door_id, credential_id, limit)

    # ------------------------------------------------------------ 报警事实
    def query_alarm_rule(self, device_id: str, rule_id: str) -> AlarmRuleSnapshot:
        adapter = self._route("query_alarm_rule", device_id)
        return adapter.query_alarm_rule(device_id, rule_id)

    def query_alarm_signal(self, device_id: str, alarm_id: str) -> AlarmSignalSnapshot:
        adapter = self._route("query_alarm_signal", device_id)
        return adapter.query_alarm_signal(device_id, alarm_id)

    def query_alarm_environment(
        self, device_id: str, alarm_id: str
    ) -> AlarmEnvironmentSnapshot:
        adapter = self._route("query_alarm_environment", device_id)
        return adapter.query_alarm_environment(device_id, alarm_id)

    def query_alarm_verification(
        self, device_id: str, alarm_id: str
    ) -> AlarmVerificationSnapshot:
        adapter = self._route("query_alarm_verification", device_id)
        return adapter.query_alarm_verification(device_id, alarm_id)

    def query_alarm_correlation(
        self, device_id: str, alarm_id: str
    ) -> AlarmCorrelationSnapshot:
        adapter = self._route("query_alarm_correlation", device_id)
        return adapter.query_alarm_correlation(device_id, alarm_id)

    def search_alarm_events(
        self,
        device_id: str,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[DeviceAlarmEvent]:
        adapter = self._route("search_alarm_events", device_id)
        return adapter.search_alarm_events(device_id, keyword, limit)

    # ------------------------------------------------------------ 配置事实
    def read_config_snapshot(self, device_id: str) -> DeviceConfigSnapshot:
        adapter = self._route("read_config_snapshot", device_id)
        return adapter.read_config_snapshot(device_id)
