"""Phase 9C-1A：内存 DeviceAssetCatalog 验收。

只依赖内存数据，不涉及网络、数据库、环境变量或真实凭证。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from security_diagnosis_harness.adapters.device_assets import (
    InMemoryDeviceAssetCatalog,
)
from security_diagnosis_harness.domain.device_integration import (
    DeviceAsset,
    DeviceCapability,
)
from security_diagnosis_harness.ports.device_assets import (
    DeviceAssetAlreadyExistsError,
    DeviceAssetCatalogError,
    DeviceAssetCatalogPort,
    DeviceAssetNotFoundError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

SOURCE_PATHS = (
    REPO_ROOT
    / "src"
    / "security_diagnosis_harness"
    / "ports"
    / "device_assets.py",
    REPO_ROOT
    / "src"
    / "security_diagnosis_harness"
    / "adapters"
    / "device_assets"
    / "__init__.py",
    REPO_ROOT
    / "src"
    / "security_diagnosis_harness"
    / "adapters"
    / "device_assets"
    / "in_memory.py",
)

ALLOWED_IMPORTS = {
    "__future__",
    "typing",
    "collections.abc",
    "copy",
    "threading",
    "ast",
    "pathlib",
    "security_diagnosis_harness.domain.device_integration",
    "security_diagnosis_harness.ports.device_assets",
    "security_diagnosis_harness.adapters.device_assets.in_memory",
}


def _asset(device_id: str, *, enabled: bool = True) -> DeviceAsset:
    """构造非敏感的合成测试资产，不含真实 IP、账号或凭证。"""
    return DeviceAsset(
        device_id=device_id,
        device_type="camera",
        site_alias="site-a",
        region_alias="region-a",
        adapter_key="simulator",
        capabilities=frozenset({DeviceCapability.STATUS}),
        enabled=enabled,
    )


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


# ---------------------------------------------------------------- 空目录
def test_empty_catalog_lists_nothing() -> None:
    catalog = InMemoryDeviceAssetCatalog()

    assert catalog.list_enabled() == []


def test_empty_catalog_get_fails() -> None:
    catalog = InMemoryDeviceAssetCatalog()

    with pytest.raises(DeviceAssetNotFoundError):
        catalog.get("dev-missing")


# ---------------------------------------------------------------- get
def test_get_returns_single_asset() -> None:
    asset = _asset("dev-cam-01")
    catalog = InMemoryDeviceAssetCatalog([asset])

    assert catalog.get("dev-cam-01") == asset


def test_unknown_asset_fails_deterministically() -> None:
    catalog = InMemoryDeviceAssetCatalog([_asset("dev-cam-01")])

    with pytest.raises(DeviceAssetNotFoundError) as exc_info:
        catalog.get("dev-unknown")

    assert isinstance(exc_info.value, DeviceAssetCatalogError)
    assert exc_info.value.device_id == "dev-unknown"


# ---------------------------------------------------------------- 重复 ID
def test_duplicate_device_id_is_rejected_on_construction() -> None:
    with pytest.raises(DeviceAssetAlreadyExistsError) as exc_info:
        InMemoryDeviceAssetCatalog([_asset("dev-cam-01"), _asset("dev-cam-01")])

    assert isinstance(exc_info.value, DeviceAssetCatalogError)
    assert exc_info.value.device_id == "dev-cam-01"


# ---------------------------------------------------------------- list_enabled
def test_disabled_assets_are_excluded_from_list_enabled() -> None:
    catalog = InMemoryDeviceAssetCatalog(
        [_asset("dev-cam-on"), _asset("dev-cam-off", enabled=False)]
    )

    assert [asset.device_id for asset in catalog.list_enabled()] == ["dev-cam-on"]


def test_list_enabled_is_stably_sorted_by_device_id() -> None:
    catalog = InMemoryDeviceAssetCatalog(
        [
            _asset("dev-cam-03"),
            _asset("dev-alarm-01"),
            _asset("dev-cam-01"),
            _asset("dev-access-01"),
        ]
    )

    first = catalog.list_enabled()
    second = catalog.list_enabled()

    assert [asset.device_id for asset in first] == [
        "dev-access-01",
        "dev-alarm-01",
        "dev-cam-01",
        "dev-cam-03",
    ]
    assert [asset.device_id for asset in second] == [
        asset.device_id for asset in first
    ]


# ---------------------------------------------------------------- 输入隔离
def test_constructor_input_list_is_copied() -> None:
    assets = [_asset("dev-cam-01"), _asset("dev-alarm-01")]
    catalog = InMemoryDeviceAssetCatalog(assets)

    assets.clear()
    assets.append(_asset("dev-cam-99", enabled=False))

    assert catalog.get("dev-cam-01") == _asset("dev-cam-01")
    assert catalog.get("dev-alarm-01") == _asset("dev-alarm-01")
    assert [asset.device_id for asset in catalog.list_enabled()] == [
        "dev-alarm-01",
        "dev-cam-01",
    ]


# ---------------------------------------------------------------- 读取隔离
def test_get_returns_deep_copies() -> None:
    stored = _asset("dev-cam-01")
    catalog = InMemoryDeviceAssetCatalog([stored])

    first = catalog.get("dev-cam-01")
    second = catalog.get("dev-cam-01")

    assert first is not stored
    assert second is not first
    assert first.capabilities is not stored.capabilities
    assert first == stored


def test_list_enabled_returns_deep_copies() -> None:
    stored = _asset("dev-cam-01")
    catalog = InMemoryDeviceAssetCatalog([stored])

    result = catalog.list_enabled()
    result.clear()

    assert catalog.list_enabled() == [stored]
    assert catalog.list_enabled()[0] is not stored


# ---------------------------------------------------------------- Protocol
def test_satisfies_runtime_checkable_port() -> None:
    catalog = InMemoryDeviceAssetCatalog([_asset("dev-cam-01")])

    assert isinstance(catalog, DeviceAssetCatalogPort)


# ---------------------------------------------------------------- 依赖守卫
def test_modules_do_not_import_forbidden_dependencies() -> None:
    for source_path in SOURCE_PATHS:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported = _imported_modules(tree)
        forbidden = imported - ALLOWED_IMPORTS
        assert not forbidden, f"{source_path.name} 引入了未授权模块: {sorted(forbidden)}"
