import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from security_diagnosis_harness.adapters.device_gateway.contract_service import (
    create_contract_app,
)
from security_diagnosis_harness.adapters.device_gateway.http_security_platform import (
    SecurityPlatformHttpAdapter,
    SecurityPlatformHttpSettings,
)
from security_diagnosis_harness.bootstrap.container import CAMERA_CASES_DATA_PATH
from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
    ResolvedCredential,
)
from security_diagnosis_harness.ports.device_gateway import DeviceGateway

FIXTURE_CREDENTIAL = "contract-fixture-value"


class FixtureCredentialResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, credential_reference: str) -> ResolvedCredential:
        self.calls += 1
        assert credential_reference == "fixture/security-platform"
        return ResolvedCredential(value=FIXTURE_CREDENTIAL)


class LocalAsgiTransport(httpx.BaseTransport):
    def __init__(self, app) -> None:
        self._client = TestClient(app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._client.request(
            request.method,
            str(request.url),
            headers=dict(request.headers),
        )
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


def settings(**changes) -> SecurityPlatformHttpSettings:
    values = {
        "base_url": "http://localhost",
        "allowed_hosts": {"localhost"},
        "credential_reference": "fixture/security-platform",
    }
    values.update(changes)
    return SecurityPlatformHttpSettings(**values)


def adapter() -> tuple[SecurityPlatformHttpAdapter, FixtureCredentialResolver]:
    app = create_contract_app(CAMERA_CASES_DATA_PATH, SecretStr(FIXTURE_CREDENTIAL))
    resolver = FixtureCredentialResolver()
    return (
        SecurityPlatformHttpAdapter(settings(), resolver, transport=LocalAsgiTransport(app)),
        resolver,
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.test",
        "ftp://localhost",
        "https://user:pass@example.test",
        "https://example.test?token=value",
        "https://example.test/#fragment",
    ],
)
def test_settings_reject_unsafe_base_urls(base_url: str) -> None:
    with pytest.raises(ValidationError):
        settings(base_url=base_url, allowed_hosts={"example.test", "localhost"})


def test_settings_requires_host_allowlist_match() -> None:
    with pytest.raises(ValidationError, match="allowlist"):
        settings(allowed_hosts={"127.0.0.1"})


def test_settings_rejects_literal_credential_in_reference() -> None:
    with pytest.raises(ValidationError, match="安全存储引用"):
        settings(credential_reference="token=plain-value")


def test_contract_service_rejects_missing_credential() -> None:
    app = create_contract_app(CAMERA_CASES_DATA_PATH, SecretStr(FIXTURE_CREDENTIAL))
    response = TestClient(app).get("/v1/devices/cam-offline-01/status")
    assert response.status_code == 401
    assert FIXTURE_CREDENTIAL not in response.text


def test_adapter_reads_status_channel_stream_pull_alarm_and_config() -> None:
    gateway, resolver = adapter()
    try:
        assert gateway.query_status("cam-offline-01").online is False
        assert gateway.query_channel_snapshot("cam-offline-01").channel_id == "1"
        assert gateway.query_stream_snapshot("cam-offline-01").device_id == "cam-offline-01"
        assert gateway.query_platform_pull_status("cam-offline-01").pull_status.value == "failed"
        assert gateway.search_alarm_events("cam-offline-01", limit=2)
        assert gateway.read_config_snapshot("cam-offline-01").redacted is True
        assert resolver.calls == 6
    finally:
        gateway.close()


def test_adapter_satisfies_existing_device_gateway_contract() -> None:
    gateway, _ = adapter()
    try:
        assert isinstance(gateway, DeviceGateway)
    finally:
        gateway.close()


def test_adapter_never_exposes_write_operations() -> None:
    gateway, _ = adapter()
    try:
        assert not hasattr(gateway, "update_config")
        assert not hasattr(gateway, "restart_device")
    finally:
        gateway.close()


