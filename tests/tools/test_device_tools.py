"""只读设备工具与知识检索工具验收。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceSource, EvidenceType
from security_diagnosis_harness.tools.contracts import ToolPermission, ToolRiskLevel
from security_diagnosis_harness.tools.device_alarm_events import DeviceAlarmEventsTool
from security_diagnosis_harness.tools.device_config import DeviceConfigSnapshotTool
from security_diagnosis_harness.tools.device_status import DeviceStatusTool
from security_diagnosis_harness.tools.knowledge_search import KnowledgeSearchTool

from ..conftest import DEVICE_ID, make_tool_context


@pytest.fixture
def context(static_gateway):
    return make_tool_context("diag_tools", gateway=static_gateway)


def test_device_status_tool_produces_device_status_draft(context, tool_registry):
    result = tool_registry.execute(
        "device__query_status", {"device_id": DEVICE_ID}, context
    )

    assert result.ok is True
    assert result.observation
    assert len(result.evidence_drafts) == 1
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.DEVICE_STATUS
    assert draft.source is EvidenceSource.DEVICE_GATEWAY
    assert draft.payload["online"] is True
    assert draft.payload["stream_status"] == "abnormal"


def test_device_alarm_events_tool_produces_device_alarm_draft(context, tool_registry):
    result = tool_registry.execute(
        "device__search_alarm_events",
        {"device_id": DEVICE_ID, "keyword": "stream", "limit": 5},
        context,
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.DEVICE_ALARM
    assert draft.source is EvidenceSource.DEVICE_GATEWAY
    assert [event["event_type"] for event in draft.payload["events"]] == [
        "STREAM_PUBLISH_FAILED"
    ]


def test_device_config_tool_produces_device_config_draft(context, tool_registry):
    result = tool_registry.execute(
        "device__read_config_snapshot", {"device_id": DEVICE_ID}, context
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.DEVICE_CONFIG
    assert draft.source is EvidenceSource.DEVICE_GATEWAY
    assert draft.redacted is True
    assert draft.payload["config"]["admin_password"] == "***REDACTED***"


def test_knowledge_search_tool_produces_knowledge_sop_draft(context, tool_registry):
    result = tool_registry.execute(
        "knowledge__search", {"query": "黑屏"}, context
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.KNOWLEDGE_SOP
    assert draft.source is EvidenceSource.KNOWLEDGE_BASE
    assert draft.payload["sops"][0]["sop_id"] == "sop-camera-black-screen-001"


def test_knowledge_search_only_matches_current_fault_type(context, tool_registry):
    access_context = make_tool_context(
        "diag_tools",
        gateway=context.device_gateway,
        fault_type=SecurityFaultType.ACCESS_CARD_FAILED,
    )

    result = tool_registry.execute("knowledge__search", {"query": "刷卡"}, access_context)

    assert result.ok is True
    assert result.evidence_drafts[0].payload["sops"][0]["sop_id"] == "sop-access-card-failed-001"


def test_knowledge_search_without_match_still_reports_observation(context, tool_registry):
    result = tool_registry.execute("knowledge__search", {"query": "不存在的故障"}, context)

    assert result.ok is True
    assert "未命中" in result.observation
    assert result.evidence_drafts[0].payload["sops"] == []


def test_device_tools_fail_controlled_when_device_missing(context, tool_registry):
    result = tool_registry.execute(
        "device__query_status", {"device_id": "camera-not-exist"}, context
    )

    assert result.ok is False
    assert result.evidence_drafts == []


@pytest.mark.parametrize(
    ("tool", "expected_permission"),
    [
        (DeviceStatusTool(), ToolPermission.DEVICE_READ),
        (DeviceAlarmEventsTool(), ToolPermission.DEVICE_READ),
        (DeviceConfigSnapshotTool(), ToolPermission.DEVICE_READ),
        (KnowledgeSearchTool(), ToolPermission.KNOWLEDGE_READ),
    ],
)
def test_all_phase0_tools_are_read_only(tool, expected_permission):
    assert tool.risk_level is ToolRiskLevel.READ_ONLY
    assert expected_permission in tool.required_permissions


def test_device_tools_require_device_read_permission(static_gateway, tool_registry):
    context = make_tool_context(
        "diag_tools",
        gateway=static_gateway,
        permissions=frozenset({ToolPermission.KNOWLEDGE_READ}),
    )

    result = tool_registry.execute(
        "device__query_status", {"device_id": DEVICE_ID}, context
    )

    assert result.ok is False
    assert "device:read" in (result.error or "")
