"""设备 HTTP Adapter 共用的响应流安全边界。"""

from __future__ import annotations

from time import monotonic

import httpx

from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
)


def read_limited_response(
    response: httpx.Response,
    *,
    max_bytes: int,
    operation: str,
    deadline: float | None = None,
) -> bytes:
    """流式读取响应，达到硬上限后立即停止，不缓冲剩余内容。"""
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)

    content = bytearray()
    for chunk in response.iter_bytes():
        if deadline is not None and monotonic() > deadline:
            raise DeviceAdapterError(DeviceAdapterErrorKind.TIMEOUT, operation)
        if len(content) + len(chunk) > max_bytes:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)
        content.extend(chunk)
    if deadline is not None and monotonic() > deadline:
        raise DeviceAdapterError(DeviceAdapterErrorKind.TIMEOUT, operation)
    return bytes(content)


__all__ = ["read_limited_response"]
