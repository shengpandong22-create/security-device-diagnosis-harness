"""Phase 2B 录像只读工具验收。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.domain.evidence import EvidenceSource, EvidenceType
from security_diagnosis_harness.tools.contracts import ToolPermission, ToolRiskLevel
from security_diagnosis_harness.tools.recording_plan import RecordingPlanTool
from security_diagnosis_harness.tools.recording_playback import RecordingPlaybackTool
from security_diagnosis_harness.tools.storage_status import StorageStatusTool

from ..conftest import make_tool_context

DEVICE_ID = "cam-rec-plan-disabled-01"
CHANNEL_ID = "1"
START_AT = "2026-09-08T12:00:00+00:00"
END_AT = "2026-09-08T13:00:00+00:00"

RECORDING_TOOLS = (
    "recording__query_plan",
    "storage__query_status",
    "recording__check_playback",
)


@pytest.fixture
def context(recording_gateway):
    return make_tool_context("diag_rec", gateway=recording_gateway)


# ------------------------------------------------------------------ 成功路径
def test_query_plan_returns_evidence_draft(context, recording_tool_registry):
    result = recording_tool_registry.execute(
        "recording__query_plan",
        {"device_id": DEVICE_ID, "channel_id": CHANNEL_ID},
        context,
    )

    assert result.ok is True
    assert result.evidence_drafts
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.RECORDING_PLAN
    assert draft.source is EvidenceSource.DEVICE_GATEWAY
    assert draft.payload["status"] == "disabled"
    assert draft.payload["mode"] == "manual"
    # observation 要说明状态、模式、是否有时间段
    assert "状态=disabled" in result.observation
    assert "模式=manual" in result.observation
    assert "时间段数=" in result.observation


def test_storage_status_returns_evidence_draft(context, recording_tool_registry):
    result = recording_tool_registry.execute(
        "storage__query_status",
        {"device_id": DEVICE_ID, "channel_id": CHANNEL_ID},
        context,
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.STORAGE_STATUS
    assert draft.payload["status"] == "normal"
    # observation 要说明存储状态、剩余容量、是否容量不足
    assert "状态=normal" in result.observation
    assert "剩余=" in result.observation
    assert "容量不足=" in result.observation


def test_storage_status_reports_capacity_low_for_full_case(context, recording_tool_registry):
    result = recording_tool_registry.execute(
        "storage__query_status",
        {"device_id": "cam-rec-storage-full-01", "channel_id": CHANNEL_ID},
        context,
    )

    assert result.ok is True
    assert result.evidence_drafts[0].payload["status"] == "full"
    assert "容量不足=True" in result.observation


def test_check_playback_returns_evidence_draft(context, recording_tool_registry):
    result = recording_tool_registry.execute(
        "recording__check_playback",
        {
            "device_id": DEVICE_ID,
            "channel_id": CHANNEL_ID,
            "start_at": START_AT,
            "end_at": END_AT,
        },
        context,
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.PLAYBACK_CHECK
    assert draft.payload["status"] == "missing"
    # observation 要说明回放状态、可回放性、文件数、失败原因
    assert "回放状态=missing" in result.observation
    assert "可回放=False" in result.observation
    assert "文件数=0" in result.observation
    assert "失败原因=" in result.observation


def test_evidence_draft_payload_has_no_plaintext_credentials(context, recording_tool_registry):
    """三类 EvidenceDraft 的 payload 都不能出现未脱敏凭证。"""
    for tool_name, arguments in (
        ("recording__query_plan", {"device_id": DEVICE_ID, "channel_id": CHANNEL_ID}),
        ("storage__query_status", {"device_id": DEVICE_ID, "channel_id": CHANNEL_ID}),
        (
            "recording__check_playback",
            {
                "device_id": DEVICE_ID,
                "channel_id": CHANNEL_ID,
                "start_at": START_AT,
                "end_at": END_AT,
            },
        ),
    ):
        result = recording_tool_registry.execute(tool_name, arguments, context)

        assert result.ok is True
        assert "sample-admin-pwd-not-real" not in str(result.evidence_drafts[0].payload)


# ------------------------------------------------------------------ 元信息
@pytest.mark.parametrize(
    ("tool", "expected_name"),
    [
        (RecordingPlanTool(), "recording__query_plan"),
        (StorageStatusTool(), "storage__query_status"),
        (RecordingPlaybackTool(), "recording__check_playback"),
    ],
)
def test_tools_are_read_only_and_require_device_read(tool, expected_name):
    assert tool.name == expected_name
    assert tool.risk_level is ToolRiskLevel.READ_ONLY
    assert ToolPermission.DEVICE_READ in tool.required_permissions


def test_recording_tools_are_registered(recording_tool_registry):
    for tool_name in RECORDING_TOOLS:
        assert recording_tool_registry.has(tool_name)


# ------------------------------------------------------------------ 权限
def test_tools_are_rejected_without_device_read(recording_gateway, recording_tool_registry):
    context = make_tool_context(
        "diag_rec",
        gateway=recording_gateway,
        permissions=frozenset({ToolPermission.KNOWLEDGE_READ}),
    )

    for tool_name, arguments in (
        ("recording__query_plan", {"device_id": DEVICE_ID, "channel_id": CHANNEL_ID}),
        ("storage__query_status", {"device_id": DEVICE_ID, "channel_id": CHANNEL_ID}),
        (
            "recording__check_playback",
            {
                "device_id": DEVICE_ID,
                "channel_id": CHANNEL_ID,
                "start_at": START_AT,
                "end_at": END_AT,
            },
        ),
    ):
        result = recording_tool_registry.execute(tool_name, arguments, context)

        assert result.ok is False
        assert "device:read" in (result.error or "")
        assert result.evidence_drafts == []


# ------------------------------------------------------------------ 参数
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("recording__query_plan", {"device_id": "", "channel_id": CHANNEL_ID}),
        ("recording__query_plan", {"device_id": DEVICE_ID, "channel_id": ""}),
        ("recording__query_plan", {"device_id": DEVICE_ID}),
        ("recording__query_plan", {"channel_id": CHANNEL_ID}),
        ("storage__query_status", {"device_id": "", "channel_id": CHANNEL_ID}),
        ("storage__query_status", {"device_id": DEVICE_ID}),
    ],
)
def test_invalid_arguments_are_rejected(context, recording_tool_registry, tool_name, arguments):
    result = recording_tool_registry.execute(tool_name, arguments, context)

    assert result.ok is False
    assert result.evidence_drafts == []


def test_playback_rejects_missing_time_arguments(context, recording_tool_registry):
    result = recording_tool_registry.execute(
        "recording__check_playback",
        {"device_id": DEVICE_ID, "channel_id": CHANNEL_ID},
        context,
    )

    assert result.ok is False
    assert result.evidence_drafts == []


def test_playback_rejects_end_at_not_after_start_at(context, recording_tool_registry):
    result = recording_tool_registry.execute(
        "recording__check_playback",
        {
            "device_id": DEVICE_ID,
            "channel_id": CHANNEL_ID,
            "start_at": END_AT,
            "end_at": START_AT,
        },
        context,
    )

    assert result.ok is False
    assert "end_at 必须大于 start_at" in (result.error or "")
    assert result.evidence_drafts == []


def test_playback_rejects_equal_start_and_end(context, recording_tool_registry):
    result = recording_tool_registry.execute(
        "recording__check_playback",
        {
            "device_id": DEVICE_ID,
            "channel_id": CHANNEL_ID,
            "start_at": START_AT,
            "end_at": START_AT,
        },
        context,
    )

    assert result.ok is False
    assert result.evidence_drafts == []


def test_playback_rejects_unparsable_datetime(context, recording_tool_registry):
    result = recording_tool_registry.execute(
        "recording__check_playback",
        {
            "device_id": DEVICE_ID,
            "channel_id": CHANNEL_ID,
            "start_at": "not-a-datetime",
            "end_at": END_AT,
        },
        context,
    )

    assert result.ok is False
    assert result.evidence_drafts == []


def test_playback_rejects_too_large_window(context, recording_tool_registry):
    result = recording_tool_registry.execute(
        "recording__check_playback",
        {
            "device_id": DEVICE_ID,
            "channel_id": CHANNEL_ID,
            "start_at": "2020-01-01T00:00:00+00:00",
            "end_at": "2026-01-01T00:00:00+00:00",
        },
        context,
    )

    assert result.ok is False
    assert "时间窗" in (result.error or "")
    assert result.evidence_drafts == []


# ------------------------------------------------------------------ 时间解析
@pytest.mark.parametrize(
    ("start_at", "end_at"),
    [
        ("2026-09-08T12:00:00+00:00", "2026-09-08T13:00:00+00:00"),
        ("2026-09-08T12:00:00Z", "2026-09-08T13:00:00Z"),
        ("2026-09-08T12:00:00", "2026-09-08T13:00:00"),
        ("2026-09-08T20:00:00+08:00", "2026-09-08T21:00:00+08:00"),
    ],
)
def test_playback_parses_iso_datetime_formats(
    context, recording_tool_registry, start_at, end_at
):
    result = recording_tool_registry.execute(
        "recording__check_playback",
        {
            "device_id": DEVICE_ID,
            "channel_id": CHANNEL_ID,
            "start_at": start_at,
            "end_at": end_at,
        },
        context,
    )

    assert result.ok is True, result.error
    assert result.evidence_drafts[0].evidence_type is EvidenceType.PLAYBACK_CHECK


def test_playback_accepts_datetime_objects(context, recording_tool_registry):
    result = recording_tool_registry.execute(
        "recording__check_playback",
        {
            "device_id": DEVICE_ID,
            "channel_id": CHANNEL_ID,
            "start_at": datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
            "end_at": datetime(2026, 9, 8, 13, 0, tzinfo=UTC),
        },
        context,
    )

    assert result.ok is True


# ------------------------------------------------------------------ 网关异常
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("recording__query_plan", {"device_id": "cam-not-exist", "channel_id": CHANNEL_ID}),
        ("storage__query_status", {"device_id": "cam-not-exist", "channel_id": CHANNEL_ID}),
        (
            "recording__check_playback",
            {
                "device_id": "cam-not-exist",
                "channel_id": CHANNEL_ID,
                "start_at": START_AT,
                "end_at": END_AT,
            },
        ),
    ],
)
def test_gateway_errors_become_controlled_failures(
    context, recording_tool_registry, tool_name, arguments
):
    result = recording_tool_registry.execute(tool_name, arguments, context)

    assert result.ok is False
    assert result.error
    assert result.evidence_drafts == []


def test_missing_gateway_becomes_controlled_failure(recording_tool_registry):
    context = make_tool_context("diag_rec", gateway=None)

    for tool_name, arguments in (
        ("recording__query_plan", {"device_id": DEVICE_ID, "channel_id": CHANNEL_ID}),
        ("storage__query_status", {"device_id": DEVICE_ID, "channel_id": CHANNEL_ID}),
    ):
        result = recording_tool_registry.execute(tool_name, arguments, context)

        assert result.ok is False
        assert "未配置 DeviceGateway" in (result.error or "")
        assert result.evidence_drafts == []


def test_camera_only_gateway_has_no_recording_facts(recording_tool_registry):
    """用不含录像事实的网关时，工具必须受控失败而不是伪造证据。"""
    from security_diagnosis_harness.bootstrap.container import CAMERA_CASES_DATA_PATH

    from ..conftest import StaticDeviceGateway

    context = make_tool_context(
        "diag_rec", gateway=StaticDeviceGateway(CAMERA_CASES_DATA_PATH)
    )

    for tool_name, arguments in (
        ("recording__query_plan", {"device_id": "cam-stream-failed-01", "channel_id": "1"}),
        ("storage__query_status", {"device_id": "cam-stream-failed-01", "channel_id": "1"}),
        (
            "recording__check_playback",
            {
                "device_id": "cam-stream-failed-01",
                "channel_id": "1",
                "start_at": START_AT,
                "end_at": END_AT,
            },
        ),
    ):
        result = recording_tool_registry.execute(tool_name, arguments, context)

        assert result.ok is False
        assert result.evidence_drafts == []


def test_redaction_marker_used_when_credentials_present(context, recording_tool_registry):
    """配置类工具确认脱敏占位符存在（与设备配置工具口径一致）。"""
    result = recording_tool_registry.execute(
        "device__read_config_snapshot", {"device_id": DEVICE_ID}, context
    )

    assert result.ok is True
    assert REDACTED_VALUE in str(result.evidence_drafts[0].payload)
