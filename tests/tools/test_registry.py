"""Tool Registry 验收。"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.tools.contracts import (
    BaseTool,
    ToolBypassError,
    ToolEvidenceDraft,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolPermission,
    ToolRiskLevel,
)
from security_diagnosis_harness.tools.device_status import DeviceStatusTool
from security_diagnosis_harness.tools.registry import (
    ToolAlreadyRegisteredError,
    ToolNotFoundError,
    ToolRegistry,
)

from ..conftest import make_tool_context


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DummyTool(BaseTool):
    name = "dummy__read"
    description = "测试用只读工具"
    input_model = EmptyInput
    required_permissions = frozenset({ToolPermission.DEVICE_READ})

    def _execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=self.name, observation="ok")


class CameraOnlyTool(DummyTool):
    name = "dummy__camera_only"
    supported_fault_types = frozenset({SecurityFaultType.CAMERA_BLACK_SCREEN})


class FaultyTool(DummyTool):
    name = "dummy__faulty"

    def _execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolExecutionResult:
        raise RuntimeError("设备网关连接失败")


class KnowledgeTool(DummyTool):
    name = "dummy__knowledge"
    required_permissions = frozenset({ToolPermission.KNOWLEDGE_READ})


class FailingTool(DummyTool):
    """返回失败结果的工具，用于验证不会被伪造成证据。"""

    name = "dummy__failing"
    evidence_draft: ClassVar[ToolEvidenceDraft | None] = None

    def _execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=self.name,
            ok=False,
            error="查询失败",
            evidence_drafts=[self.evidence_draft] if self.evidence_draft else [],
        )


def test_register_tool_success():
    registry = ToolRegistry()
    tool = DummyTool()

    registry.register(tool)

    assert registry.has("dummy__read")
    assert registry.get("dummy__read") is tool
    assert registry.names() == ["dummy__read"]


def test_duplicate_registration_is_rejected():
    registry = ToolRegistry()
    registry.register(DummyTool())

    with pytest.raises(ToolAlreadyRegisteredError):
        registry.register(DummyTool())


def test_registering_mutating_tool_is_rejected():
    class MutatingTool(DummyTool):
        name = "dummy__write"
        risk_level = ToolRiskLevel.MUTATING

    registry = ToolRegistry()

    with pytest.raises(ValueError, match="READ_ONLY"):
        registry.register(MutatingTool())


def test_unknown_tool_is_rejected():
    registry = ToolRegistry()
    context = make_tool_context("diag_a")

    result = registry.execute("device__not_registered", {}, context)

    assert result.ok is False
    assert "未注册" in (result.error or "")
    assert result.evidence_drafts == []


def test_unknown_tool_raises_when_fetched_by_name():
    registry = ToolRegistry()

    with pytest.raises(ToolNotFoundError):
        registry.get("device__not_registered")


def test_missing_permission_is_rejected():
    registry = ToolRegistry()
    registry.register(KnowledgeTool())
    context = make_tool_context(
        "diag_a",
        permissions=frozenset({ToolPermission.DEVICE_READ}),
    )

    result = registry.execute("dummy__knowledge", {}, context)

    assert result.ok is False
    assert "knowledge:read" in (result.error or "")


def test_unsupported_fault_type_is_rejected():
    registry = ToolRegistry()
    registry.register(CameraOnlyTool())
    context = make_tool_context(
        "diag_a",
        fault_type=SecurityFaultType.ACCESS_CARD_FAILED,
    )

    result = registry.execute("dummy__camera_only", {}, context)

    assert result.ok is False
    assert "不被工具" in (result.error or "")


def test_supported_fault_type_is_executed():
    registry = ToolRegistry()
    registry.register(CameraOnlyTool())
    context = make_tool_context("diag_a", fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN)

    result = registry.execute("dummy__camera_only", {}, context)

    assert result.ok is True


def test_invalid_arguments_are_rejected():
    registry = ToolRegistry()
    registry.register(DeviceStatusTool())
    context = make_tool_context("diag_a")

    result = registry.execute("device__query_status", {"device_id": ""}, context)

    assert result.ok is False
    assert "参数非法" in (result.error or "")


def test_non_mapping_arguments_are_rejected():
    registry = ToolRegistry()
    registry.register(DeviceStatusTool())
    context = make_tool_context("diag_a")

    result = registry.execute("device__query_status", ["camera-3f-001"], context)

    assert result.ok is False
    assert "键值映射" in (result.error or "")


def test_tool_exception_becomes_controlled_failure():
    registry = ToolRegistry()
    registry.register(FaultyTool())
    context = make_tool_context("diag_a")

    result = registry.execute("dummy__faulty", {}, context)

    assert result.ok is False
    assert "设备网关连接失败" in (result.error or "")
    assert result.evidence_drafts == []


def test_failed_result_never_carries_evidence():
    draft = ToolEvidenceDraft(
        evidence_type="device_status",
        source="device_gateway",
        summary="伪装的证据",
    )
    FailingTool.evidence_draft = draft
    registry = ToolRegistry()
    registry.register(FailingTool())
    context = make_tool_context("diag_a")

    result = registry.execute("dummy__failing", {}, context)

    assert result.ok is False
    assert result.evidence_drafts == []


def test_tool_cannot_be_invoked_without_registry():
    context = make_tool_context("diag_a")

    with pytest.raises(ToolBypassError):
        DummyTool().run({}, context)


def test_registry_sets_registry_invocation_flag():
    registry = ToolRegistry()
    registry.register(DummyTool())
    context = make_tool_context("diag_a")

    registry.execute("dummy__read", {}, context)

    assert context.invoked_by_registry is False
