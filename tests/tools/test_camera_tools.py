"""Phase 1 只读摄像头工具验收。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.domain.evidence import EvidenceSource, EvidenceType
from security_diagnosis_harness.tools.contracts import ToolPermission, ToolRiskLevel
from security_diagnosis_harness.tools.device_channel import DeviceChannelTool
from security_diagnosis_harness.tools.device_stream import DeviceStreamTool
from security_diagnosis_harness.tools.platform_pull import PlatformPullStatusTool

from ..conftest import make_tool_context

DEVICE_ID = "cam-stream-failed-01"


@pytest.fixture
def gateway():
    from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
    from security_diagnosis_harness.bootstrap.container import CAMERA_CASES_DATA_PATH

    return StaticDeviceGateway(CAMERA_CASES_DATA_PATH)


@pytest.fixture
def context(gateway):
    return make_tool_context("diag_phase1", gateway=gateway)


def test_query_channel_produces_device_channel_draft(context, tool_registry):
    result = tool_registry.execute("device__query_channel", {"device_id": DEVICE_ID}, context)

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.DEVICE_CHANNEL
    assert draft.source is EvidenceSource.DEVICE_GATEWAY
    assert draft.payload["channel_status"] == "online"
    assert draft.payload["platform_registered"] is True


def test_query_stream_produces_device_stream_draft(context, tool_registry):
    result = tool_registry.execute(
        "device__query_stream",
        {"device_id": DEVICE_ID, "stream_kind": "main"},
        context,
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.DEVICE_STREAM
    assert draft.source is EvidenceSource.DEVICE_GATEWAY
    assert draft.payload["pull_status"] == "failed"
    assert draft.payload["error_code"] == "STREAM_PUBLISH_FAILED"


def test_query_stream_accepts_sub_stream(context, tool_registry):
    result = tool_registry.execute(
        "device__query_stream",
        {"device_id": DEVICE_ID, "stream_kind": "sub"},
        context,
    )

    assert result.ok is True
    assert result.evidence_drafts[0].payload["pull_status"] == "success"


def test_query_stream_rejects_unknown_stream_kind(context, tool_registry):
    result = tool_registry.execute(
        "device__query_stream",
        {"device_id": DEVICE_ID, "stream_kind": "third"},
        context,
    )

    assert result.ok is False
    assert result.evidence_drafts == []


def test_platform_pull_produces_platform_pull_draft(context, tool_registry):
    result = tool_registry.execute(
        "platform__query_pull_status", {"device_id": "cam-platform-pull-01"}, context
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.PLATFORM_PULL
    assert draft.source is EvidenceSource.DEVICE_GATEWAY
    assert draft.payload["pull_status"] == "failed"


def test_tools_are_rejected_without_device_read_permission(gateway, tool_registry):
    context = make_tool_context(
        "diag_phase1",
        gateway=gateway,
        permissions=frozenset({ToolPermission.KNOWLEDGE_READ}),
    )

    for tool_name, arguments in (
        ("device__query_channel", {"device_id": DEVICE_ID}),
        ("device__query_stream", {"device_id": DEVICE_ID}),
        ("platform__query_pull_status", {"device_id": DEVICE_ID}),
    ):
        result = tool_registry.execute(tool_name, arguments, context)
        assert result.ok is False
        assert "device:read" in (result.error or "")


def test_tool_failure_produces_no_evidence_draft(context, tool_registry):
    result = tool_registry.execute(
        "device__query_channel", {"device_id": "cam-not-exist"}, context
    )

    assert result.ok is False
    assert result.evidence_drafts == []
    assert result.error


def test_invalid_arguments_produce_no_evidence_draft(context, tool_registry):
    result = tool_registry.execute("device__query_channel", {"device_id": ""}, context)

    assert result.ok is False
    assert result.evidence_drafts == []


@pytest.mark.parametrize(
    "tool",
    [DeviceChannelTool(), DeviceStreamTool(), PlatformPullStatusTool()],
)
def test_new_tools_are_read_only(tool):
    assert tool.risk_level is ToolRiskLevel.READ_ONLY
    assert ToolPermission.DEVICE_READ in tool.required_permissions


def test_new_tools_are_registered_in_registry(tool_registry):
    for tool_name in (
        "device__query_channel",
        "device__query_stream",
        "platform__query_pull_status",
    ):
        assert tool_registry.has(tool_name)
