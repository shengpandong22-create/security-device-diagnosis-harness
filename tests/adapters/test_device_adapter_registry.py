"""Phase 9C-1B：内存 Adapter Registry 验收。

只依赖内存对象，不涉及网络、数据库、环境变量或真实凭证。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from security_diagnosis_harness.adapters.device_gateway.registry import (
    InMemoryDeviceAdapterRegistry,
)
from security_diagnosis_harness.ports.device_adapters import (
    AdapterNotReadyError,
    DeviceAdapterRegistryError,
    DeviceAdapterRegistryPort,
    DuplicateAdapterKeyError,
    InvalidAdapterKeyError,
    UnknownAdapterKeyError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

SOURCE_PATHS = (
    REPO_ROOT
    / "src"
    / "security_diagnosis_harness"
    / "ports"
    / "device_adapters.py",
    REPO_ROOT
    / "src"
    / "security_diagnosis_harness"
    / "adapters"
    / "device_gateway"
    / "registry.py",
)

ALLOWED_IMPORTS = {
    "__future__",
    "typing",
    "collections.abc",
    "threading",
    "security_diagnosis_harness.ports.device_gateway",
    "security_diagnosis_harness.ports.device_adapters",
}


class FakeGateway:
    """最小 Spy Gateway：只记录自身标识，不访问任何外部资源。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def query_status(self, device_id: str) -> str:
        return f"{self.name}:{device_id}"


# ---------------------------------------------------------------- 注册
def test_register_then_get_returns_same_gateway() -> None:
    gateway = FakeGateway("static")
    registry = InMemoryDeviceAdapterRegistry()

    registry.register("static", gateway)

    assert registry.get("static") is gateway


def test_register_with_ready_flag_is_immediately_retrievable() -> None:
    gateway = FakeGateway("simulator")
    registry = InMemoryDeviceAdapterRegistry()

    registry.register("simulator", gateway, ready=True)

    assert registry.get_ready("simulator") is gateway


@pytest.mark.parametrize("bad_key", ["", "   ", "\t"])
def test_register_rejects_empty_adapter_key(bad_key: str) -> None:
    registry = InMemoryDeviceAdapterRegistry()

    with pytest.raises(InvalidAdapterKeyError) as exc_info:
        registry.register(bad_key, FakeGateway("static"))

    assert isinstance(exc_info.value, DeviceAdapterRegistryError)


def test_duplicate_key_fails_and_never_overwrites() -> None:
    first = FakeGateway("first")
    second = FakeGateway("second")
    registry = InMemoryDeviceAdapterRegistry()
    registry.register("static", first, ready=True)

    with pytest.raises(DuplicateAdapterKeyError) as exc_info:
        registry.register("static", second)

    assert isinstance(exc_info.value, DeviceAdapterRegistryError)
    assert exc_info.value.adapter_key == "static"
    assert registry.get("static") is first


def test_constructor_rejects_duplicate_keys() -> None:
    entries = [("static", FakeGateway("a")), ("static", FakeGateway("b"))]

    with pytest.raises(DuplicateAdapterKeyError):
        InMemoryDeviceAdapterRegistry(entries)


# ---------------------------------------------------------------- get
def test_get_unknown_key_fails_never_returns_none() -> None:
    registry = InMemoryDeviceAdapterRegistry()

    with pytest.raises(UnknownAdapterKeyError) as exc_info:
        registry.get("ghost")

    assert isinstance(exc_info.value, DeviceAdapterRegistryError)
    assert exc_info.value.adapter_key == "ghost"


def test_get_ready_unknown_key_fails() -> None:
    registry = InMemoryDeviceAdapterRegistry()

    with pytest.raises(UnknownAdapterKeyError):
        registry.get_ready("ghost")


# ---------------------------------------------------------------- ready 状态
def test_registered_adapter_defaults_to_not_ready() -> None:
    registry = InMemoryDeviceAdapterRegistry()
    registry.register("static", FakeGateway("static"))

    with pytest.raises(AdapterNotReadyError) as exc_info:
        registry.get_ready("static")

    assert isinstance(exc_info.value, DeviceAdapterRegistryError)
    assert exc_info.value.adapter_key == "static"
    assert registry.ready_adapter_keys() == ()


def test_mark_ready_then_get_ready_returns_gateway() -> None:
    gateway = FakeGateway("static")
    registry = InMemoryDeviceAdapterRegistry()
    registry.register("static", gateway)

    registry.mark_ready("static")

    assert registry.get_ready("static") is gateway


def test_mark_not_ready_removes_from_ready_set() -> None:
    registry = InMemoryDeviceAdapterRegistry()
    registry.register("static", FakeGateway("static"), ready=True)

    registry.mark_not_ready("static")

    with pytest.raises(AdapterNotReadyError):
        registry.get_ready("static")
    assert registry.ready_adapter_keys() == ()


@pytest.mark.parametrize("method", ["mark_ready", "mark_not_ready"])
def test_marking_unknown_key_fails(method: str) -> None:
    registry = InMemoryDeviceAdapterRegistry()

    with pytest.raises(UnknownAdapterKeyError):
        getattr(registry, method)("ghost")


# ---------------------------------------------------------------- ready keys
def test_ready_adapter_keys_only_contains_ready_entries() -> None:
    registry = InMemoryDeviceAdapterRegistry()
    registry.register("static", FakeGateway("static"), ready=True)
    registry.register("simulator", FakeGateway("simulator"))
    registry.register("http", FakeGateway("http"), ready=True)

    assert registry.ready_adapter_keys() == ("http", "static")


def test_ready_adapter_keys_is_frozen_and_stable() -> None:
    registry = InMemoryDeviceAdapterRegistry()
    registry.register("b", FakeGateway("b"), ready=True)
    registry.register("a", FakeGateway("a"), ready=True)

    first = registry.ready_adapter_keys()
    second = registry.ready_adapter_keys()

    assert isinstance(first, tuple)
    assert first == ("a", "b")
    assert first == second


def test_empty_registry_ready_keys_is_empty_tuple() -> None:
    registry = InMemoryDeviceAdapterRegistry()

    assert registry.ready_adapter_keys() == ()


# ---------------------------------------------------------------- 输入隔离
def test_constructor_input_list_cannot_replace_internal_mapping() -> None:
    entries = [("static", FakeGateway("static"))]
    registry = InMemoryDeviceAdapterRegistry(entries)

    entries.clear()
    entries.append(("intruder", FakeGateway("intruder")))

    assert registry.get("static").name == "static"
    with pytest.raises(UnknownAdapterKeyError):
        registry.get("intruder")


# ---------------------------------------------------------------- Protocol
def test_satisfies_runtime_checkable_port() -> None:
    registry = InMemoryDeviceAdapterRegistry()

    assert isinstance(registry, DeviceAdapterRegistryPort)


# ---------------------------------------------------------------- 依赖守卫
def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def test_modules_do_not_import_forbidden_dependencies() -> None:
    for source_path in SOURCE_PATHS:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported = _imported_modules(tree)
        forbidden = imported - ALLOWED_IMPORTS
        assert not forbidden, f"{source_path.name} 引入了未授权模块: {sorted(forbidden)}"
