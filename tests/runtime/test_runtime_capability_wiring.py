"""Phase 9C-2B：能力判定接入正式 Runtime 验收。

正式 Runtime 的 `supported_fault_types` 必须由实际装配结果推导
（资产 + Adapter readiness + 固定自检集合 + 资产能力 + 工具覆盖），
Service 只接收 `resolve_fault_support()` 的输出；旧常量不再是真实输入。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from security_diagnosis_harness.adapters.device_assets import InMemoryDeviceAssetCatalog
from security_diagnosis_harness.adapters.device_gateway.routed import RoutedDeviceGateway
from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.application.errors import UnsupportedFaultTypeError
from security_diagnosis_harness.application.runtime_capabilities import (
    FaultSupportReason,
    RuntimeCapabilitySupport,
)
from security_diagnosis_harness.bootstrap.container import build_container
from security_diagnosis_harness.config import RepositoryMode, RuntimeSettings
from security_diagnosis_harness.domain.device_integration import DeviceAsset, DeviceCapability
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.ports.device_gateway import DeviceGateway
from security_diagnosis_harness.runtime import (
    DEFAULT_RUNTIME_ADAPTER_KEY,
    build_default_runtime_asset,
    build_runtime_container,
)
from security_diagnosis_harness.tools.contracts import BaseTool, ToolExecutionResult
from security_diagnosis_harness.tools.device_channel import DeviceChannelTool
from security_diagnosis_harness.tools.device_status import DeviceStatusTool
from security_diagnosis_harness.tools.device_stream import DeviceStreamTool
from security_diagnosis_harness.tools.registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = REPO_ROOT / "scripts" / "probe_phase9_runtime_capabilities.py"

NON_CAMERA_FAULT_TYPES = (
    SecurityFaultType.RECORDING_MISSING,
    SecurityFaultType.ACCESS_CARD_FAILED,
    SecurityFaultType.ALARM_FALSE_POSITIVE,
)


def _memory_settings() -> RuntimeSettings:
    return RuntimeSettings(repository_mode="memory")


def _sqlite_settings(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings(
        repository_mode="sqlite",
        database_url=f"sqlite:///{(tmp_path / 'wiring.db').as_posix()}",
        auto_migrate=True,
    )


def _verdict_reason(
    container: object, fault_type: SecurityFaultType
) -> FaultSupportReason | None:
    for verdict in container.capability_support.verdicts:  # type: ignore[attr-defined]
        if verdict.fault_type is fault_type:
            return verdict.reason
    return None


def _asset(
    *,
    enabled: bool = True,
    capabilities: frozenset[DeviceCapability] | None = None,
    adapter_key: str = DEFAULT_RUNTIME_ADAPTER_KEY,
) -> DeviceAsset:
    return DeviceAsset(
        device_id="camera-3f-001",
        device_type="camera",
        adapter_key=adapter_key,
        capabilities=(
            capabilities
            if capabilities is not None
            else build_default_runtime_asset().capabilities
        ),
        enabled=enabled,
    )


# ---------------------------------------------------------------- 默认装配
def test_default_gateway_is_routed_not_static():
    with build_runtime_container(_memory_settings()) as runtime:
        assert isinstance(runtime.gateway, DeviceGateway)
        assert isinstance(runtime.gateway, RoutedDeviceGateway)
        assert not isinstance(runtime.gateway, StaticDeviceGateway)


def test_default_support_is_camera_only_and_derived():
    with build_runtime_container(_memory_settings()) as runtime:
        assert runtime.capability_support.supported_fault_types == (
            SecurityFaultType.CAMERA_BLACK_SCREEN,
        )
        for fault_type in NON_CAMERA_FAULT_TYPES:
            assert _verdict_reason(runtime, fault_type) is FaultSupportReason.MISSING_CAPABILITY


def test_service_supported_types_come_from_resolver_output():
    with build_runtime_container(_memory_settings()) as runtime:
        resolver_output = frozenset(runtime.capability_support.supported_fault_types)
        assert runtime.service.supported_fault_types == resolver_output
        assert runtime.service.is_fault_type_supported(SecurityFaultType.CAMERA_BLACK_SCREEN)
        for fault_type in NON_CAMERA_FAULT_TYPES:
            assert not runtime.service.is_fault_type_supported(fault_type)


def test_container_exposes_capability_wiring_fields():
    with build_runtime_container(_memory_settings()) as runtime:
        assert isinstance(runtime.asset_catalog, InMemoryDeviceAssetCatalog)
        assert runtime.adapter_registry.ready_adapter_keys() == (
            DEFAULT_RUNTIME_ADAPTER_KEY,
        )
        assert isinstance(runtime.capability_support, RuntimeCapabilitySupport)

        asset = runtime.asset_catalog.get("camera-3f-001")
        assert asset.enabled is True
        assert asset.adapter_key == DEFAULT_RUNTIME_ADAPTER_KEY
        assert {
            DeviceCapability.STATUS,
            DeviceCapability.CHANNEL,
            DeviceCapability.STREAM,
        } <= asset.capabilities

        # StaticDeviceGateway 只作为 Router 内部 ready Adapter 暴露。
        assert not hasattr(runtime.adapter_registry, "get_ready")


# ---------------------------------------------------------------- 摄像头闭环
def test_default_camera_loop_still_runs(tmp_path: Path):
    with build_runtime_container(_sqlite_settings(tmp_path)) as runtime:
        case = runtime.service.create_diagnosis(
            device_id="camera-3f-001",
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="probe",
        )
        result = runtime.service.run_diagnosis(case.diagnosis_id)
        final = runtime.service.get_diagnosis(case.diagnosis_id)

    assert result.ok is True
    assert final.status is SecurityDiagnosisStatus.WAITING_FOR_CONFIRMATION
    assert final.conclusion is not None


@pytest.mark.parametrize("fault_type", NON_CAMERA_FAULT_TYPES)
def test_non_camera_fault_types_still_rejected(fault_type):
    with build_runtime_container(_memory_settings()) as runtime:
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.create_diagnosis(
                device_id="dev-1",
                fault_type=fault_type,
                reporter="probe",
            )


# ---------------------------------------------------------------- 装配缺陷一律拒绝
def test_empty_asset_catalog_rejects_camera():
    with build_runtime_container(_memory_settings(), assets=()) as runtime:
        assert _verdict_reason(runtime, SecurityFaultType.CAMERA_BLACK_SCREEN) is (
            FaultSupportReason.NO_ENABLED_ASSET
        )
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.create_diagnosis(
                device_id="camera-3f-001",
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                reporter="probe",
            )


def test_disabled_asset_rejects_camera():
    with build_runtime_container(_memory_settings(), assets=(_asset(enabled=False),)) as runtime:
        assert _verdict_reason(runtime, SecurityFaultType.CAMERA_BLACK_SCREEN) is (
            FaultSupportReason.NO_ENABLED_ASSET
        )
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.create_diagnosis(
                device_id="camera-3f-001",
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                reporter="probe",
            )


def test_adapter_not_ready_rejects_camera():
    with build_runtime_container(_memory_settings(), adapter_ready=False) as runtime:
        assert _verdict_reason(runtime, SecurityFaultType.CAMERA_BLACK_SCREEN) is (
            FaultSupportReason.ADAPTER_NOT_READY
        )
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.create_diagnosis(
                device_id="camera-3f-001",
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                reporter="probe",
            )


def test_self_check_failure_rejects_camera():
    with build_runtime_container(
        _memory_settings(), self_check_passed_adapter_keys=()
    ) as runtime:
        assert _verdict_reason(runtime, SecurityFaultType.CAMERA_BLACK_SCREEN) is (
            FaultSupportReason.SELF_CHECK_FAILED
        )
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.create_diagnosis(
                device_id="camera-3f-001",
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                reporter="probe",
            )


def test_missing_capability_rejects_camera():
    limited = frozenset({DeviceCapability.STATUS, DeviceCapability.CHANNEL})
    assets = (_asset(capabilities=limited),)
    with build_runtime_container(_memory_settings(), assets=assets) as runtime:
        assert _verdict_reason(runtime, SecurityFaultType.CAMERA_BLACK_SCREEN) is (
            FaultSupportReason.MISSING_CAPABILITY
        )
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.create_diagnosis(
                device_id="camera-3f-001",
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                reporter="probe",
            )


def test_asset_adapter_key_cannot_override_registry():
    """资产指向未注册/未就绪的 adapter key 时，不得获得支持。"""
    with build_runtime_container(
        _memory_settings(), assets=(_asset(adapter_key="ghost-adapter"),)
    ) as runtime:
        assert _verdict_reason(runtime, SecurityFaultType.CAMERA_BLACK_SCREEN) is (
            FaultSupportReason.ADAPTER_NOT_READY
        )
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.create_diagnosis(
                device_id="camera-3f-001",
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                reporter="probe",
            )


# ---------------------------------------------------------------- 工具覆盖
def _camera_registry_without_platform_pull() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (DeviceStatusTool(), DeviceChannelTool(), DeviceStreamTool()):
        registry.register(tool)
    return registry


def test_partial_tool_coverage_rejects_camera():
    """某域必要工具只要缺一个，该域就不得声明支持。"""
    registry = _camera_registry_without_platform_pull()
    with build_runtime_container(_memory_settings(), registry=registry) as runtime:
        assert _verdict_reason(runtime, SecurityFaultType.CAMERA_BLACK_SCREEN) is (
            FaultSupportReason.TOOL_COVERAGE_MISSING
        )
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.create_diagnosis(
                device_id="camera-3f-001",
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                reporter="probe",
            )


class _EmptyArguments(BaseModel):
    pass


class _PullToolWithoutCameraSupport(BaseTool):
    """注册名合法但不允许摄像头黑屏域的假工具。"""

    name = "platform__query_pull_status"
    description = "probe tool that never allows camera domain"
    supported_fault_types = frozenset({SecurityFaultType.RECORDING_MISSING})
    input_model = _EmptyArguments

    def _execute(self, arguments: BaseModel, context: object) -> ToolExecutionResult:
        raise AssertionError("探针工具不应被执行")


def test_tool_must_allow_the_fault_type_itself():
    """工具存在但声明不支持该故障域时，同样不算覆盖。"""
    registry = _camera_registry_without_platform_pull()
    registry.register(_PullToolWithoutCameraSupport())  # type: ignore[arg-type]

    with build_runtime_container(_memory_settings(), registry=registry) as runtime:
        assert _verdict_reason(runtime, SecurityFaultType.CAMERA_BLACK_SCREEN) is (
            FaultSupportReason.TOOL_COVERAGE_MISSING
        )
        with pytest.raises(UnsupportedFaultTypeError):
            runtime.service.create_diagnosis(
                device_id="camera-3f-001",
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                reporter="probe",
            )


# ---------------------------------------------------------------- 仓储与生命周期
def test_capability_support_matches_across_repository_modes(tmp_path: Path):
    memory_verdicts = None
    sqlite_verdicts = None
    with build_runtime_container(_memory_settings()) as runtime:
        assert runtime.settings.repository_mode is RepositoryMode.MEMORY
        memory_verdicts = runtime.capability_support.verdicts
    with build_runtime_container(_sqlite_settings(tmp_path)) as runtime:
        assert runtime.settings.repository_mode is RepositoryMode.SQLITE
        sqlite_verdicts = runtime.capability_support.verdicts

    assert memory_verdicts == sqlite_verdicts


def test_close_is_idempotent_and_engine_owner_not_regressed(tmp_path: Path):
    from security_diagnosis_harness import runtime as runtime_module

    runtime = build_runtime_container(_sqlite_settings(tmp_path))
    engine = runtime.engine
    assert engine is not None

    runtime.close()
    runtime.close()

    assert engine.pool.checkedout() == 0
    assert runtime.closed is True
    # 不允许出现丢失 Engine owner 的 builder 入口。
    assert not hasattr(runtime_module, "build_runtime_service")


# ---------------------------------------------------------------- 评测 Container 不变
def test_evaluation_container_keeps_unlimited_support():
    container = build_container()
    assert container.service.supported_fault_types is None
    case = container.service.create_diagnosis(
        device_id="dev-1",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter="probe",
    )
    assert case.fault_type is SecurityFaultType.CAMERA_BLACK_SCREEN


# ---------------------------------------------------------------- 模块级副作用
def test_importing_runtime_module_has_no_build_side_effects(tmp_path: Path):
    completed = subprocess.run(
        [sys.executable, "-c", "import security_diagnosis_harness.runtime"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert list(tmp_path.rglob("*.db")) == []
    assert not (tmp_path / "data").exists()


# ---------------------------------------------------------------- 探针输出卫生
def test_probe_output_has_no_secrets_or_paths(tmp_path: Path):
    completed = subprocess.run(
        [sys.executable, str(PROBE)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    output = completed.stdout
    lowered = output.lower()
    for forbidden in (
        "endpoint",
        "credential",
        "password",
        "token",
        "secret",
        "http",
        "://",
        "@",
    ):
        assert forbidden not in lowered, forbidden
    # 绝对路径：Windows 盘符或任何反斜杠路径片段。
    assert re.search(r"[A-Za-z]:[\\/]", output) is None
    assert "\\" not in output
    # 探针必须报告推导结果而不是硬编码常量。
    assert '"gateway_is_routed": true' in output
    assert '"camera_run_ok": true' in output
    assert '"non_camera_create_rejected": true' in output
