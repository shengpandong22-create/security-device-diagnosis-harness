import httpx
import pytest
from pydantic import ValidationError

from security_diagnosis_harness.adapters.device_gateway.onvif import (
    OnvifReadOnlyAdapter,
    OnvifReadOnlySettings,
)
from security_diagnosis_harness.domain.camera import PullStatus, StreamKind
from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
    ResolvedCredential,
)
from security_diagnosis_harness.ports.device_gateway import DeviceGateway

_SECRET = "unit-user:unit-password-not-real"
_DEVICE_RESPONSE = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Body><GetDeviceInformationResponse><Manufacturer>Lab</Manufacturer>
<Model>SimCam</Model><FirmwareVersion>1.0</FirmwareVersion>
</GetDeviceInformationResponse></s:Body></s:Envelope>"""
_PROFILES_RESPONSE = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Body><GetProfilesResponse><Profiles token="profile_main"><Name>main</Name></Profiles>
</GetProfilesResponse></s:Body></s:Envelope>"""
_GENERIC_PROFILES_RESPONSE = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Body><GetProfilesResponse>
<Profiles token="Profile_1"><Name>Profile 1</Name><VideoEncoderConfiguration>
<Encoding>H265</Encoding><Resolution><Width>1920</Width><Height>1080</Height></Resolution>
<RateControl><FrameRateLimit>25</FrameRateLimit><BitrateLimit>4096</BitrateLimit></RateControl>
</VideoEncoderConfiguration></Profiles>
<Profiles token="Profile_2"><Name>Profile 2</Name><VideoEncoderConfiguration>
<Encoding>H264</Encoding><Resolution><Width>640</Width><Height>360</Height></Resolution>
<RateControl><FrameRateLimit>15</FrameRateLimit><BitrateLimit>512</BitrateLimit></RateControl>
</VideoEncoderConfiguration></Profiles>
</GetProfilesResponse></s:Body></s:Envelope>"""
_URI_RESPONSE = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Body><GetStreamUriResponse><MediaUri>
<Uri>rtsp://unit-user:do-not-store@127.0.0.1:28554/profile_main</Uri>
</MediaUri></GetStreamUriResponse></s:Body></s:Envelope>"""


class _Resolver:
    def resolve(self, credential_reference: str) -> ResolvedCredential:
        assert credential_reference == "lab/onvif"
        return ResolvedCredential(value=_SECRET)


def _settings(**changes) -> OnvifReadOnlySettings:
    values = {
        "base_url": "http://127.0.0.1:28080",
        "allowed_hosts": {"127.0.0.1"},
        "credential_reference": "lab/onvif",
        "rtsp_probe_host": "127.0.0.1",
        "rtsp_probe_port": 28554,
    }
    values.update(changes)
    return OnvifReadOnlySettings(**values)


def _transport(request: httpx.Request) -> httpx.Response:
    body = request.content.decode()
    assert "unit-password-not-real" not in body
    assert "PasswordDigest" in body
    if "GetDeviceInformation" in body:
        content = _DEVICE_RESPONSE
    elif "GetProfiles" in body:
        content = _PROFILES_RESPONSE
    elif "GetStreamUri" in body:
        content = _URI_RESPONSE
    else:
        return httpx.Response(501, request=request)
    return httpx.Response(200, text=content, request=request)


def _adapter(transport=None) -> OnvifReadOnlyAdapter:
    return OnvifReadOnlyAdapter(
        _settings(),
        _Resolver(),
        transport=transport or httpx.MockTransport(_transport),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "http://camera.example", "allowed_hosts": {"camera.example"}},
        {"base_url": "https://user:password@camera.example", "allowed_hosts": {"camera.example"}},
        {"base_url": "https://camera.example?token=value", "allowed_hosts": {"camera.example"}},
        {"rtsp_probe_host": "other.invalid"},
        {"credential_reference": "password=literal"},
    ],
)
def test_settings_reject_unsafe_configuration(changes) -> None:
    with pytest.raises(ValidationError):
        _settings(**changes)


