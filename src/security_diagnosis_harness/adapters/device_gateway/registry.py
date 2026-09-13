"""确定性的内存 Adapter Registry。

- 以非空 adapter_key 注册 DeviceGateway；重复 key 立即受控失败，禁止覆盖；
- get 未知 key 受控失败，绝不返回 None；
- 显式维护 ready / not-ready 状态，只有 ready Adapter 可由 Router 获取；
- `ready_adapter_keys()` 返回冻结、稳定排序的结果；
- 构造输入与注册调用不会让调用方替换 Registry 内部映射；
- 不访问网络、配置、环境变量或凭证。
"""

from __future__ import annotations

from collections.abc import Iterable
from threading import RLock

from security_diagnosis_harness.ports.device_adapters import (
    AdapterNotReadyError,
    DuplicateAdapterKeyError,
    InvalidAdapterKeyError,
    UnknownAdapterKeyError,
)
from security_diagnosis_harness.ports.device_gateway import DeviceGateway

__all__ = ["InMemoryDeviceAdapterRegistry"]


class _RegistryEntry:
    """Registry 内部条目，外部不可直接修改。"""

    __slots__ = ("gateway", "ready")

    def __init__(self, gateway: DeviceGateway, *, ready: bool) -> None:
        self.gateway = gateway
        self.ready = ready


class InMemoryDeviceAdapterRegistry:
    """线程安全的内存 Adapter Registry，行为完全确定。"""

    def __init__(self, entries: Iterable[tuple[str, DeviceGateway]] = ()) -> None:
        """从 (adapter_key, gateway) 序列构造；序列会被立即复制。

        重复 key 立即受控失败；所有条目初始为 not-ready。
        """
        items: dict[str, _RegistryEntry] = {}
        for adapter_key, gateway in entries:
            _require_key(adapter_key)
            if adapter_key in items:
                raise DuplicateAdapterKeyError(adapter_key)
            items[adapter_key] = _RegistryEntry(gateway, ready=False)
        self._items: dict[str, _RegistryEntry] = items
        self._lock = RLock()

    def register(
        self,
        adapter_key: str,
        gateway: DeviceGateway,
        *,
        ready: bool = False,
    ) -> None:
        """注册 Adapter；空 key 或重复 key 立即受控失败，禁止覆盖。"""
        _require_key(adapter_key)
        with self._lock:
            if adapter_key in self._items:
                raise DuplicateAdapterKeyError(adapter_key)
            self._items[adapter_key] = _RegistryEntry(gateway, ready=ready)

    def mark_ready(self, adapter_key: str) -> None:
        """把已注册 Adapter 标记为 ready；未知 key 受控失败。"""
        with self._lock:
            entry = self._require_entry(adapter_key)
            entry.ready = True

    def mark_not_ready(self, adapter_key: str) -> None:
        """把已注册 Adapter 标记为 not-ready；未知 key 受控失败。"""
        with self._lock:
            entry = self._require_entry(adapter_key)
            entry.ready = False

    def get(self, adapter_key: str) -> DeviceGateway:
        """按 key 读取已注册 Adapter；未知 key 抛受控异常，绝不返回 None。"""
        with self._lock:
            return self._require_entry(adapter_key).gateway

    def get_ready(self, adapter_key: str) -> DeviceGateway:
        """按 key 读取 ready Adapter；未知 key 或未 ready 均受控失败。"""
        with self._lock:
            entry = self._require_entry(adapter_key)
            if not entry.ready:
                raise AdapterNotReadyError(adapter_key)
            return entry.gateway

    def ready_adapter_keys(self) -> tuple[str, ...]:
        """返回冻结、按 key 稳定排序的 ready key 元组。"""
        with self._lock:
            keys = [key for key, entry in self._items.items() if entry.ready]
        return tuple(sorted(keys))

    def _require_entry(self, adapter_key: str) -> _RegistryEntry:
        entry = self._items.get(adapter_key)
        if entry is None:
            raise UnknownAdapterKeyError(adapter_key)
        return entry


def _require_key(adapter_key: str) -> None:
    """拒绝空或纯空白的 adapter_key。"""
    if not isinstance(adapter_key, str) or not adapter_key.strip():
        raise InvalidAdapterKeyError()
