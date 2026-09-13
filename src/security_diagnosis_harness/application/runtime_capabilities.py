"""能力驱动的故障域支持判定（Phase 9C-2A）。

纯决策核心：根据启用资产、Adapter 就绪状态、固定自检结果、资产能力与工具覆盖，
计算正式 Runtime 可声明支持的故障域。不访问环境变量、网络、数据库、文件、
真实模型或设备；不修改正式 Runtime，也不提前宣称任何故障域开放。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, field_validator

from security_diagnosis_harness.domain.device_integration import DeviceAsset, DeviceCapability
from security_diagnosis_harness.domain.enums import SecurityFaultType


class FaultSupportReason(StrEnum):
    """稳定的故障域支持判定原因码。"""

    NO_ENABLED_ASSET = "no_enabled_asset"
    ADAPTER_NOT_READY = "adapter_not_ready"
    SELF_CHECK_FAILED = "self_check_failed"
    MISSING_CAPABILITY = "missing_capability"
    TOOL_COVERAGE_MISSING = "tool_coverage_missing"
    SUPPORTED = "supported"


class FaultDomainRequirement(BaseModel):
    """单个故障域的判定需求（不可变）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fault_type: SecurityFaultType
    required_capabilities: frozenset[DeviceCapability] = frozenset()
    tool_coverage_ready: bool = False


class FaultSupportRequest(BaseModel):
    """resolver 的不可变输入快照；所有集合在构造时复制/冻结。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assets: tuple[DeviceAsset, ...] = ()
    ready_adapter_keys: frozenset[str] = frozenset()
    requirements: Mapping[SecurityFaultType, FaultDomainRequirement] = MappingProxyType({})
    self_check_passed_adapter_keys: frozenset[str] = frozenset()

    @field_validator("requirements", mode="after")
    @classmethod
    def _freeze_requirements(
        cls,
        value: Mapping[SecurityFaultType, FaultDomainRequirement],
    ) -> Mapping[SecurityFaultType, FaultDomainRequirement]:
        return MappingProxyType(dict(value))


class FaultSupportVerdict(BaseModel):
    """单个故障域的支持判定结果（不可变）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fault_type: SecurityFaultType
    supported: bool
    reason: FaultSupportReason


class RuntimeCapabilitySupport(BaseModel):
    """正式 Runtime 的故障域支持判定输出（不可变，按 fault type value 稳定排序）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdicts: tuple[FaultSupportVerdict, ...] = ()

    @property
    def supported_fault_types(self) -> tuple[SecurityFaultType, ...]:
        return tuple(verdict.fault_type for verdict in self.verdicts if verdict.supported)


def resolve_fault_support(
    assets: Iterable[DeviceAsset],
    ready_adapter_keys: Iterable[str],
    required_capabilities_by_fault_type: Mapping[SecurityFaultType, Iterable[DeviceCapability]],
    tool_supported_fault_types: Iterable[SecurityFaultType],
    self_check_passed_adapter_keys: Iterable[str],
) -> RuntimeCapabilitySupport:
    """纯函数：计算正式 Runtime 可声明支持的故障域。

    对 `required_capabilities_by_fault_type` 中的每个 fault type 依次判定（按
    reason 优先级从高到低，命中即停）：

    1. 不存在任何 enabled 资产 -> `no_enabled_asset`；
    2. enabled 资产中没有 adapter_key 就绪的 -> `adapter_not_ready`；
    3. 就绪 Adapter 中没有固定自检通过的 -> `self_check_failed`；
    4. 没有任何单一资产完整覆盖全部 required capabilities -> `missing_capability`
       （disabled 资产不参与判定，也禁止把多个资产的 capabilities 拼接成完整集合）；
    5. 工具集合未声明覆盖该 fault type -> `tool_coverage_missing`；
    6. 全部满足 -> `supported`。

    多个资产中任一资产完整满足上述全部条件即可支持。输出按 fault type value
    稳定排序；所有输入集合在构造时复制/冻结，调用方后续修改不影响结果。
    """
    tool_supported = frozenset(tool_supported_fault_types)
    request = FaultSupportRequest(
        assets=assets,
        ready_adapter_keys=ready_adapter_keys,
        requirements={
            fault_type: FaultDomainRequirement(
                fault_type=fault_type,
                required_capabilities=frozenset(
                    required_capabilities_by_fault_type.get(fault_type, ())
                ),
                tool_coverage_ready=fault_type in tool_supported,
            )
            for fault_type in required_capabilities_by_fault_type
        },
        self_check_passed_adapter_keys=self_check_passed_adapter_keys,
    )
    verdicts = tuple(
        _evaluate_fault_support(request, fault_type)
        for fault_type in sorted(request.requirements, key=lambda item: item.value)
    )
    return RuntimeCapabilitySupport(verdicts=verdicts)


def _evaluate_fault_support(
    request: FaultSupportRequest,
    fault_type: SecurityFaultType,
) -> FaultSupportVerdict:
    requirement = request.requirements[fault_type]
    enabled_assets = [asset for asset in request.assets if asset.enabled]
    if not enabled_assets:
        return _verdict(fault_type, FaultSupportReason.NO_ENABLED_ASSET)
    ready_assets = [
        asset for asset in enabled_assets if asset.adapter_key in request.ready_adapter_keys
    ]
    if not ready_assets:
        return _verdict(fault_type, FaultSupportReason.ADAPTER_NOT_READY)
    self_checked_assets = [
        asset
        for asset in ready_assets
        if asset.adapter_key in request.self_check_passed_adapter_keys
    ]
    if not self_checked_assets:
        return _verdict(fault_type, FaultSupportReason.SELF_CHECK_FAILED)
    if not any(
        requirement.required_capabilities <= asset.capabilities for asset in self_checked_assets
    ):
        return _verdict(fault_type, FaultSupportReason.MISSING_CAPABILITY)
    if not requirement.tool_coverage_ready:
        return _verdict(fault_type, FaultSupportReason.TOOL_COVERAGE_MISSING)
    return _verdict(fault_type, FaultSupportReason.SUPPORTED)


def _verdict(fault_type: SecurityFaultType, reason: FaultSupportReason) -> FaultSupportVerdict:
    return FaultSupportVerdict(
        fault_type=fault_type,
        supported=reason is FaultSupportReason.SUPPORTED,
        reason=reason,
    )
