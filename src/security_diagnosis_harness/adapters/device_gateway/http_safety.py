"""设备 HTTP Adapter 共用的响应流安全边界。"""

from __future__ import annotations

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
) -> bytes:
    """流式读取响应，达到硬上限后立即停止，不缓冲剩余内容。"""
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)

    content = bytearray()
    for chunk in response.iter_bytes():
        if len(content) + len(chunk) > max_bytes:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)
        content.extend(chunk)
    return bytes(content)


__all__ = ["read_limited_response"]
