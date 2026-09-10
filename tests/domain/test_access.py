"""门禁刷卡异常事实模型验收。"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, time
from pathlib import Path

import pydantic
import pytest

from security_diagnosis_harness.domain import access as access_module
from security_diagnosis_harness.domain.access import (
    ALL_WEEKDAYS,
    AccessControllerHealth,
    AccessControllerSnapshot,
    AccessControllerStatus,
    AccessDecision,
    AccessDenyReason,
    AccessEvent,
    AccessPolicySnapshot,
    AccessTimeRange,
    CredentialSnapshot,
    CredentialStatus,
    CredentialType,
    DoorLockStatus,
    DoorSnapshot,
    DoorStatus,
    is_access_sensitive_key,
    redact_access_sensitive_values,
)
from security_diagnosis_harness.domain.device import REDACTED_VALUE

DEVICE_ID = "access-controller-01"
CONTROLLER_ID = "ctrl-01"
DOOR_ID = "door-east-01"
CREDENTIAL_ID = "sample-card-id-not-real"
PERSON_ID = "sample-person-id-not-real"
BASE_TIME = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)


# --------------------------------------------------------------- 控制器
def test_controller_snapshot_expresses_online_and_healthy():
    snapshot = AccessControllerSnapshot(
        device_id=DEVICE_ID,
        controller_id=CONTROLLER_ID,
        status=AccessControllerStatus.ONLINE,
        health=AccessControllerHealth.HEALTHY,
        last_seen_at=BASE_TIME,
    )

    assert snapshot.device_id == DEVICE_ID
    assert snapshot.controller_id == CONTROLLER_ID
    assert snapshot.status is AccessControllerStatus.ONLINE
    assert snapshot.health is AccessControllerHealth.HEALTHY
    assert snapshot.last_seen_at == BASE_TIME


def test_controller_snapshot_expresses_offline_device():
    snapshot = AccessControllerSnapshot(
        device_id=DEVICE_ID,
        controller_id=CONTROLLER_ID,
        status=AccessControllerStatus.OFFLINE,
        health=AccessControllerHealth.ERROR,
        last_error="controller heartbeat lost",
    )

    assert snapshot.status is AccessControllerStatus.OFFLINE
    assert snapshot.health is AccessControllerHealth.ERROR
    assert snapshot.last_error == "controller heartbeat lost"


def test_controller_snapshot_rejects_blank_identifiers():
    for field in ("device_id", "controller_id"):
        payload = {"device_id": DEVICE_ID, "controller_id": CONTROLLER_ID}
        payload[field] = ""

        with pytest.raises(pydantic.ValidationError):
            AccessControllerSnapshot(**payload)


# --------------------------------------------------------------- 门状态
def test_door_snapshot_expresses_closed_and_locked():
    door = DoorSnapshot(
        device_id=DEVICE_ID,
        controller_id=CONTROLLER_ID,
        door_id=DOOR_ID,
        door_status=DoorStatus.CLOSED,
        lock_status=DoorLockStatus.LOCKED,
    )

    assert door.door_status is DoorStatus.CLOSED
    assert door.lock_status is DoorLockStatus.LOCKED
    assert door.has_lock_error is False


def test_door_snapshot_expresses_lock_jammed():
    door = DoorSnapshot(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        door_status=DoorStatus.CLOSED,
        lock_status=DoorLockStatus.JAMMED,
        last_error="lock motor blocked",
    )

    assert door.lock_status is DoorLockStatus.JAMMED
    assert door.has_lock_error is True
    assert door.last_error == "lock motor blocked"


def test_door_snapshot_expresses_forced_open():
    door = DoorSnapshot(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        door_status=DoorStatus.FORCED_OPEN,
        lock_status=DoorLockStatus.UNLOCKED,
    )

    assert door.door_status is DoorStatus.FORCED_OPEN


def test_door_snapshot_rejects_blank_door_id():
    with pytest.raises(pydantic.ValidationError):
        DoorSnapshot(device_id=DEVICE_ID, door_id="")


# --------------------------------------------------------------- 凭证
def test_credential_snapshot_expresses_active_card():
    credential = CredentialSnapshot(
        device_id=DEVICE_ID,
        credential_id=CREDENTIAL_ID,
        credential_type=CredentialType.CARD,
        status=CredentialStatus.ACTIVE,
        expires_at=datetime(2026, 12, 31, tzinfo=UTC),
    )

    assert credential.credential_type is CredentialType.CARD
    assert credential.status is CredentialStatus.ACTIVE
    assert credential.is_valid is True
    assert credential.redacted is True
    assert credential.credential_id == REDACTED_VALUE


@pytest.mark.parametrize(
    "status",
    [CredentialStatus.FROZEN, CredentialStatus.EXPIRED, CredentialStatus.LOST],
)
def test_credential_snapshot_expresses_invalid_credential(status):
    credential = CredentialSnapshot(
        device_id=DEVICE_ID,
        credential_id=CREDENTIAL_ID,
        credential_type=CredentialType.CARD,
        status=status,
    )

    assert credential.is_valid is False
    assert credential.credential_id == REDACTED_VALUE


def test_credential_snapshot_rejects_blank_credential_id():
    with pytest.raises(pydantic.ValidationError):
        CredentialSnapshot(
            device_id=DEVICE_ID,
            credential_id="",
            credential_type=CredentialType.CARD,
        )


# --------------------------------------------------------------- 授权策略
def test_access_policy_snapshot_expresses_permission_granted():
    policy = AccessPolicySnapshot(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        person_id=PERSON_ID,
        credential_id=CREDENTIAL_ID,
        allowed=True,
        time_ranges=[AccessTimeRange(start=time(8, 0), end=time(18, 0))],
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        valid_until=datetime(2026, 12, 31, tzinfo=UTC),
    )

    assert policy.allowed is True
    assert policy.door_id == DOOR_ID
    assert policy.has_time_ranges is True
    assert policy.person_id == REDACTED_VALUE
    assert policy.credential_id == REDACTED_VALUE
    assert policy.redacted is True


def test_access_policy_snapshot_expresses_permission_denied():
    policy = AccessPolicySnapshot(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        person_id=PERSON_ID,
        credential_id=CREDENTIAL_ID,
        allowed=False,
    )

    assert policy.allowed is False


def test_access_policy_time_range_supports_crossing_midnight():
    access_range = AccessTimeRange(start=time(22, 0), end=time(6, 0), weekdays=[1, 5])
    policy = AccessPolicySnapshot(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        person_id=PERSON_ID,
        credential_id=CREDENTIAL_ID,
        allowed=True,
        time_ranges=[access_range],
    )

    assert access_range.crosses_midnight is True
    assert access_range.is_all_day is False
    assert policy.crossing_time_ranges == [access_range]


def test_access_policy_time_range_all_day_is_not_crossing():
    access_range = AccessTimeRange(start=time(0, 0), end=time(0, 0))

    assert access_range.is_all_day is True
    assert access_range.crosses_midnight is False
    assert access_range.weekdays == list(ALL_WEEKDAYS)


@pytest.mark.parametrize("weekday", [0, 8, -1])
def test_access_policy_time_range_rejects_invalid_weekday(weekday):
    with pytest.raises(pydantic.ValidationError):
        AccessTimeRange(start=time(8, 0), end=time(18, 0), weekdays=[weekday])


@pytest.mark.parametrize("field", ["device_id", "door_id", "person_id", "credential_id"])
def test_access_policy_rejects_blank_identifiers(field):
    payload = {
        "device_id": DEVICE_ID,
        "door_id": DOOR_ID,
        "person_id": PERSON_ID,
        "credential_id": CREDENTIAL_ID,
        "allowed": True,
    }
    payload[field] = ""

    with pytest.raises(pydantic.ValidationError):
        AccessPolicySnapshot(**payload)


# --------------------------------------------------------------- 刷卡事件
def test_access_event_expresses_card_denied_by_permission():
    event = AccessEvent(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        credential_id=CREDENTIAL_ID,
        person_id=PERSON_ID,
        credential_type=CredentialType.CARD,
        decision=AccessDecision.DENIED,
        deny_reason=AccessDenyReason.PERMISSION_DENIED,
        occurred_at=BASE_TIME,
    )

    assert event.decision is AccessDecision.DENIED
    assert event.deny_reason is AccessDenyReason.PERMISSION_DENIED
    assert event.credential_id == REDACTED_VALUE
    assert event.person_id == REDACTED_VALUE
    assert event.redacted is True


def test_access_event_expresses_controller_timeout():
    event = AccessEvent(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        credential_id=CREDENTIAL_ID,
        credential_type=CredentialType.CARD,
        decision=AccessDecision.TIMEOUT,
        deny_reason=AccessDenyReason.CONTROLLER_TIMEOUT,
    )

    assert event.decision is AccessDecision.TIMEOUT
    assert event.deny_reason is AccessDenyReason.CONTROLLER_TIMEOUT


def test_access_event_expresses_time_window_denied():
    event = AccessEvent(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        credential_id=CREDENTIAL_ID,
        credential_type=CredentialType.CARD,
        decision=AccessDecision.DENIED,
        deny_reason=AccessDenyReason.TIME_WINDOW_DENIED,
    )

    assert event.deny_reason is AccessDenyReason.TIME_WINDOW_DENIED


def test_access_event_granted_has_no_deny_reason():
    event = AccessEvent(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        credential_id=CREDENTIAL_ID,
        credential_type=CredentialType.FACE,
        decision=AccessDecision.GRANTED,
    )

    assert event.decision is AccessDecision.GRANTED
    assert event.deny_reason is None


def test_access_event_granted_rejects_deny_reason():
    with pytest.raises(pydantic.ValidationError, match="decision=granted"):
        AccessEvent(
            device_id=DEVICE_ID,
            door_id=DOOR_ID,
            credential_id=CREDENTIAL_ID,
            decision=AccessDecision.GRANTED,
            deny_reason=AccessDenyReason.DOOR_LOCK_ERROR,
        )


def test_access_event_denied_requires_deny_reason():
    with pytest.raises(pydantic.ValidationError, match="decision=denied"):
        AccessEvent(
            device_id=DEVICE_ID,
            door_id=DOOR_ID,
            credential_id=CREDENTIAL_ID,
            decision=AccessDecision.DENIED,
        )


def test_access_event_denied_allows_unknown_reason():
    event = AccessEvent(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        credential_id=CREDENTIAL_ID,
        decision=AccessDecision.DENIED,
        deny_reason=AccessDenyReason.UNKNOWN,
    )

    assert event.deny_reason is AccessDenyReason.UNKNOWN


@pytest.mark.parametrize("field", ["device_id", "door_id", "credential_id"])
def test_access_event_rejects_blank_identifiers(field):
    payload = {
        "device_id": DEVICE_ID,
        "door_id": DOOR_ID,
        "credential_id": CREDENTIAL_ID,
        "decision": AccessDecision.GRANTED,
    }
    payload[field] = ""

    with pytest.raises(pydantic.ValidationError):
        AccessEvent(**payload)


# --------------------------------------------------------------- 脱敏
@pytest.mark.parametrize(
    "payload",
    [
        {"card_no": "330106000001"},
        {"card-number": "330106000002"},
        {"face_template": "face-vector-bytes"},
        {"fingerprint_template": "fingerprint-template"},
        {"pin": "123456"},
        {"phone": "sample-phone-not-real"},
        {"mobile": "sample-mobile-not-real"},
        {"id_card": "sample-id-card-not-real"},
        {"api_token": "plain-token"},
        {"password": "plain-password"},
        {"nested": {"person_id": "person-001"}},
        {"items": [{"card_id": "card-001"}]},
    ],
)
def test_access_sensitive_extra_values_are_redacted(payload):
    cleaned, changed = redact_access_sensitive_values(payload)

    assert changed is True
    assert "330106000001" not in str(cleaned)
    assert "330106000002" not in str(cleaned)
    assert "face-vector-bytes" not in str(cleaned)
    assert "fingerprint-template" not in str(cleaned)
    assert "123456" not in str(cleaned)
    assert "sample-phone-not-real" not in str(cleaned)
    assert "sample-mobile-not-real" not in str(cleaned)
    assert "sample-id-card-not-real" not in str(cleaned)
    assert "plain-token" not in str(cleaned)
    assert "plain-password" not in str(cleaned)


def test_access_sensitive_extra_redacts_inside_model():
    event = AccessEvent(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        credential_id=CREDENTIAL_ID,
        decision=AccessDecision.DENIED,
        deny_reason=AccessDenyReason.PERMISSION_DENIED,
        extra={"operator_phone": "sample-phone-not-real", "nested": {"face_id": "face-1"}},
    )

    assert event.redacted is True
    assert "sample-phone-not-real" not in str(event.extra)
    assert "face-1" not in str(event.extra)


def test_access_non_sensitive_extra_is_preserved():
    door = DoorSnapshot(
        device_id=DEVICE_ID,
        door_id=DOOR_ID,
        extra={"door_name": "east gate", "firmware_version": "v2.3.1"},
    )

    assert door.redacted is False
    assert door.extra["door_name"] == "east gate"
    assert door.extra["firmware_version"] == "v2.3.1"


@pytest.mark.parametrize(
    "key",
    [
        "card_no",
        "card-number",
        "card_id",
        "face_feature",
        "fingerprint_template",
        "person_id",
        "id_card",
        "phone",
        "mobile",
        "pin",
        "secret",
    ],
)
def test_is_access_sensitive_key_matches_expected_keys(key):
    assert is_access_sensitive_key(key) is True


@pytest.mark.parametrize("key", ["door_name", "firmware", "location", "note"])
def test_is_access_sensitive_key_ignores_plain_keys(key):
    assert is_access_sensitive_key(key) is False


# --------------------------------------------------------------- 枚举与架构边界
def test_enum_values_match_phase3a_specification():
    assert {item.value for item in AccessControllerStatus} == {
        "online",
        "offline",
        "unknown",
    }
    assert {item.value for item in AccessControllerHealth} == {
        "healthy",
        "degraded",
        "error",
        "unknown",
    }
    assert {item.value for item in DoorStatus} == {
        "closed",
        "open",
        "forced_open",
        "held_open",
        "unknown",
    }
    assert {item.value for item in DoorLockStatus} == {
        "locked",
        "unlocked",
        "jammed",
        "unknown",
    }
    assert {item.value for item in CredentialType} == {
        "card",
        "face",
        "qr_code",
        "fingerprint",
        "unknown",
    }
    assert {item.value for item in CredentialStatus} == {
        "active",
        "frozen",
        "expired",
        "lost",
        "unknown",
    }
    assert {item.value for item in AccessDecision} == {
        "granted",
        "denied",
        "timeout",
        "unknown",
    }
    assert {item.value for item in AccessDenyReason} == {
        "permission_denied",
        "expired_credential",
        "frozen_credential",
        "time_window_denied",
        "controller_offline",
        "controller_timeout",
        "door_lock_error",
        "unknown",
    }


@pytest.mark.parametrize(
    "model_cls",
    [
        AccessControllerSnapshot,
        DoorSnapshot,
        CredentialSnapshot,
        AccessPolicySnapshot,
        AccessEvent,
    ],
)
def test_access_models_are_pydantic_models(model_cls):
    assert issubclass(model_cls, pydantic.BaseModel)


@pytest.mark.parametrize(
    "model_cls",
    [
        AccessControllerSnapshot,
        DoorSnapshot,
        CredentialSnapshot,
        AccessPolicySnapshot,
        AccessEvent,
    ],
)
def test_access_models_do_not_express_conclusions(model_cls):
    fields = set(model_cls.model_fields)

    for forbidden in ("confidence", "root_cause", "conclusion", "final_status"):
        assert forbidden not in fields


def test_access_module_imports_only_domain_dependencies():
    source = Path(access_module.__file__).read_text(encoding="utf-8")

    tree = ast.parse(source)
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert imported_roots <= {
        "__future__",
        "re",
        "datetime",
        "enum",
        "typing",
        "pydantic",
        "security_diagnosis_harness",
    }

    for forbidden in ("fastapi", "sqlalchemy", "alembic", "openai", "httpx", "requests"):
        assert forbidden not in source.lower()
