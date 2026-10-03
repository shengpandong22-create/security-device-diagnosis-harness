from __future__ import annotations

import httpx
import pytest

from security_diagnosis_harness.adapters.device_gateway import http_safety
from security_diagnosis_harness.domain.device_integration import DeviceAdapterError
from security_diagnosis_harness.http_stream import (
    ResponseDeadlineExceeded,
    read_bounded_bytes,
)


class _Chunks(httpx.SyncByteStream):
    def __iter__(self):
        yield b"a"
        yield b"b"


def _response() -> httpx.Response:
    return httpx.Response(
        200, request=httpx.Request("GET", "https://example.test"), stream=_Chunks()
    )


def test_generic_stream_stops_when_total_deadline_expires(monkeypatch):
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(
        "security_diagnosis_harness.http_stream.monotonic", lambda: next(ticks)
    )
    response = _response()

    with pytest.raises(ResponseDeadlineExceeded):
        read_bounded_bytes(response, 100, deadline=1.0)

    assert response.is_closed


def test_device_stream_maps_total_deadline_to_timeout(monkeypatch):
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(http_safety, "monotonic", lambda: next(ticks))

    with pytest.raises(DeviceAdapterError) as excinfo:
        http_safety.read_limited_response(
            _response(), max_bytes=100, operation="probe", deadline=1.0
        )

    assert excinfo.value.kind.value == "timeout"
