"""设备资产目录 Port。"""

from typing import Protocol, runtime_checkable

from security_diagnosis_harness.domain.device_integration import DeviceAsset


@runtime_checkable
class DeviceAssetCatalogPort(Protocol):
    def get(self, device_id: str) -> DeviceAsset:
        """按内部设备 ID 读取非敏感资产信息。"""
        ...

    def list_enabled(self) -> list[DeviceAsset]:
        """列出启用资产。"""
        ...