def test_unimplemented_read_capability_is_controlled() -> None:
    gateway, _ = adapter()
    try:
        with pytest.raises(DeviceAdapterError) as excinfo:
            gateway.query_recording_plan("camera-1", "1")
        assert excinfo.value.kind is DeviceAdapterErrorKind.UNSUPPORTED_CAPABILITY
    finally:
        gateway.close()


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, DeviceAdapterErrorKind.AUTHENTICATION),
        (403, DeviceAdapterErrorKind.AUTHENTICATION),
        (408, DeviceAdapterErrorKind.TIMEOUT),
        (429, DeviceAdapterErrorKind.RATE_LIMITED),
        (500, DeviceAdapterErrorKind.UNAVAILABLE),
        (503, DeviceAdapterErrorKind.UNAVAILABLE),
        (404, DeviceAdapterErrorKind.INVALID_RESPONSE),
    ],
)
def test_http_statuses_map_to_stable_errors_without_retry(status: int, kind) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="password=backend-secret", request=request)

    gateway = SecurityPlatformHttpAdapter(
        settings(), FixtureCredentialResolver(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(DeviceAdapterError) as excinfo:
        gateway.query_status("camera-1")
    assert excinfo.value.kind is kind
    assert "backend-secret" not in str(excinfo.value)
    assert calls == 1


def test_invalid_json_is_controlled() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="not-json"))
    gateway = SecurityPlatformHttpAdapter(
        settings(), FixtureCredentialResolver(), transport=transport
    )
    with pytest.raises(DeviceAdapterError) as excinfo:
        gateway.query_status("camera-1")
    assert excinfo.value.kind is DeviceAdapterErrorKind.INVALID_RESPONSE


def test_transport_timeout_is_controlled_without_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("backend included secret", request=request)

    gateway = SecurityPlatformHttpAdapter(
        settings(), FixtureCredentialResolver(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(DeviceAdapterError) as excinfo:
        gateway.query_status("camera-1")
    assert excinfo.value.kind is DeviceAdapterErrorKind.TIMEOUT
    assert "secret" not in str(excinfo.value)
    assert calls == 1


def test_schema_mismatch_is_controlled() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"online": True}))
    gateway = SecurityPlatformHttpAdapter(
        settings(), FixtureCredentialResolver(), transport=transport
    )
    with pytest.raises(DeviceAdapterError) as excinfo:
        gateway.query_status("camera-1")
    assert excinfo.value.kind is DeviceAdapterErrorKind.INVALID_RESPONSE


def test_oversized_response_is_rejected() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 1_025))
    gateway = SecurityPlatformHttpAdapter(
        settings(max_response_bytes=1_024), FixtureCredentialResolver(), transport=transport
    )
    with pytest.raises(DeviceAdapterError) as excinfo:
        gateway.query_status("camera-1")
    assert excinfo.value.kind is DeviceAdapterErrorKind.INVALID_RESPONSE


def test_deep_json_is_rejected() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"a": {"b": {"c": 1}}})
    )
    gateway = SecurityPlatformHttpAdapter(
        settings(max_json_depth=2), FixtureCredentialResolver(), transport=transport
    )
    with pytest.raises(DeviceAdapterError) as excinfo:
        gateway.query_status("camera-1")
    assert excinfo.value.kind is DeviceAdapterErrorKind.INVALID_RESPONSE


def test_oversized_json_list_is_rejected() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=[{"event": index} for index in range(3)])
    )
    gateway = SecurityPlatformHttpAdapter(
        settings(max_list_items=2), FixtureCredentialResolver(), transport=transport
    )
    with pytest.raises(DeviceAdapterError) as excinfo:
        gateway.search_alarm_events("camera-1")
    assert excinfo.value.kind is DeviceAdapterErrorKind.INVALID_RESPONSE


def test_redirect_is_not_followed_and_is_rejected() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "https://outside.test"})

    gateway = SecurityPlatformHttpAdapter(
        settings(), FixtureCredentialResolver(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(DeviceAdapterError):
        gateway.query_status("camera-1")
    assert calls == 1


def test_authorization_header_is_ephemeral_and_not_in_adapter_repr() -> None:
    seen = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen = request.headers["authorization"]
        return httpx.Response(500)

    gateway = SecurityPlatformHttpAdapter(
        settings(), FixtureCredentialResolver(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(DeviceAdapterError):
        gateway.query_status("camera-1")
    assert seen == f"Bearer {FIXTURE_CREDENTIAL}"
    assert FIXTURE_CREDENTIAL not in repr(gateway)


def test_device_id_cannot_escape_controlled_path() -> None:
    seen_path = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_path
        seen_path = request.url.raw_path.decode()
        return httpx.Response(404)

    gateway = SecurityPlatformHttpAdapter(
        settings(), FixtureCredentialResolver(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(DeviceAdapterError):
        gateway.query_status("../../admin?token=value")
    assert "/../" not in seen_path
    assert "?token=" not in seen_path
