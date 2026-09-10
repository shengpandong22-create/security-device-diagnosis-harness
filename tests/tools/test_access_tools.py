"""Phase 3B 门禁只读工具验收。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.domain.evidence import EvidenceSource, EvidenceType
from security_diagnosis_harness.tools.access_controller import AccessControllerTool
from security_diagnosis_harness.tools.access_credential import AccessCredentialTool
from security_diagnosis_harness.tools.access_door import AccessDoorTool
from security_diagnosis_harness.tools.access_events import AccessEventsTool
from security_diagnosis_harness.tools.access_policy import AccessPolicyTool
from security_diagnosis_harness.tools.contracts import ToolPermission, ToolRiskLevel

from ..conftest import make_tool_context

DEVICE_ID = "access-credential-frozen-01"
DOOR_ID = "door-1"
CREDENTIAL_ID = "sample-card-frozen"
PERSON_ID = "sample-person-frozen"

ACCESS_TOOLS = (
    "access__query_controller",
    "access__query_door",
    "access__query_credential",
    "access__query_policy",
    "access__search_events",
)


@pytest.fixture
def context(access_gateway):
    return make_tool_context("diag_access", gateway=access_gateway)


def test_query_controller_returns_evidence_draft(context, access_tool_registry):
    result = access_tool_registry.execute(
        "access__query_controller",
        {"device_id": DEVICE_ID},
        context,
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.ACCESS_CONTROLLER
    assert draft.source is EvidenceSource.DEVICE_GATEWAY
    assert draft.payload["status"] == "online"
    assert "在线状态=online" in result.observation
    assert "健康状态=healthy" in result.observation


def test_query_door_returns_evidence_draft(context, access_tool_registry):
    result = access_tool_registry.execute(
        "access__query_door",
        {"device_id": DEVICE_ID, "door_id": DOOR_ID},
        context,
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.ACCESS_DOOR
    assert draft.payload["door_status"] == "closed"
    assert "门锁状态=locked" in result.observation


def test_query_credential_returns_evidence_draft(context, access_tool_registry):
    result = access_tool_registry.execute(
        "access__query_credential",
        {"credential_id": CREDENTIAL_ID},
        context,
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.ACCESS_CREDENTIAL
    assert draft.payload["status"] == "frozen"
    assert draft.payload["credential_id"] == REDACTED_VALUE
    assert "凭证状态=frozen" in result.observation


def test_query_policy_returns_evidence_draft(context, access_tool_registry):
    result = access_tool_registry.execute(
        "access__query_policy",
        {"person_id": PERSON_ID, "door_id": DOOR_ID},
        context,
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.ACCESS_POLICY
    assert draft.payload["allowed"] is True
    assert draft.payload["person_id"] == REDACTED_VALUE
    assert "授权允许=True" in result.observation


def test_search_events_returns_evidence_draft(context, access_tool_registry):
    result = access_tool_registry.execute(
        "access__search_events",
        {
            "device_id": DEVICE_ID,
            "door_id": DOOR_ID,
            "credential_id": CREDENTIAL_ID,
        },
        context,
    )

    assert result.ok is True
    draft = result.evidence_drafts[0]
    assert draft.evidence_type is EvidenceType.ACCESS_EVENT
    assert draft.payload["events"][0]["deny_reason"] == "frozen_credential"
    assert draft.payload["events"][0]["credential_id"] == REDACTED_VALUE
    assert "decision=denied" in result.observation


def test_search_events_without_matches_returns_no_evidence(context, access_tool_registry):
    result = access_tool_registry.execute(
        "access__search_events",
        {
            "device_id": DEVICE_ID,
            "door_id": DOOR_ID,
            "credential_id": "sample-card-not-exist",
        },
        context,
    )

    assert result.ok is True
    assert result.evidence_drafts == []
    assert "未命中" in result.observation


@pytest.mark.parametrize(
    ("tool", "expected_name"),
    [
        (AccessControllerTool(), "access__query_controller"),
        (AccessDoorTool(), "access__query_door"),
        (AccessCredentialTool(), "access__query_credential"),
        (AccessPolicyTool(), "access__query_policy"),
        (AccessEventsTool(), "access__search_events"),
    ],
)
def test_tools_are_read_only_and_require_device_read(tool, expected_name):
    assert tool.name == expected_name
    assert tool.risk_level is ToolRiskLevel.READ_ONLY
    assert ToolPermission.DEVICE_READ in tool.required_permissions


def test_access_tools_are_registered(access_tool_registry):
    for tool_name in ACCESS_TOOLS:
        assert access_tool_registry.has(tool_name)


def test_tools_are_rejected_without_device_read(access_gateway, access_tool_registry):
    context = make_tool_context(
        "diag_access",
        gateway=access_gateway,
        permissions=frozenset({ToolPermission.KNOWLEDGE_READ}),
    )

    for tool_name, arguments in (
        ("access__query_controller", {"device_id": DEVICE_ID}),
        ("access__query_door", {"device_id": DEVICE_ID, "door_id": DOOR_ID}),
        ("access__query_credential", {"credential_id": CREDENTIAL_ID}),
        ("access__query_policy", {"person_id": PERSON_ID, "door_id": DOOR_ID}),
        (
            "access__search_events",
            {"device_id": DEVICE_ID, "door_id": DOOR_ID, "credential_id": CREDENTIAL_ID},
        ),
    ):
        result = access_tool_registry.execute(tool_name, arguments, context)

        assert result.ok is False
        assert "device:read" in (result.error or "")
        assert result.evidence_drafts == []


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("access__query_controller", {"device_id": ""}),
        ("access__query_door", {"device_id": DEVICE_ID}),
        ("access__query_door", {"device_id": DEVICE_ID, "door_id": ""}),
        ("access__query_credential", {"credential_id": ""}),
        ("access__query_policy", {"person_id": "", "door_id": DOOR_ID}),
        ("access__query_policy", {"person_id": PERSON_ID}),
        (
            "access__search_events",
            {
                "device_id": DEVICE_ID,
                "door_id": DOOR_ID,
                "credential_id": CREDENTIAL_ID,
                "limit": 0,
            },
        ),
        (
            "access__search_events",
            {
                "device_id": DEVICE_ID,
                "door_id": DOOR_ID,
                "credential_id": CREDENTIAL_ID,
                "limit": 51,
            },
        ),
    ],
)
def test_invalid_arguments_are_rejected(context, access_tool_registry, tool_name, arguments):
    result = access_tool_registry.execute(tool_name, arguments, context)

    assert result.ok is False
    assert result.evidence_drafts == []


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("access__query_controller", {"device_id": "access-not-exist"}),
        ("access__query_door", {"device_id": DEVICE_ID, "door_id": "door-not-exist"}),
        ("access__query_credential", {"credential_id": "sample-card-not-exist"}),
        ("access__query_policy", {"person_id": "sample-person-not-exist", "door_id": DOOR_ID}),
        (
            "access__search_events",
            {"device_id": "access-not-exist", "door_id": DOOR_ID, "credential_id": CREDENTIAL_ID},
        ),
    ],
)
def test_gateway_errors_become_controlled_failures(
    context, access_tool_registry, tool_name, arguments
):
    result = access_tool_registry.execute(tool_name, arguments, context)

    assert result.ok is False
    assert result.error
    assert result.evidence_drafts == []


def test_missing_gateway_becomes_controlled_failure(access_tool_registry):
    context = make_tool_context("diag_access", gateway=None)

    result = access_tool_registry.execute(
        "access__query_controller",
        {"device_id": DEVICE_ID},
        context,
    )

    assert result.ok is False
    assert "未配置 DeviceGateway" in (result.error or "")
    assert result.evidence_drafts == []


def test_access_payloads_do_not_leak_sensitive_identifiers(context, access_tool_registry):
    for tool_name, arguments in (
        ("access__query_credential", {"credential_id": CREDENTIAL_ID}),
        ("access__query_policy", {"person_id": PERSON_ID, "door_id": DOOR_ID}),
        (
            "access__search_events",
            {"device_id": DEVICE_ID, "door_id": DOOR_ID, "credential_id": CREDENTIAL_ID},
        ),
    ):
        result = access_tool_registry.execute(tool_name, arguments, context)

        assert result.ok is True
        payload_text = str(result.evidence_drafts[0].payload)
        assert CREDENTIAL_ID not in payload_text
        assert PERSON_ID not in payload_text
        assert "sample-card-no-not-real" not in payload_text
