"""确定性的内存设备资产目录。"""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from threading import RLock

from security_diagnosis_harness.domain.device_integration import DeviceAsset
from security_diagnosis_harness.ports.device_assets import (
    DeviceAssetAlreadyExistsError,
    DeviceAssetNotFoundError,
)

__all__ = ["InMemoryDeviceAssetCatalog"]


class InMemoryDeviceAssetCatalog:
    """通过深拷贝隔离调用方、按 device_id 稳定排序的内存资产目录。

    - 构造时立即拒绝重复 device_id，不做静默覆盖；
    - 所有输入先深拷贝后存储，所有读取返回深拷贝；
    - `get()` 对未知资产抛受控异常，绝不返回 None。
    """

    def __init__(self, assets: Iterable[DeviceAsset] = ()) -> None:
        items: dict[str, DeviceAsset] = {}
        for asset in assets:
            stored = deepcopy(asset)
            if stored.device_id in items:
                raise DeviceAssetAlreadyExistsError(stored.device_id)
            items[stored.device_id] = stored
        self._items: dict[str, DeviceAsset] = items
        self._lock = RLock()

    def get(self, device_id: str) -> DeviceAsset:
        """按 device_id 读取资产；不存在时抛 `DeviceAssetNotFoundError`。"""
        with self._lock:
            asset = self._items.get(device_id)
            if asset is None:
                raise DeviceAssetNotFoundError(device_id)
            return deepcopy(asset)

    def list_enabled(self) -> list[DeviceAsset]:
        """返回启用资产，按 device_id 稳定排序。"""
        with self._lock:
            snapshot = [
                deepcopy(asset) for asset in self._items.values() if asset.enabled
            ]
        snapshot.sort(key=lambda asset: asset.device_id)
        return snapshot
