from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
    DeviceAsset,
    DeviceCapability,
    DeviceConnectionProfile,
    DeviceRequestContext,
)


def test_device_asset_expresses_deterministic_adapter_and_capabilities() -> None:
    asset = DeviceAsset(
        device_id="camera-1",
        device_type="camera",
        adapter_key="simulator",
        capabilities={DeviceCapability.STATUS, DeviceCapability.STREAM},
    )
    assert asset.adapter_key == "simulator"
    assert asset.capabilities == {DeviceCapability.STATUS, DeviceCapability.STREAM}


@pytest.mark.parametrize(
    "endpoint_alias",
    ["https://user:pass@example.test", "host@tenant", "primary?token=secret"],
)
def test_connection_profile_rejects_urls_and_credentials(endpoint_alias: str) -> None:
    with pytest.raises(ValidationError, match="受控别名"):
        DeviceConnectionProfile(
            protocol="https",
            endpoint_alias=endpoint_alias,
            credential_reference="credential-ref",
        )


def test_connection_profile_contains_reference_not_secret() -> None:
    profile = DeviceConnectionProfile(
        protocol="https",
        endpoint_alias="security-platform-primary",
        credential_reference="vault/security/read-only",
    )
    assert not hasattr(profile, "password")
    assert not hasattr(profile, "token")


def test_request_context_carries_deadline_and_permission() -> None:
    context = DeviceRequestContext(
        request_id="req-1",
        diagnosis_id="diag-1",
        deadline=datetime.now(UTC) + timedelta(seconds=5),
        source="tool_registry",
        permissions={"device:read"},
    )
    assert context.deadline.tzinfo is not None
    assert "device:read" in context.permissions


@pytest.mark.parametrize("kind", list(DeviceAdapterErrorKind))
def test_adapter_error_has_stable_safe_message(kind: DeviceAdapterErrorKind) -> None:
    error = DeviceAdapterError(kind, "query_status")
    message = str(error)
    assert kind.value in message
    assert "password" not in message.lower()
    assert "http" not in message.lower()

