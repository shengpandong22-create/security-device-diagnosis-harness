"""Phase 9C-1B：RoutedDeviceGateway 验收。

全部使用 Spy/Fake Gateway 与内存资产目录、内存 Registry；
不访问本机 HTTP 服务、网络、环境变量、凭证或文件。
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import pytest

from security_diagnosis_harness.adapters.device_gateway import (
    DeviceAssetDisabledError,
    DeviceCapabilityMissingError,
    DeviceRoutingError,
    InMemoryDeviceAdapterRegistry,
    RoutedDeviceGateway,
    RoutingContextRequiredError,
)
from security_diagnosis_harness.adapters.device_gateway.routed import _METHOD_CAPABILITIES
from security_diagnosis_harness.domain.camera import StreamKind
from security_diagnosis_harness.domain.device import DeviceSnapshot
from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
    DeviceAsset,
    DeviceCapability,
)
from security_diagnosis_harness.ports.device_adapters import (
    AdapterNotReadyError,
    UnknownAdapterKeyError,
)
from security_diagnosis_harness.ports.device_assets import (
    DeviceAssetNotFoundError,
)
from security_diagnosis_harness.ports.device_gateway import DeviceGateway

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
    REPO_ROOT
    / "src"
    / "security_diagnosis_harness"
    / "adapters"
    / "device_gateway"
    / "routed.py",
)

ALLOWED_IMPORTS = {
    "__future__",
    "datetime",
    "types",
    "typing",
    "collections.abc",
    "threading",
    "security_diagnosis_harness.domain.access",
    "security_diagnosis_harness.domain.alarm",
    "security_diagnosis_harness.domain.camera",
    "security_diagnosis_harness.domain.device",
    "security_diagnosis_harness.domain.device_integration",
    "security_diagnosis_harness.domain.recording",
    "security_diagnosis_harness.ports.device_adapters",
    "security_diagnosis_harness.ports.device_assets",
    "security_diagnosis_harness.ports.device_gateway",
}

_ALL_CAPABILITIES = frozenset(DeviceCapability)

_START = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
_END = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)

# 所有含 device_id 的既有 DeviceGateway 方法及其调用参数（默认值场景覆盖两组）。
_ROUTED_CALLS: list[tuple[str, tuple[object, ...]]] = [
    ("query_status", ("dev-cam-01",)),
    ("query_channel_snapshot", ("dev-cam-01",)),
    ("query_stream_snapshot", ("dev-cam-01",)),
    ("query_stream_snapshot", ("dev-cam-01", StreamKind.SUB)),
    ("query_platform_pull_status", ("dev-cam-01",)),
    ("query_recording_plan", ("dev-cam-01", "channel-01")),
    ("query_storage_status", ("dev-cam-01", "channel-01")),
    ("check_recording_playback", ("dev-cam-01", "channel-01", _START, _END)),
    ("query_access_controller", ("dev-access-01",)),
    ("query_door", ("dev-access-01", "door-01")),
    ("search_access_events", ("dev-access-01", "door-01", "credential-01")),
    ("search_access_events", ("dev-access-01", "door-01", "credential-01", 5)),
    ("query_alarm_rule", ("dev-alarm-01", "rule-01")),
    ("query_alarm_signal", ("dev-alarm-01", "alarm-01")),
    ("query_alarm_environment", ("dev-alarm-01", "alarm-01")),
    ("query_alarm_verification", ("dev-alarm-01", "alarm-01")),
    ("query_alarm_correlation", ("dev-alarm-01", "alarm-01")),
    ("search_alarm_events", ("dev-alarm-01",)),
    ("search_alarm_events", ("dev-alarm-01", "offline")),
    ("search_alarm_events", ("dev-alarm-01", "offline", 3)),
    ("read_config_snapshot", ("dev-cam-01",)),
]

# 每个映射操作的完整调用参数（取 _ROUTED_CALLS 中该操作的首个条目）。
_OPERATION_ARGS: dict[str, tuple[object, ...]] = {}
for _operation, _args in _ROUTED_CALLS:
    _OPERATION_ARGS.setdefault(_operation, _args)


class SpyDeviceGateway:
    """记录每次调用的 Spy Gateway，返回预置对象或抛出预置异常。"""

    def __init__(self, name: str = "spy") -> None:
        self.name = name
        self.default_result: object = object()
        self.results: dict[str, object] = {}
        self.errors: dict[str, Exception] = {}
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def _observe(self, operation: str, *args: object) -> object:
        self.calls.append((operation, args))
        if operation in self.errors:
            raise self.errors[operation]
        return self.results.get(operation, self.default_result)

    def query_status(self, device_id: str) -> object:
        return self._observe("query_status", device_id)

    def query_channel_snapshot(self, device_id: str) -> object:
        return self._observe("query_channel_snapshot", device_id)

    def query_stream_snapshot(
        self, device_id: str, stream_kind: StreamKind = StreamKind.MAIN
    ) -> object:
        return self._observe("query_stream_snapshot", device_id, stream_kind)

    def query_platform_pull_status(self, device_id: str) -> object:
        return self._observe("query_platform_pull_status", device_id)

    def query_recording_plan(self, device_id: str, channel_id: str) -> object:
        return self._observe("query_recording_plan", device_id, channel_id)

    def query_storage_status(self, device_id: str, channel_id: str) -> object:
        return self._observe("query_storage_status", device_id, channel_id)

    def check_recording_playback(
        self, device_id: str, channel_id: str, start_at: datetime, end_at: datetime
    ) -> object:
        return self._observe("check_recording_playback", device_id, channel_id, start_at, end_at)

    def query_access_controller(self, device_id: str) -> object:
        return self._observe("query_access_controller", device_id)

    def query_door(self, device_id: str, door_id: str) -> object:
        return self._observe("query_door", device_id, door_id)

    def query_credential(self, credential_id: str) -> object:
        return self._observe("query_credential", credential_id)

    def query_access_policy(self, person_id: str, door_id: str) -> object:
        return self._observe("query_access_policy", person_id, door_id)

    def search_access_events(
        self, device_id: str, door_id: str, credential_id: str, limit: int = 10
    ) -> object:
        return self._observe("search_access_events", device_id, door_id, credential_id, limit)

    def query_alarm_rule(self, device_id: str, rule_id: str) -> object:
        return self._observe("query_alarm_rule", device_id, rule_id)

    def query_alarm_signal(self, device_id: str, alarm_id: str) -> object:
        return self._observe("query_alarm_signal", device_id, alarm_id)

    def query_alarm_environment(self, device_id: str, alarm_id: str) -> object:
        return self._observe("query_alarm_environment", device_id, alarm_id)

    def query_alarm_verification(self, device_id: str, alarm_id: str) -> object:
        return self._observe("query_alarm_verification", device_id, alarm_id)

    def query_alarm_correlation(self, device_id: str, alarm_id: str) -> object:
        return self._observe("query_alarm_correlation", device_id, alarm_id)

    def search_alarm_events(
        self, device_id: str, keyword: str | None = None, limit: int = 10
    ) -> object:
        return self._observe("search_alarm_events", device_id, keyword, limit)

    def read_config_snapshot(self, device_id: str) -> object:
        return self._observe("read_config_snapshot", device_id)


def _asset(
    device_id: str = "dev-cam-01",
    *,
    adapter_key: str = "sim",
    capabilities: frozenset[DeviceCapability] = _ALL_CAPABILITIES,
    enabled: bool = True,
) -> DeviceAsset:
    """构造非敏感的合成测试资产，不含真实 IP、账号或凭证。"""
    return DeviceAsset(
        device_id=device_id,
        device_type="camera",
        site_alias="site-a",
        region_alias="region-a",
        adapter_key=adapter_key,
        capabilities=capabilities,
        enabled=enabled,
    )


def _build(
    *,
    adapter_key: str = "sim",
    ready: bool = True,
    enabled: bool = True,
    capabilities: frozenset[DeviceCapability] = _ALL_CAPABILITIES,
) -> tuple[RoutedDeviceGateway, SpyDeviceGateway]:
    spy = SpyDeviceGateway()
    catalog_assets = [
        _asset(device_id, adapter_key=adapter_key, capabilities=capabilities, enabled=enabled)
        for device_id in ("dev-cam-01", "dev-access-01", "dev-alarm-01")
    ]
    registry = InMemoryDeviceAdapterRegistry()
    registry.register("sim", spy, ready=ready)
    return (
        RoutedDeviceGateway(_FakeCatalog(catalog_assets), registry),
        spy,
    )


class _FakeCatalog:
    """最小资产目录 Fake：按构造参数返回资产。"""

    def __init__(self, assets: list[DeviceAsset]) -> None:
        self._assets = {asset.device_id: asset for asset in assets}

    def get(self, device_id: str) -> DeviceAsset:
        asset = self._assets.get(device_id)
        if asset is None:
            raise DeviceAssetNotFoundError(device_id)
        return asset

    def list_enabled(self) -> list[DeviceAsset]:
        return [asset for asset in self._assets.values() if asset.enabled]


# ---------------------------------------------------------------- 契约
def test_satisfies_runtime_checkable_device_gateway_protocol() -> None:
    routed, _ = _build()

    assert isinstance(routed, DeviceGateway)


def test_spy_fake_satisfies_device_gateway_protocol() -> None:
    assert isinstance(SpyDeviceGateway(), DeviceGateway)


# ---------------------------------------------------------------- 确定性路由
def test_assets_route_to_their_own_adapter() -> None:
    static_spy = SpyDeviceGateway("static")
    simulator_spy = SpyDeviceGateway("simulator")
    catalog = _FakeCatalog(
        [
            _asset("dev-static-01", adapter_key="static"),
            _asset("dev-sim-01", adapter_key="simulator"),
        ]
    )
    registry = InMemoryDeviceAdapterRegistry()
    registry.register("static", static_spy, ready=True)
    registry.register("simulator", simulator_spy, ready=True)
    routed = RoutedDeviceGateway(catalog, registry)

    first = routed.query_status("dev-static-01")
    second = routed.query_status("dev-sim-01")

    assert static_spy.calls == [("query_status", ("dev-static-01",))]
    assert simulator_spy.calls == [("query_status", ("dev-sim-01",))]
    assert first is static_spy.default_result
    assert second is simulator_spy.default_result


@pytest.mark.parametrize(("operation", "args"), _ROUTED_CALLS)
def test_every_routed_operation_delegates_exactly_once(
    operation: str, args: tuple[object, ...]
) -> None:
    routed, spy = _build()

    result = getattr(routed, operation)(*args)

    assert len(spy.calls) == 1
    assert spy.calls[0][0] == operation
    assert result is spy.default_result


def test_arguments_are_forwarded_verbatim() -> None:
    routed, spy = _build()

    routed.check_recording_playback("dev-cam-01", "channel-01", _START, _END)
    routed.search_access_events("dev-cam-01", "door-01", "credential-01", 7)

    assert spy.calls == [
        ("check_recording_playback", ("dev-cam-01", "channel-01", _START, _END)),
        ("search_access_events", ("dev-cam-01", "door-01", "credential-01", 7)),
    ]


def test_domain_object_is_returned_untouched() -> None:
    snapshot = DeviceSnapshot(device_id="dev-cam-01", online=True)
    routed, spy = _build()
    spy.results["query_status"] = snapshot

    result = routed.query_status("dev-cam-01")

    assert result is snapshot


# ---------------------------------------------------------------- 路由不可被覆盖
def test_public_signatures_expose_no_routing_parameters() -> None:
    forbidden = {
        "adapter_key",
        "adapter",
        "endpoint",
        "endpoint_alias",
        "credential",
        "credential_reference",
        "base_url",
        "token",
        "api_key",
    }
    for name, member in vars(RoutedDeviceGateway).items():
        if not callable(member) or name.startswith("_"):
            continue
        params = set(inspect.signature(member).parameters)
        leaked = params & forbidden
        assert not leaked, f"{name} 暴露了路由参数: {sorted(leaked)}"


def test_routing_decision_comes_only_from_asset() -> None:
    """同一 Registry 中存在多个 ready Adapter 时，也只按资产 adapter_key 路由。"""
    static_spy = SpyDeviceGateway("static")
    simulator_spy = SpyDeviceGateway("simulator")
    catalog = _FakeCatalog([_asset("dev-cam-01", adapter_key="simulator")])
    registry = InMemoryDeviceAdapterRegistry()
    registry.register("static", static_spy, ready=True)
    registry.register("simulator", simulator_spy, ready=True)
    routed = RoutedDeviceGateway(catalog, registry)

    routed.query_status("dev-cam-01")

    assert simulator_spy.calls == [("query_status", ("dev-cam-01",))]
    assert static_spy.calls == []


# ---------------------------------------------------------------- 受控失败
def test_missing_asset_fails_before_delegation() -> None:
    routed, spy = _build()

    with pytest.raises(DeviceAssetNotFoundError):
        routed.query_status("dev-unknown")

    assert spy.calls == []


def test_disabled_asset_fails_before_delegation() -> None:
    routed, spy = _build(enabled=False)

    with pytest.raises(DeviceAssetDisabledError) as exc_info:
        routed.query_status("dev-cam-01")

    assert exc_info.value.reason == "asset_disabled"
    assert exc_info.value.device_id == "dev-cam-01"
    assert spy.calls == []


@pytest.mark.parametrize(("operation", "capability"), sorted(_METHOD_CAPABILITIES.items()))
def test_missing_capability_fails_before_delegation(
    operation: str, capability: DeviceCapability
) -> None:
    reduced = _ALL_CAPABILITIES - {capability}
    routed, spy = _build(capabilities=reduced)

    with pytest.raises(DeviceCapabilityMissingError) as exc_info:
        getattr(routed, operation)(*_OPERATION_ARGS[operation])

    assert exc_info.value.reason == "capability_missing"
    assert exc_info.value.capability is capability
    assert spy.calls == []


def test_unknown_adapter_key_fails_before_delegation() -> None:
    routed, spy = _build(adapter_key="ghost")

    with pytest.raises(UnknownAdapterKeyError) as exc_info:
        routed.query_status("dev-cam-01")

    assert exc_info.value.adapter_key == "ghost"
    assert spy.calls == []


def test_not_ready_adapter_fails_before_delegation() -> None:
    routed, spy = _build(ready=False)

    with pytest.raises(AdapterNotReadyError):
        routed.query_status("dev-cam-01")

    assert spy.calls == []


def test_adapter_becomes_unready_after_mark_not_ready() -> None:
    spy = SpyDeviceGateway()
    catalog = _FakeCatalog([_asset()])
    registry = InMemoryDeviceAdapterRegistry()
    registry.register("sim", spy, ready=True)
    routed = RoutedDeviceGateway(catalog, registry)
    routed.query_status("dev-cam-01")

    registry.mark_not_ready("sim")

    with pytest.raises(AdapterNotReadyError):
        routed.query_status("dev-cam-01")
    assert len(spy.calls) == 1


def test_no_fallback_broadcast_or_retry_on_downstream_error() -> None:
    primary = SpyDeviceGateway("primary")
    secondary = SpyDeviceGateway("secondary")
    primary.errors["query_status"] = DeviceAdapterError(
        DeviceAdapterErrorKind.UNAVAILABLE, "query_status"
    )
    catalog = _FakeCatalog([_asset("dev-cam-01", adapter_key="primary")])
    registry = InMemoryDeviceAdapterRegistry()
    registry.register("primary", primary, ready=True)
    registry.register("secondary", secondary, ready=True)
    routed = RoutedDeviceGateway(catalog, registry)

    with pytest.raises(DeviceAdapterError):
        routed.query_status("dev-cam-01")
    with pytest.raises(DeviceAdapterError):
        routed.query_status("dev-cam-01")

    assert primary.calls == [("query_status", ("dev-cam-01",)), ("query_status", ("dev-cam-01",))]
    assert secondary.calls == []


def test_downstream_error_classification_preserved_and_called_once() -> None:
    failure = DeviceAdapterError(DeviceAdapterErrorKind.RATE_LIMITED, "query_alarm_signal")
    routed, spy = _build()
    spy.errors["query_alarm_signal"] = failure

    with pytest.raises(DeviceAdapterError) as exc_info:
        routed.query_alarm_signal("dev-cam-01", "alarm-01")

    assert exc_info.value is failure
    assert exc_info.value.kind is DeviceAdapterErrorKind.RATE_LIMITED
    assert exc_info.value.operation == "query_alarm_signal"
    assert len(spy.calls) == 1


def test_downstream_error_message_is_stable_and_leak_free() -> None:
    routed, spy = _build()
    spy.errors["query_status"] = DeviceAdapterError(
        DeviceAdapterErrorKind.AUTHENTICATION, "query_status"
    )

    with pytest.raises(DeviceAdapterError) as exc_info:
        routed.query_status("dev-cam-01")

    assert str(exc_info.value) == (
        "设备只读操作失败: kind=authentication, operation=query_status"
    )


# ---------------------------------------------------------------- 路由上下文
def test_query_credential_rejects_routing_before_delegation() -> None:
    routed, spy = _build()

    with pytest.raises(RoutingContextRequiredError) as exc_info:
        routed.query_credential("credential-01")

    assert exc_info.value.reason == "routing_context_required"
    assert exc_info.value.operation == "query_credential"
    assert spy.calls == []


def test_query_access_policy_rejects_routing_before_delegation() -> None:
    routed, spy = _build()

    with pytest.raises(RoutingContextRequiredError) as exc_info:
        routed.query_access_policy("person-01", "door-01")

    assert exc_info.value.reason == "routing_context_required"
    assert exc_info.value.operation == "query_access_policy"
    assert spy.calls == []


def test_context_errors_share_routing_error_base() -> None:
    routed, _ = _build()

    with pytest.raises(DeviceRoutingError):
        routed.query_credential("credential-01")
    with pytest.raises(DeviceRoutingError):
        routed.query_access_policy("person-01", "door-01")


# ---------------------------------------------------------------- 固定能力映射
def _protocol_device_id_methods() -> set[str]:
    methods: set[str] = set()
    for name, member in vars(DeviceGateway).items():
        if not callable(member) or name.startswith("_"):
            continue
        if "device_id" in inspect.signature(member).parameters:
            methods.add(name)
    return methods


def test_capability_mapping_covers_exactly_all_device_id_methods() -> None:
    assert set(_METHOD_CAPABILITIES) == _protocol_device_id_methods()


def test_capability_mapping_excludes_context_free_methods() -> None:
    assert "query_credential" not in _METHOD_CAPABILITIES
    assert "query_access_policy" not in _METHOD_CAPABILITIES


def test_capability_mapping_is_frozen() -> None:
    assert isinstance(_METHOD_CAPABILITIES, MappingProxyType)
    with pytest.raises(TypeError):
        _METHOD_CAPABILITIES["query_status"] = DeviceCapability.ALARM  # type: ignore[index]


def test_all_mapping_values_are_device_capabilities() -> None:
    for capability in _METHOD_CAPABILITIES.values():
        assert isinstance(capability, DeviceCapability)


# ---------------------------------------------------------------- 依赖守卫
def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def test_modules_do_not_touch_network_env_credentials_or_files() -> None:
    for source_path in SOURCE_PATHS:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported = _imported_modules(tree)
        forbidden = imported - ALLOWED_IMPORTS
        assert not forbidden, f"{source_path.name} 引入了未授权模块: {sorted(forbidden)}"
