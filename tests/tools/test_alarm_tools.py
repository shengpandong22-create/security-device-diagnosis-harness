"""Phase 4B 报警误报只读工具验收。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceType
from security_diagnosis_harness.tools.contracts import ToolPermission, ToolRiskLevel
from security_diagnosis_harness.tools.registry import default_permissions

from ..conftest import make_tool_context

DEVICE_ID = "alarm-rule-sensitive-01"
ALARM_ID = "alarm-1"
RULE_ID = "rule-1"

ALARM_TOOLS = (
    "alarm__query_rule",
    "alarm__query_signal",
    "alarm__query_environment",
    "alarm__query_verification",
    "alarm__query_correlation",
)


def _context(alarm_gateway):
    return make_tool_context(
        "diag-alarm",
        gateway=alarm_gateway,
        fault_type=SecurityFaultType.ALARM_FALSE_POSITIVE,
    )


def test_alarm_rule_tool_returns_evidence_draft(alarm_gateway, alarm_tool_registry):
    result = alarm_tool_registry.execute(
        "alarm__query_rule",
        {"device_id": DEVICE_ID, "rule_id": RULE_ID},
        _context(alarm_gateway),
    )

    assert result.ok is True
    assert result.evidence_drafts[0].evidence_type is EvidenceType.ALARM_RULE
    assert "疑似过敏=True" in result.observation


def test_alarm_signal_tool_returns_evidence_draft(alarm_gateway, alarm_tool_registry):
    result = alarm_tool_registry.execute(
        "alarm__query_signal",
        {"device_id": "alarm-sensor-noise-01", "alarm_id": ALARM_ID},
        _context(alarm_gateway),
    )

    assert result.ok is True
    assert result.evidence_drafts[0].evidence_type is EvidenceType.ALARM_SIGNAL
    assert "噪声异常=True" in result.observation


def test_alarm_environment_tool_returns_evidence_draft(alarm_gateway, alarm_tool_registry):
    result = alarm_tool_registry.execute(
        "alarm__query_environment",
        {"device_id": "alarm-environment-rain-01", "alarm_id": ALARM_ID},
        _context(alarm_gateway),
    )

    assert result.ok is True
    assert result.evidence_drafts[0].evidence_type is EvidenceType.ALARM_ENVIRONMENT
    assert "存在干扰=True" in result.observation


def test_alarm_verification_tool_returns_evidence_draft(alarm_gateway, alarm_tool_registry):
    result = alarm_tool_registry.execute(
        "alarm__query_verification",
        {"device_id": "alarm-verification-negative-01", "alarm_id": ALARM_ID},
        _context(alarm_gateway),
    )

    assert result.ok is True
    assert result.evidence_drafts[0].evidence_type is EvidenceType.ALARM_VERIFICATION
    assert "疑似误报=True" in result.observation


def test_alarm_correlation_tool_returns_evidence_draft(alarm_gateway, alarm_tool_registry):
    result = alarm_tool_registry.execute(
        "alarm__query_correlation",
        {"device_id": "alarm-duplicate-burst-01", "alarm_id": ALARM_ID},
        _context(alarm_gateway),
    )

    assert result.ok is True
    assert result.evidence_drafts[0].evidence_type is EvidenceType.ALARM_CORRELATION
    assert "告警风暴=True" in result.observation


@pytest.mark.parametrize("tool_name", ALARM_TOOLS)
def test_alarm_tools_are_read_only_and_require_device_read(alarm_tool_registry, tool_name):
    tool = alarm_tool_registry.get(tool_name)

    assert tool.risk_level is ToolRiskLevel.READ_ONLY
    assert tool.required_permissions == frozenset({ToolPermission.DEVICE_READ})


@pytest.mark.parametrize("tool_name", ALARM_TOOLS)
def test_alarm_tools_are_registered(alarm_tool_registry, tool_name):
    assert alarm_tool_registry.has(tool_name)


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("alarm__query_rule", {"device_id": "", "rule_id": RULE_ID}),
        ("alarm__query_rule", {"device_id": DEVICE_ID, "rule_id": ""}),
        ("alarm__query_signal", {"device_id": DEVICE_ID, "alarm_id": ""}),
        ("alarm__query_environment", {"device_id": "", "alarm_id": ALARM_ID}),
        ("alarm__query_verification", {"device_id": "", "alarm_id": ALARM_ID}),
        ("alarm__query_correlation", {"device_id": DEVICE_ID, "alarm_id": ""}),
    ],
)
def test_alarm_tools_reject_invalid_arguments(
    alarm_gateway, alarm_tool_registry, tool_name, args
):
    result = alarm_tool_registry.execute(tool_name, args, _context(alarm_gateway))

    assert result.ok is False
    assert result.evidence_drafts == []


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("alarm__query_rule", {"device_id": DEVICE_ID, "rule_id": RULE_ID}),
        ("alarm__query_signal", {"device_id": DEVICE_ID, "alarm_id": ALARM_ID}),
        ("alarm__query_environment", {"device_id": DEVICE_ID, "alarm_id": ALARM_ID}),
        ("alarm__query_verification", {"device_id": DEVICE_ID, "alarm_id": ALARM_ID}),
        ("alarm__query_correlation", {"device_id": DEVICE_ID, "alarm_id": ALARM_ID}),
    ],
)
def test_alarm_tools_are_rejected_without_device_read(
    alarm_gateway, alarm_tool_registry, tool_name, args
):
    context = make_tool_context(
        "diag-alarm",
        gateway=alarm_gateway,
        fault_type=SecurityFaultType.ALARM_FALSE_POSITIVE,
        permissions=frozenset(default_permissions() - {ToolPermission.DEVICE_READ}),
    )

    result = alarm_tool_registry.execute(tool_name, args, context)

    assert result.ok is False
    assert result.evidence_drafts == []


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("alarm__query_rule", {"device_id": "missing", "rule_id": RULE_ID}),
        ("alarm__query_signal", {"device_id": "missing", "alarm_id": ALARM_ID}),
        ("alarm__query_environment", {"device_id": "missing", "alarm_id": ALARM_ID}),
        ("alarm__query_verification", {"device_id": "missing", "alarm_id": ALARM_ID}),
        ("alarm__query_correlation", {"device_id": "missing", "alarm_id": ALARM_ID}),
    ],
)
def test_alarm_gateway_errors_become_controlled_failures(
    alarm_gateway, alarm_tool_registry, tool_name, args
):
    result = alarm_tool_registry.execute(tool_name, args, _context(alarm_gateway))

    assert result.ok is False
    assert result.evidence_drafts == []


def test_alarm_verification_payload_has_no_plaintext_snapshot_url(
    alarm_gateway, alarm_tool_registry
):
    result = alarm_tool_registry.execute(
        "alarm__query_verification",
        {"device_id": "alarm-verification-negative-01", "alarm_id": ALARM_ID},
        _context(alarm_gateway),
    )

    payload = result.evidence_drafts[0].payload

    assert "http://example.local/snapshot/not-real.jpg" not in str(payload)
    assert "***REDACTED***" in str(payload)
