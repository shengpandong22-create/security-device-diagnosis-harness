"""StaticDeviceGateway 对 Phase 3B 门禁刷卡异常样例数据的支持。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.domain.access import (
    AccessControllerHealth,
    AccessControllerSnapshot,
    AccessControllerStatus,
    AccessDecision,
    AccessDenyReason,
    AccessEvent,
    AccessPolicySnapshot,
    CredentialSnapshot,
    CredentialStatus,
    DoorLockStatus,
    DoorSnapshot,
)
from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.ports.device_gateway import (
    DeviceGateway,
    DeviceGatewayDataError,
    DeviceNotFoundError,
)

from ..conftest import ACCESS_CASES_DATA_PATH

EXPECTED_CASE_IDS = [
    "credential_frozen",
    "permission_denied",
    "time_window_denied",
    "controller_offline",
    "door_lock_jammed",
]

EXPECTED_DEVICES = [
    "access-credential-frozen-01",
    "access-permission-denied-01",
    "access-time-window-denied-01",
    "access-controller-offline-01",
    "access-door-lock-jammed-01",
]

DOOR_ID = "door-1"


@pytest.fixture
def gateway() -> StaticDeviceGateway:
    return StaticDeviceGateway(ACCESS_CASES_DATA_PATH)


def test_access_cases_file_is_loadable():
    gateway = StaticDeviceGateway(ACCESS_CASES_DATA_PATH)

    assert [case.case_id for case in gateway.list_cases()] == EXPECTED_CASE_IDS


def test_all_five_access_cases_are_listed(gateway):
    cases = gateway.list_cases()

    assert len(cases) == 5
    assert {case.device_id for case in cases} == set(EXPECTED_DEVICES)
    for case in cases:
        assert case.expected_label


def test_gateway_satisfies_device_gateway_protocol(gateway):
    assert isinstance(gateway, DeviceGateway)


def test_query_access_controller_returns_snapshot(gateway):
    controller = gateway.query_access_controller("access-credential-frozen-01")

    assert isinstance(controller, AccessControllerSnapshot)
    assert controller.controller_id == "ctrl-frozen-01"
    assert controller.status is AccessControllerStatus.ONLINE
    assert controller.health is AccessControllerHealth.HEALTHY


def test_query_access_controller_for_offline_case(gateway):
    controller = gateway.query_access_controller("access-controller-offline-01")

    assert controller.status is AccessControllerStatus.OFFLINE
    assert controller.health is AccessControllerHealth.ERROR
    assert controller.last_error == "controller heartbeat lost"


def test_query_access_controller_unknown_device_fails(gateway):
    with pytest.raises(DeviceNotFoundError):
        gateway.query_access_controller("access-not-exist")


def test_query_access_controller_without_access_data_fails(device_data_file):
    gateway = StaticDeviceGateway(device_data_file)

    with pytest.raises(DeviceGatewayDataError):
        gateway.query_access_controller("camera-3f-001")


def test_query_door_returns_snapshot(gateway):
    door = gateway.query_door("access-credential-frozen-01", DOOR_ID)

    assert isinstance(door, DoorSnapshot)
    assert door.door_id == DOOR_ID
    assert door.lock_status is DoorLockStatus.LOCKED
    assert door.has_lock_error is False


def test_query_door_for_lock_jammed_case(gateway):
    door = gateway.query_door("access-door-lock-jammed-01", DOOR_ID)

    assert door.lock_status is DoorLockStatus.JAMMED
    assert door.has_lock_error is True
    assert door.last_error == "lock motor blocked"


def test_query_door_unknown_door_fails(gateway):
    with pytest.raises(DeviceGatewayDataError):
        gateway.query_door("access-credential-frozen-01", "door-99")


def test_query_credential_returns_snapshot(gateway):
    credential = gateway.query_credential("sample-card-frozen")

    assert isinstance(credential, CredentialSnapshot)
    assert credential.status is CredentialStatus.FROZEN
    assert credential.credential_id == REDACTED_VALUE
    assert credential.redacted is True
    assert "sample-card-no-not-real" not in str(credential.model_dump(mode="json"))


def test_query_credential_unknown_fails(gateway):
    with pytest.raises(DeviceGatewayDataError):
        gateway.query_credential("sample-card-not-exist")


def test_query_access_policy_returns_snapshot(gateway):
    policy = gateway.query_access_policy("sample-person-frozen", DOOR_ID)

    assert isinstance(policy, AccessPolicySnapshot)
    assert policy.allowed is True
    assert policy.person_id == REDACTED_VALUE
    assert policy.credential_id == REDACTED_VALUE
    assert policy.redacted is True


def test_query_access_policy_for_denied_case(gateway):
    policy = gateway.query_access_policy("sample-person-no-permission", DOOR_ID)

    assert policy.allowed is False


def test_query_access_policy_unknown_fails(gateway):
    with pytest.raises(DeviceGatewayDataError):
        gateway.query_access_policy("sample-person-not-exist", DOOR_ID)


def test_search_access_events_returns_events(gateway):
    events = gateway.search_access_events(
        "access-credential-frozen-01",
        DOOR_ID,
        "sample-card-frozen",
    )

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, AccessEvent)
    assert event.decision is AccessDecision.DENIED
    assert event.deny_reason is AccessDenyReason.FROZEN_CREDENTIAL
    assert event.credential_id == REDACTED_VALUE
    assert event.person_id == REDACTED_VALUE


def test_search_access_events_for_controller_timeout_case(gateway):
    events = gateway.search_access_events(
        "access-controller-offline-01",
        DOOR_ID,
        "sample-card-controller-offline",
    )

    assert events[0].decision is AccessDecision.TIMEOUT
    assert events[0].deny_reason is AccessDenyReason.CONTROLLER_TIMEOUT


def test_search_access_events_respects_limit(gateway):
    events = gateway.search_access_events(
        "access-credential-frozen-01",
        DOOR_ID,
        "sample-card-frozen",
        limit=1,
    )

    assert len(events) == 1


def test_search_access_events_unknown_credential_returns_empty(gateway):
    events = gateway.search_access_events(
        "access-credential-frozen-01",
        DOOR_ID,
        "sample-card-not-exist",
    )

    assert events == []


def test_access_facts_do_not_leak_credentials(gateway):
    snapshots = [
        gateway.query_access_controller("access-credential-frozen-01"),
        gateway.query_door("access-door-lock-jammed-01", DOOR_ID),
        gateway.query_credential("sample-card-frozen"),
        gateway.query_access_policy("sample-person-frozen", DOOR_ID),
        *gateway.search_access_events(
            "access-credential-frozen-01",
            DOOR_ID,
            "sample-card-frozen",
        ),
    ]

    for snapshot in snapshots:
        dumped = str(snapshot.model_dump(mode="json"))
        assert "sample-card-no-not-real" not in dumped
        assert "sample-admin-pwd-not-real" not in dumped

