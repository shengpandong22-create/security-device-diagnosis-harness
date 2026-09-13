"""能力驱动故障域支持判定（Phase 9C-2A）验收。

resolver 为纯函数：不访问环境变量、网络、数据库、文件、真实模型或设备；
disabled 资产不贡献能力；禁止把多个资产的 capabilities 拼接成完整集合。
"""

from __future__ import annotations

import inspect
import types

import pytest
from pydantic import ValidationError

from security_diagnosis_harness.application import runtime_capabilities
from security_diagnosis_harness.application.runtime_capabilities import (
    FaultDomainRequirement,
    FaultSupportReason,
    FaultSupportRequest,
    FaultSupportVerdict,
    RuntimeCapabilitySupport,
    resolve_fault_support,
)
from security_diagnosis_harness.domain.device_integration import DeviceAsset, DeviceCapability
from security_diagnosis_harness.domain.enums import SecurityFaultType

ADAPTER_KEY = "camera-adapter"
FULL_CAMERA_CAPS = frozenset({DeviceCapability.STATUS, DeviceCapability.STREAM})


def _asset(
    device_id: str = "device-1",
    adapter_key: str = ADAPTER_KEY,
    capabilities: frozenset[DeviceCapability] = FULL_CAMERA_CAPS,
    enabled: bool = True,
) -> DeviceAsset:
    return DeviceAsset(
        device_id=device_id,
        device_type="camera",
        adapter_key=adapter_key,
        capabilities=capabilities,
        enabled=enabled,
    )


def _resolve(assets, **overrides) -> RuntimeCapabilitySupport:
    kwargs = {
        "ready_adapter_keys": {ADAPTER_KEY},
        "required_capabilities_by_fault_type": {
            SecurityFaultType.CAMERA_BLACK_SCREEN: FULL_CAMERA_CAPS,
        },
        "tool_supported_fault_types": {SecurityFaultType.CAMERA_BLACK_SCREEN},
        "self_check_passed_adapter_keys": {ADAPTER_KEY},
    }
    kwargs.update(overrides)
    return resolve_fault_support(assets, **kwargs)


def _single_verdict(result: RuntimeCapabilitySupport) -> FaultSupportVerdict:
    assert len(result.verdicts) == 1
    return result.verdicts[0]


def test_full_capability_support() -> None:
    result = _resolve([_asset()])

    assert result.supported_fault_types == (SecurityFaultType.CAMERA_BLACK_SCREEN,)
    verdict = _single_verdict(result)
    assert verdict.supported is True
    assert verdict.reason is FaultSupportReason.SUPPORTED


def test_disabled_asset_does_not_contribute() -> None:
    enabled_asset = _asset(device_id="device-a", capabilities=frozenset({DeviceCapability.STATUS}))
    disabled_asset = _asset(device_id="device-b", enabled=False)

    result = _resolve([enabled_asset, disabled_asset])

    # enabled 资产缺 STREAM；disabled 资产即使能力完整也不允许补齐
    verdict = _single_verdict(result)
    assert verdict.supported is False
    assert verdict.reason is FaultSupportReason.MISSING_CAPABILITY


def test_no_enabled_asset() -> None:
    result = _resolve([_asset(enabled=False)])

    verdict = _single_verdict(result)
    assert verdict.supported is False
    assert verdict.reason is FaultSupportReason.NO_ENABLED_ASSET


def test_adapter_not_ready() -> None:
    result = _resolve([_asset()], ready_adapter_keys=set())

    verdict = _single_verdict(result)
    assert verdict.supported is False
    assert verdict.reason is FaultSupportReason.ADAPTER_NOT_READY


def test_self_check_failed() -> None:
    result = _resolve([_asset()], self_check_passed_adapter_keys=set())

    verdict = _single_verdict(result)
    assert verdict.supported is False
    assert verdict.reason is FaultSupportReason.SELF_CHECK_FAILED


def test_missing_capability() -> None:
    result = _resolve([_asset(capabilities=frozenset({DeviceCapability.STATUS}))])

    verdict = _single_verdict(result)
    assert verdict.supported is False
    assert verdict.reason is FaultSupportReason.MISSING_CAPABILITY


def test_tool_coverage_missing() -> None:
    result = _resolve([_asset()], tool_supported_fault_types=set())

    verdict = _single_verdict(result)
    assert verdict.supported is False
    assert verdict.reason is FaultSupportReason.TOOL_COVERAGE_MISSING


def test_any_single_fully_satisfied_asset_supports() -> None:
    partial_asset = _asset(device_id="device-a", capabilities=frozenset({DeviceCapability.STATUS}))
    complete_asset = _asset(device_id="device-b")

    result = _resolve([partial_asset, complete_asset])

    assert result.supported_fault_types == (SecurityFaultType.CAMERA_BLACK_SCREEN,)
    assert _single_verdict(result).reason is FaultSupportReason.SUPPORTED


def test_no_cross_asset_capability_stitching() -> None:
    status_asset = _asset(device_id="device-a", capabilities=frozenset({DeviceCapability.STATUS}))
    stream_asset = _asset(device_id="device-b", capabilities=frozenset({DeviceCapability.STREAM}))

    result = _resolve([status_asset, stream_asset])

    # 两个资产各持一半能力，拼接后的完整集合不算数
    verdict = _single_verdict(result)
    assert verdict.supported is False
    assert verdict.reason is FaultSupportReason.MISSING_CAPABILITY