def test_adapter_reads_device_information_without_exposing_credentials() -> None:
    with _adapter() as adapter:
        snapshot = adapter.query_status("lab-camera")
    assert snapshot.online is True
    assert snapshot.source == "onvif_read_only"
    assert snapshot.extra == {"manufacturer": "Lab", "model": "SimCam", "firmware": "1.0"}
    assert "password" not in snapshot.model_dump_json().lower()


def test_adapter_reads_main_profile_and_checks_rtsp(monkeypatch) -> None:
    probed: list[str] = []
    with _adapter() as adapter:
        monkeypatch.setattr(
            adapter,
            "_rtsp_available",
            lambda stream_uri: probed.append(stream_uri) or True,
        )
        stream = adapter.query_stream_snapshot("lab-camera", StreamKind.MAIN)
    assert stream.pull_status is PullStatus.SUCCESS
    assert stream.error_code is None
    assert probed == ["rtsp://127.0.0.1:28554/profile_main"]
    assert "rtsp://" not in stream.model_dump_json()
    assert "do-not-store" not in stream.model_dump_json()


def test_missing_sub_profile_is_a_traceable_fact(monkeypatch) -> None:
    with _adapter() as adapter:
        monkeypatch.setattr(adapter, "_rtsp_available", lambda stream_uri: True)
        stream = adapter.query_stream_snapshot("lab-camera", StreamKind.SUB)
    assert stream.pull_status is PullStatus.FAILED
    assert stream.error_code == "PROFILE_NOT_FOUND"


@pytest.mark.parametrize(
    ("kind", "token", "encoding", "resolution", "frame_rate", "bitrate"),
    [
        (StreamKind.MAIN, "Profile_1", "H265", "1920x1080", 25, 4096),
        (StreamKind.SUB, "Profile_2", "H264", "640x360", 15, 512),
    ],
)
def test_generic_profile_names_use_encoder_facts(
    monkeypatch, kind, token, encoding, resolution, frame_rate, bitrate
) -> None:
    requested: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "GetProfiles" in body:
            content = _GENERIC_PROFILES_RESPONSE
        else:
            requested.append(body)
            content = _URI_RESPONSE
        return httpx.Response(200, text=content, request=request)

    with _adapter(httpx.MockTransport(transport)) as adapter:
        monkeypatch.setattr(adapter, "_rtsp_available", lambda stream_uri: True)
        stream = adapter.query_stream_snapshot("lab-camera", kind)

    assert token in requested[0]
    assert stream.encoding == encoding
    assert stream.resolution == resolution
    assert stream.frame_rate == frame_rate
    assert stream.bitrate_kbps == bitrate


def test_ambiguous_generic_profiles_are_not_guessed(monkeypatch) -> None:
    response = _GENERIC_PROFILES_RESPONSE.replace("1920", "640").replace(
        "1080", "360"
    ).replace("4096", "512").replace("25", "15")

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=response, request=request)

    with _adapter(httpx.MockTransport(transport)) as adapter:
        monkeypatch.setattr(adapter, "_rtsp_available", lambda stream_uri: True)
        stream = adapter.query_stream_snapshot("lab-camera", StreamKind.MAIN)

    assert stream.pull_status is PullStatus.FAILED
    assert stream.error_code == "PROFILE_NOT_FOUND"


def test_rtsp_unreachable_is_not_device_offline(monkeypatch) -> None:
    with _adapter() as adapter:
        monkeypatch.setattr(adapter, "_rtsp_available", lambda stream_uri: False)
        status = adapter.query_status("lab-camera")
        stream = adapter.query_stream_snapshot("lab-camera", StreamKind.MAIN)
    assert status.online is True
    assert stream.pull_status is PullStatus.FAILED
    assert stream.error_code == "RTSP_UNREACHABLE"


