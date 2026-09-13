"""设备资产目录 Port。"""

from typing import Protocol, runtime_checkable

from security_diagnosis_harness.domain.device_integration import DeviceAsset


class DeviceAssetCatalogError(Exception):
    """设备资产目录异常基类。"""


class DeviceAssetNotFoundError(DeviceAssetCatalogError):
    """指定 device_id 的资产不存在。"""

    def __init__(self, device_id: str) -> None:
        super().__init__(f"设备资产 {device_id} 不存在")
        self.device_id = device_id


class DeviceAssetAlreadyExistsError(DeviceAssetCatalogError):
    """指定 device_id 的资产已存在（禁止静默覆盖）。"""

    def __init__(self, device_id: str) -> None:
        super().__init__(f"设备资产 {device_id} 已存在")
        self.device_id = device_id


@runtime_checkable
class DeviceAssetCatalogPort(Protocol):
    def get(self, device_id: str) -> DeviceAsset:
        """按内部设备 ID 读取非敏感资产信息。"""
        ...

    def list_enabled(self) -> list[DeviceAsset]:
        """列出启用资产。"""
        ...