def test_verdicts_stably_sorted_by_fault_type_value() -> None:
    requirements = {
        SecurityFaultType.RECORDING_MISSING: frozenset(),
        SecurityFaultType.CAMERA_BLACK_SCREEN: frozenset(),
        SecurityFaultType.ALARM_FALSE_POSITIVE: frozenset(),
        SecurityFaultType.ACCESS_CARD_FAILED: frozenset(),
    }
    result = _resolve(
        [_asset()],
        required_capabilities_by_fault_type=requirements,
        tool_supported_fault_types=set(SecurityFaultType),
    )

    assert [verdict.fault_type for verdict in result.verdicts] == sorted(
        requirements, key=lambda fault_type: fault_type.value
    )
    assert result.supported_fault_types == tuple(
        sorted(requirements, key=lambda fault_type: fault_type.value)
    )


def test_input_mutation_isolation() -> None:
    assets = [_asset()]
    ready_adapter_keys = {ADAPTER_KEY}
    required = {
        SecurityFaultType.CAMERA_BLACK_SCREEN: {DeviceCapability.STATUS, DeviceCapability.STREAM}
    }
    tool_supported = {SecurityFaultType.CAMERA_BLACK_SCREEN}
    self_check_passed = {ADAPTER_KEY}

    result = resolve_fault_support(
        assets,
        ready_adapter_keys,
        required,
        tool_supported,
        self_check_passed,
    )
    assert result.supported_fault_types == (SecurityFaultType.CAMERA_BLACK_SCREEN,)

    # 调用方随后修改输入集合，不得污染既有结果
    assets.clear()
    ready_adapter_keys.clear()
    required.clear()
    tool_supported.clear()
    self_check_passed.clear()

    assert result.verdicts == (
        FaultSupportVerdict(
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            supported=True,
            reason=FaultSupportReason.SUPPORTED,
        ),
    )


def test_fault_support_request_freezes_inputs() -> None:
    requirements = {
        SecurityFaultType.CAMERA_BLACK_SCREEN: FaultDomainRequirement(
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            required_capabilities=FULL_CAMERA_CAPS,
            tool_coverage_ready=True,
        )
    }
    request = FaultSupportRequest(
        assets=[_asset()],
        ready_adapter_keys={ADAPTER_KEY},
        requirements=requirements,
        self_check_passed_adapter_keys={ADAPTER_KEY},
    )

    with pytest.raises(ValidationError):
        request.assets = ()
    with pytest.raises(TypeError):
        request.requirements[SecurityFaultType.CAMERA_BLACK_SCREEN] = requirements[  # type: ignore[index]
            SecurityFaultType.CAMERA_BLACK_SCREEN
        ]

    requirements.clear()
    assert SecurityFaultType.CAMERA_BLACK_SCREEN in request.requirements
    assert request.requirements[SecurityFaultType.CAMERA_BLACK_SCREEN].required_capabilities == (
        FULL_CAMERA_CAPS
    )


def test_empty_inputs() -> None:
    result = resolve_fault_support(
        [],
        set(),
        {},
        set(),
        set(),
    )

    assert result.verdicts == ()
    assert result.supported_fault_types == ()


def test_empty_assets_report_no_enabled_asset() -> None:
    result = _resolve(
        [],
        required_capabilities_by_fault_type={SecurityFaultType.CAMERA_BLACK_SCREEN: frozenset()},
    )

    verdict = _single_verdict(result)
    assert verdict.supported is False
    assert verdict.reason is FaultSupportReason.NO_ENABLED_ASSET


def test_all_four_existing_fault_types_usable_as_keys() -> None:
    requirements = {fault_type: frozenset() for fault_type in SecurityFaultType}
    result = _resolve(
        [_asset()],
        required_capabilities_by_fault_type=requirements,
        tool_supported_fault_types=set(SecurityFaultType),
    )

    assert result.supported_fault_types == tuple(
        sorted(SecurityFaultType, key=lambda fault_type: fault_type.value)
    )
    assert all(verdict.reason is FaultSupportReason.SUPPORTED for verdict in result.verdicts)


_FORBIDDEN_SOURCE_TOKENS = (
    "os.environ",
    "getenv",
    "socket",
    "subprocess",
    "httpx",
    "requests",
    "sqlalchemy",
    "sqlite3",
    "alembic",
    "open(",
    "Path(",
    "popen",
)
_FORBIDDEN_MODULES = {"os", "socket", "subprocess", "httpx", "requests", "sqlalchemy", "sqlite3"}


def test_module_has_no_infrastructure_dependencies() -> None:
    source = inspect.getsource(runtime_capabilities)
    for token in _FORBIDDEN_SOURCE_TOKENS:
        assert token not in source, f"模块源码不允许出现 {token!r}"

    bound_modules = {
        value.__name__
        for value in vars(runtime_capabilities).values()
        if isinstance(value, types.ModuleType)
    }
    assert not bound_modules & _FORBIDDEN_MODULES
