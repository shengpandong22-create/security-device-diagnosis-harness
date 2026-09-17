"""WS-Discovery XAddr 的低敏结构摘要。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit
from xml.etree import ElementTree

_MAX_DISCOVERY_BYTES = 65_536
_MAX_XADDRS = 8


@dataclass(frozen=True)
class OnvifXAddrShape:
    """可持久化的 XAddr 结构；刻意不包含 host、完整 URL 或原始路径。"""

    scheme: str
    explicit_port: bool
    default_port: bool
    path_kind: str
    path_segment_count: int
    query_present: bool


def summarize_xaddr(value: str) -> OnvifXAddrShape:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.fragment:
        raise ValueError("invalid ONVIF XAddr")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid ONVIF XAddr port") from exc
    explicit_port = port is not None
    default_port = port in {None, 80 if parsed.scheme == "http" else 443}
    normalized = parsed.path.rstrip("/").lower()
    if normalized == "/onvif/device_service":
        path_kind = "standard_device_service"
    elif normalized.startswith("/onvif/"):
        path_kind = "nonstandard_onvif_path"
    else:
        path_kind = "vendor_path"
    return OnvifXAddrShape(
        scheme=parsed.scheme,
        explicit_port=explicit_port,
        default_port=default_port,
        path_kind=path_kind,
        path_segment_count=len([item for item in parsed.path.split("/") if item]),
        query_present=bool(parsed.query),
    )


def extract_xaddrs(content: bytes) -> tuple[str, ...]:
    """从受限 ProbeMatch 中提取 XAddr；返回值只能短暂留在调用内存。"""
    if len(content) > _MAX_DISCOVERY_BYTES:
        raise ValueError("discovery response too large")
    lowered = content.lower().replace(b"\x00", b"")
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("forbidden discovery XML")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ValueError("invalid discovery XML") from exc
    values: list[str] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "XAddrs" or not element.text:
            continue
        values.extend(element.text.split())
        if len(values) > _MAX_XADDRS:
            raise ValueError("too many discovery XAddrs")
    return tuple(values)
