"""Tool Registry：工具注册与受控执行的唯一入口。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.tools.contracts import (
    BaseTool,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolPermission,
    ToolRiskLevel,
    failure_result,
)


class ToolAlreadyRegisteredError(Exception):
    """工具重复注册。"""


class ToolNotFoundError(Exception):
    """工具未注册。"""


class ToolRegistry:
    """工具注册表。

    所有工具必须通过这里注册和执行；Registry 负责权限、故障类型、
    参数和异常的受控处理，异常不允许随意冒泡到调用方。
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册工具。重复注册直接拒绝。"""
        if not tool.name:
            raise ToolAlreadyRegisteredError("工具名不能为空")
        if tool.name in self._tools:
            raise ToolAlreadyRegisteredError(f"工具 {tool.name} 已注册，不允许重复注册")
        if tool.risk_level is not ToolRiskLevel.READ_ONLY:
            raise ValueError(f"Phase 0 只允许注册 READ_ONLY 工具: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, tool_name: str) -> BaseTool:
        """按名字取工具，未注册则抛出。"""
        try:
            return self._tools[tool_name]
        except KeyError as exc:
            raise ToolNotFoundError(f"工具 {tool_name} 未注册") from exc

    def has(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def names(self) -> list[str]:
        """已注册工具名，按注册顺序。"""
        return list(self._tools)

    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """受控执行工具，任何拒绝或异常都转成失败结果。"""
        if not self.has(tool_name):
            return failure_result(tool_name, f"工具 {tool_name} 未注册")

        tool = self._tools[tool_name]

        missing_permissions = tool.required_permissions - context.permissions
        if missing_permissions:
            return failure_result(
                tool_name,
                f"缺少权限: {sorted(permission.value for permission in missing_permissions)}",
            )

        if (
            tool.supported_fault_types is not None
            and context.fault_type not in tool.supported_fault_types
        ):
            return failure_result(
                tool_name,
                f"故障类型 {context.fault_type.value} 不被工具 {tool_name} 支持",
            )

        guarded_context = context.model_copy(update={"invoked_by_registry": True})
        try:
            result = tool.run(arguments, guarded_context)
        except Exception as exc:  # noqa: BLE001 - 受控失败，不允许异常冒泡
            return failure_result(tool_name, f"工具执行失败: {exc}")

        if not result.ok:
            # 工具失败不能被包装成 Evidence。
            return failure_result(tool_name, result.error or "工具执行失败")
        return result


def default_permissions() -> frozenset[ToolPermission]:
    """Phase 0 默认授予的只读权限集合。"""
    return frozenset({ToolPermission.DEVICE_READ, ToolPermission.KNOWLEDGE_READ})


def supports_fault_type(tool: BaseTool, fault_type: SecurityFaultType) -> bool:
    """判断工具是否支持某个故障类型。"""
    return tool.supported_fault_types is None or fault_type in tool.supported_fault_types