def test_stream_uri_host_or_port_cannot_escape_configured_probe_target() -> None:
    response = _URI_RESPONSE.replace("127.0.0.1:28554", "camera.invalid:8554")

    def transport(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        content = _PROFILES_RESPONSE if "GetProfiles" in body else response
        return httpx.Response(200, text=content, request=request)

    with _adapter(httpx.MockTransport(transport)) as adapter:
        with pytest.raises(DeviceAdapterError) as exc_info:
            adapter.query_stream_snapshot("lab-camera", StreamKind.MAIN)
    assert exc_info.value.kind is DeviceAdapterErrorKind.INVALID_RESPONSE


def test_allowed_device_uri_is_mapped_to_controlled_nat_probe(monkeypatch) -> None:
    response = _URI_RESPONSE.replace("127.0.0.1:28554", "onvif-simulator:8554")

    def transport(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        content = _PROFILES_RESPONSE if "GetProfiles" in body else response
        return httpx.Response(200, text=content, request=request)

    probed: list[str] = []
    adapter = OnvifReadOnlyAdapter(
        _settings(allowed_hosts={"127.0.0.1", "onvif-simulator"}),
        _Resolver(),
        transport=httpx.MockTransport(transport),
    )
    with adapter:
        monkeypatch.setattr(
            adapter,
            "_rtsp_available",
            lambda stream_uri: probed.append(stream_uri) or True,
        )
        stream = adapter.query_stream_snapshot("lab-camera", StreamKind.MAIN)

    assert stream.pull_status is PullStatus.SUCCESS
    assert probed == ["rtsp://127.0.0.1:28554/profile_main"]


@pytest.mark.parametrize(
    ("status_line", "expected"),
    [
        (b"RTSP/1.0 200 OK\r\n", True),
        (b"RTSP/1.0 401 Unauthorized\r\n", False),
        (b"RTSP/1.0 404 Not Found\r\n", False),
        (b"not-rtsp\r\n", False),
    ],
)
def test_rtsp_probe_requires_success_for_the_actual_uri(monkeypatch, status_line, expected):
    sent: list[bytes] = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def settimeout(self, timeout):
            assert timeout == 5.0

        def sendall(self, value):
            sent.append(value)

        def recv(self, size):
            assert size == 512
            return status_line

    monkeypatch.setattr(
        "security_diagnosis_harness.adapters.device_gateway.onvif.socket.create_connection",
        lambda target, timeout: Connection(),
    )
    with _adapter() as adapter:
        result = adapter._rtsp_available("rtsp://127.0.0.1:28554/profile_main")

    assert result is expected
    assert b"OPTIONS rtsp://127.0.0.1:28554/profile_main RTSP/1.0" in sent[0]
    assert b"do-not-store" not in sent[0]


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, DeviceAdapterErrorKind.AUTHENTICATION), (503, DeviceAdapterErrorKind.UNAVAILABLE)],
)
def test_http_failures_use_stable_error_kinds(status, kind) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="backend password=do-not-leak", request=request)

    with _adapter(httpx.MockTransport(fail)) as adapter:
        with pytest.raises(DeviceAdapterError) as exc_info:
            adapter.query_status("lab-camera")
    assert exc_info.value.kind is kind
    assert "do-not-leak" not in str(exc_info.value)
    assert _SECRET not in str(exc_info.value)


def test_timeout_uses_stable_error_kind() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret backend detail", request=request)

    with _adapter(httpx.MockTransport(timeout)) as adapter:
        with pytest.raises(DeviceAdapterError) as exc_info:
            adapter.query_status("lab-camera")
    assert exc_info.value.kind is DeviceAdapterErrorKind.TIMEOUT
    assert "secret" not in str(exc_info.value)


def test_invalid_xml_is_controlled() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="not-xml", request=request)
    )
    with _adapter(transport) as adapter:
        with pytest.raises(DeviceAdapterError) as exc_info:
            adapter.query_status("lab-camera")
    assert exc_info.value.kind is DeviceAdapterErrorKind.INVALID_RESPONSE


def test_write_capabilities_do_not_exist() -> None:
    adapter = _adapter()
    try:
        assert not hasattr(adapter, "set_config")
        assert not hasattr(adapter, "reboot")
        assert not hasattr(adapter, "ptz")
        assert isinstance(adapter, DeviceGateway)
    finally:
        adapter.close()
