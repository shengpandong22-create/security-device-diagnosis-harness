import pytest

from security_diagnosis_harness.adapters.device_gateway.onvif_discovery import (
    extract_xaddrs,
    summarize_xaddr,
)


def test_standard_xaddr_is_reduced_to_non_identifying_shape() -> None:
    shape = summarize_xaddr("http://192.0.2.44:80/onvif/device_service")
    assert shape.scheme == "http"
    assert shape.explicit_port is True
    assert shape.default_port is True
    assert shape.path_kind == "standard_device_service"
    assert shape.path_segment_count == 2
    assert "192.0.2.44" not in repr(shape)
    assert "/onvif/device_service" not in repr(shape)


def test_vendor_xaddr_does_not_persist_original_path_or_query() -> None:
    shape = summarize_xaddr("https://camera.invalid:8443/vendor/onvif?secret=value")
    assert shape.default_port is False
    assert shape.path_kind == "vendor_path"
    assert shape.query_present is True
    assert "camera.invalid" not in repr(shape)
    assert "secret" not in repr(shape)


@pytest.mark.parametrize("value", ["ftp://host/path", "http:///missing", "not-a-url"])
def test_invalid_xaddr_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        summarize_xaddr(value)


def test_extract_xaddrs_from_bounded_probe_match() -> None:
    payload = b'''<e:Envelope xmlns:e="urn:e" xmlns:d="urn:d"><e:Body>
    <d:ProbeMatches><d:ProbeMatch><d:XAddrs>
    http://192.0.2.44/onvif/device_service
    </d:XAddrs></d:ProbeMatch></d:ProbeMatches></e:Body></e:Envelope>'''
    assert extract_xaddrs(payload) == (
        "http://192.0.2.44/onvif/device_service",
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"not-xml",
        b'<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>',
        b"x" * 65_537,
    ],
    ids=["invalid-xml", "forbidden-dtd", "oversized"],
)
def test_extract_xaddrs_rejects_unsafe_response(payload: bytes) -> None:
    with pytest.raises(ValueError):
        extract_xaddrs(payload)
