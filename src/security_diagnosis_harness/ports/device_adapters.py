"""Adapter Registry Port。

为 RoutedDeviceGateway 提供按 adapter_key 获取 ready Adapter 的确定性契约。
只维护内存中的注册表，不访问网络、配置或凭证。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from security_diagnosis_harness.ports.device_gateway import DeviceGateway


class DeviceAdapterRegistryError(Exception):
    """Adapter Registry 受控异常基类。"""


class InvalidAdapterKeyError(DeviceAdapterRegistryError):
    """adapter_key 为空或空白（禁止注册）。"""

    def __init__(self) -> None:
        super().__init__("adapter_key 不能为空")


class DuplicateAdapterKeyError(DeviceAdapterRegistryError):
    """adapter_key 已存在（禁止静默覆盖）。"""

    def __init__(self, adapter_key: str) -> None:
        super().__init__(f"adapter_key {adapter_key} 已注册，禁止覆盖")
        self.adapter_key = adapter_key


class UnknownAdapterKeyError(DeviceAdapterRegistryError):
    """adapter_key 未注册（禁止返回 None 或猜测路由）。"""

    def __init__(self, adapter_key: str) -> None:
        super().__init__(f"adapter_key {adapter_key} 未注册")
        self.adapter_key = adapter_key


class AdapterNotReadyError(DeviceAdapterRegistryError):
    """adapter_key 已注册但尚未 ready（Router 不得获取）。"""

    def __init__(self, adapter_key: str) -> None:
        super().__init__(f"adapter_key {adapter_key} 未 ready")
        self.adapter_key = adapter_key


@runtime_checkable
class DeviceAdapterRegistryPort(Protocol):
    """按 adapter_key 注册与获取 DeviceGateway 的确定性契约。"""

    def register(
        self,
        adapter_key: str,
        gateway: DeviceGateway,
        *,
        ready: bool = False,
    ) -> None:
        """注册 Adapter；重复 key 或空 key 立即受控失败，禁止覆盖。"""
        ...

    def mark_ready(self, adapter_key: str) -> None:
        """把已注册 Adapter 标记为 ready；未知 key 受控失败。"""
        ...

    def mark_not_ready(self, adapter_key: str) -> None:
        """把已注册 Adapter 标记为 not-ready；未知 key 受控失败。"""
        ...

    def get(self, adapter_key: str) -> DeviceGateway:
        """按 key 读取已注册 Adapter；未知 key 抛受控异常，绝不返回 None。"""
        ...

    def get_ready(self, adapter_key: str) -> DeviceGateway:
        """按 key 读取 ready Adapter；未知 key 或未 ready 均受控失败。"""
        ...

    def ready_adapter_keys(self) -> tuple[str, ...]:
        """返回冻结、稳定排序的 ready key 元组。"""
        ...
