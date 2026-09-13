"""设备工具受控降级映射（Phase 9C-3）。

把 Router / Registry / Catalog / Adapter 的受控异常映射成稳定、机器可读的
failure kind，并生成不含 device_id、adapter_key、endpoint、底层异常原文的
稳定安全文案。

约束：

- 失败结果的 `metadata` 只保存低基数的 `failure_kind` / `operation`；
- 未识别异常统一映射为 `unexpected`，绝不拼接 `str(exc)`；
- 失败结果不携带任何 EvidenceDraft；
- 不访问网络、配置、环境变量或凭证。
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from security_diagnosis_harness.adapters.device_gateway.routed import (
    DeviceAssetDisabledError,
    DeviceCapabilityMissingError,
    RoutingContextRequiredError,
)
from security_diagnosis_harness.domain.device_integration import DeviceAdapterError
from security_diagnosis_harness.ports.device_adapters import (
    AdapterNotReadyError,
    UnknownAdapterKeyError,
)
from security_diagnosis_harness.ports.device_assets import DeviceAssetNotFoundError
from security_diagnosis_harness.ports.device_gateway import (
    DeviceGatewayDataError,
    DeviceNotFoundError,
)
from security_diagnosis_harness.tools.contracts import ToolExecutionResult

__all__ = [
    "DeviceFailureKind",
    "classify_device_failure",
    "collect_device_failure_kinds",
    "device_failure_message",
    "device_failure_result",
]


class DeviceFailureKind(StrEnum):
    """稳定的设备调用失败分类，机器可读且低基数。"""

    ASSET_NOT_FOUND = "asset_not_found"
    ASSET_DISABLED = "asset_disabled"
    CAPABILITY_MISSING = "capability_missing"
    ADAPTER_NOT_READY = "adapter_not_ready"
    AUTHENTICATION = "authentication"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INVALID_RESPONSE = "invalid_response"
    ROUTING_CONTEXT_REQUIRED = "routing_context_required"
    UNEXPECTED = "unexpected"


# capability 类失败：无 Evidence 时允许进入 inconclusive（不猜根因）。
CAPABILITY_FAILURE_KINDS: frozenset[DeviceFailureKind] = frozenset(
    {DeviceFailureKind.CAPABILITY_MISSING, DeviceFailureKind.UNSUPPORTED_CAPABILITY}
)

# 每类失败给 LLM 的稳定安全文案；与具体设备、Adapter、底层异常完全无关。
_SAFE_MESSAGES: dict[DeviceFailureKind, str] = {
    DeviceFailureKind.ASSET_NOT_FOUND: "设备资产不存在或未登记，无法执行该查询",
    DeviceFailureKind.ASSET_DISABLED: "设备资产已禁用，查询被拒绝",
    DeviceFailureKind.CAPABILITY_MISSING: "设备资产未声明该查询所需能力，查询被拒绝",
    DeviceFailureKind.ADAPTER_NOT_READY: "设备适配器未就绪，查询暂不可用",
    DeviceFailureKind.AUTHENTICATION: "设备认证失败，查询未执行",
    DeviceFailureKind.TIMEOUT: "设备查询超时",
    DeviceFailureKind.RATE_LIMITED: "设备查询被限流",
    DeviceFailureKind.UNAVAILABLE: "设备服务暂不可用",
    DeviceFailureKind.UNSUPPORTED_CAPABILITY: "设备不支持该查询能力",
    DeviceFailureKind.INVALID_RESPONSE: "设备返回数据无效",
    DeviceFailureKind.ROUTING_CONTEXT_REQUIRED: "该查询缺少设备上下文，无法路由",
    DeviceFailureKind.UNEXPECTED: "设备查询发生未预期错误",
}


def classify_device_failure(exc: BaseException) -> DeviceFailureKind:
    """把受控异常映射为稳定 failure kind；未识别异常统一为 unexpected。"""
    if isinstance(exc, (DeviceAssetNotFoundError, DeviceNotFoundError)):
        return DeviceFailureKind.ASSET_NOT_FOUND
    if isinstance(exc, DeviceAssetDisabledError):
        return DeviceFailureKind.ASSET_DISABLED
    if isinstance(exc, DeviceCapabilityMissingError):
        return DeviceFailureKind.CAPABILITY_MISSING
    if isinstance(exc, RoutingContextRequiredError):
        return DeviceFailureKind.ROUTING_CONTEXT_REQUIRED
    if isinstance(exc, (AdapterNotReadyError, UnknownAdapterKeyError)):
        return DeviceFailureKind.ADAPTER_NOT_READY
    if isinstance(exc, DeviceGatewayDataError):
        return DeviceFailureKind.INVALID_RESPONSE
    if isinstance(exc, DeviceAdapterError):
        try:
            return DeviceFailureKind(exc.kind.value)
        except ValueError:
            return DeviceFailureKind.UNEXPECTED
    return DeviceFailureKind.UNEXPECTED


def device_failure_message(kind: DeviceFailureKind) -> str:
    """返回该失败分类的稳定安全文案（不含任何标识或异常原文）。"""
    return _SAFE_MESSAGES[kind]


def device_failure_result(
    tool_name: str,
    kind: DeviceFailureKind,
    operation: str,
) -> ToolExecutionResult:
    """构造受控降级失败结果：只有稳定文案与低基数 metadata，无 Evidence。"""
    return ToolExecutionResult(
        tool_name=tool_name,
        ok=False,
        error=device_failure_message(kind),
        metadata={"failure_kind": kind.value, "operation": operation},
    )


def collect_device_failure_kinds(
    tool_results: Iterable[ToolExecutionResult],
) -> tuple[str, ...]:
    """从工具结果中收集稳定 failure kind；按 kind 值排序去重。"""
    kinds: set[str] = set()
    for result in tool_results:
        if result.ok:
            continue
        kind = result.metadata.get("failure_kind")
        if isinstance(kind, str):
            kinds.add(kind)
    return tuple(sorted(kinds))
