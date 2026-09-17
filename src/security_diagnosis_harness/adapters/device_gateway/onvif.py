"""严格只读的 ONVIF Device/Media Adapter。"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import re
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree
from xml.parsers import expat
from xml.sax.saxutils import escape

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.adapters.device_gateway.http_safety import (
    read_limited_response,
)
from security_diagnosis_harness.domain.camera import (
    ChannelSnapshot,
    ChannelStatus,
    PullStatus,
    StreamKind,
    StreamSnapshot,
)
from security_diagnosis_harness.domain.device import (
    DeviceConfigSnapshot,
    DeviceSnapshot,
    RecordingStatus,
    StreamStatus,
)
from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
)
from security_diagnosis_harness.ports.credentials import CredentialResolverPort

__all__ = [
    "OnvifAuthenticationMode",
    "OnvifReadOnlyAdapter",
    "OnvifReadOnlySettings",
    "OnvifMediaDiagnosticResult",
    "OnvifSingleProbeResult",
    "RtspDiagnosticResult",
]


class OnvifAuthenticationMode(StrEnum):
    """ONVIF 认证策略；必须由授权真机配置显式选择。"""

    WS_SECURITY = "ws_security"
    HTTP_DIGEST = "http_digest"


@dataclass(frozen=True)
class OnvifSingleProbeResult:
    """单次真机只读探针的脱敏结果；不包含地址、凭证、URI 或 Profile token。"""

    authenticated: bool
    manufacturer_present: bool
    model_present: bool
    firmware_present: bool
    profile_count: int
    main_profile_present: bool
    main_encoding: str | None
    main_resolution: str | None
    rtsp_reachable: bool


@dataclass(frozen=True)
class RtspDiagnosticResult:
    """单次未认证 OPTIONS 的脱敏分类，不保留 URI、地址或原始响应。"""

    connection_stage: str
    status_category: str | None = None
    authentication_scheme: str | None = None

    @property
    def reachable(self) -> bool:
        return self.status_category == "success"


@dataclass(frozen=True)
class OnvifMediaDiagnosticResult:
    """Profiles、Stream URI 与单次 RTSP OPTIONS 的脱敏诊断结果。"""

    profile_count: int
    main_profile_present: bool
    main_encoding: str | None
    main_resolution: str | None
    rtsp: RtspDiagnosticResult


@dataclass(frozen=True)
class _MediaProfile:
    token: str
    name: str
    encoding: str | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: int | None = None
    bitrate_kbps: int | None = None

    @property
    def resolution(self) -> str | None:
        if self.width is None or self.height is None:
            return None
        return f"{self.width}x{self.height}"

    @property
    def media_rank(self) -> tuple[int, int, int]:
        pixels = (self.width or 0) * (self.height or 0)
        return (pixels, self.bitrate_kbps or 0, self.frame_rate or 0)


class _XmlLimitExceeded(ValueError):
    """SOAP XML 在建树过程中超过资源边界。"""


#: 禁止出现在 SOAP 响应中的标记（DTD / 内部与外部实体声明）。
_FORBIDDEN_MARKUP = (b"<!doctype", b"<!entity")
_DIGEST_PARAMETER = re.compile(r'(\w+)=(?:"([^"]*)"|([^,\s]+))')


def _contains_forbidden_markup(content: bytes) -> bool:
    """编码无关地检测 DTD / 实体声明，覆盖 UTF-8、UTF-16 与 UTF-32。

    先在原始字节上比较；再对"去除 NUL 后"的字节序列比较一次——UTF-16/UTF-32 的
    每个 ASCII 字符之间会插入 NUL 字节，去 NUL 后仍能还原出 ``<!DOCTYPE`` /
    ``<!ENTITY``，因此无法通过改变编码绕过检测。ONVIF SOAP 响应不需要 DTD，
    命中即 fail-closed。
    """
    lowered = content.lower()
    if any(marker in lowered for marker in _FORBIDDEN_MARKUP):
        return True
    without_nul = lowered.replace(b"\x00", b"")
    return any(marker in without_nul for marker in _FORBIDDEN_MARKUP)


def _reject_forbidden_xml_constructs(content: bytes) -> None:
    """Use parser events to reject DTD, entity declarations and external entities."""
    parser = expat.ParserCreate()

    def reject(*_args: object) -> None:
        raise _XmlLimitExceeded

    def reject_external(*_args: object) -> int:
        raise _XmlLimitExceeded

    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.UnparsedEntityDeclHandler = reject
    parser.NotationDeclHandler = reject
    parser.ExternalEntityRefHandler = reject_external
    parser.Parse(content, True)


class _BoundedTreeBuilder(ElementTree.TreeBuilder):
    def __init__(self, settings: OnvifReadOnlySettings) -> None:
        super().__init__()
        self._settings = settings
        self._elements = 0
        self._depth = 0
        self._attributes = 0
        self._attribute_chars = 0
        self._text_chars = 0

    def start(self, tag: str, attrs: dict[str, str]) -> ElementTree.Element:
        self._elements += 1
        self._depth += 1
        self._attributes += len(attrs)
        # 属性名与值的总字符预算：属性数量有限不代表内容规模有限。
        self._attribute_chars += len(tag) + sum(
            len(name) + len(value) for name, value in attrs.items()
        )
        if (
            self._elements > self._settings.max_xml_elements
            or self._depth > self._settings.max_xml_depth
            or self._attributes > self._settings.max_xml_attributes
            or self._attribute_chars > self._settings.max_xml_attribute_chars
        ):
            raise _XmlLimitExceeded
        return super().start(tag, attrs)

    def end(self, tag: str) -> ElementTree.Element:
        element = super().end(tag)
        self._depth -= 1
        return element

    def data(self, data: str) -> None:
        self._text_chars += len(data)
        if self._text_chars > self._settings.max_xml_text_chars:
            raise _XmlLimitExceeded
        super().data(data)


class OnvifReadOnlySettings(BaseModel):
    """ONVIF 只读端点配置；不保存实际用户名或密码。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    allowed_hosts: frozenset[str]
    credential_reference: str = Field(min_length=1)
    rtsp_probe_host: str
    rtsp_probe_port: int = Field(ge=1, le=65535)
    allow_private_http: bool = False
    authentication_mode: OnvifAuthenticationMode = OnvifAuthenticationMode.WS_SECURITY
    max_http_exchanges_per_operation: int = Field(default=1, ge=1, le=2)
    device_service_path: str = "/onvif/device_service"
    media_service_path: str = "/onvif/media_service"
    allow_private_stream_uri_host_rewrite: bool = False
    connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    read_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    max_response_bytes: int = Field(default=256_000, ge=1_024, le=2_000_000)
    max_xml_elements: int = Field(default=4_096, ge=1, le=20_000)
    max_xml_depth: int = Field(default=32, ge=2, le=128)
    max_xml_attributes: int = Field(default=4_096, ge=0, le=20_000)
    max_xml_attribute_chars: int = Field(default=64_000, ge=0, le=2_000_000)
    max_xml_text_chars: int = Field(default=128_000, ge=0, le=2_000_000)

    @model_validator(mode="after")
    def _validate_endpoint(self) -> OnvifReadOnlySettings:
        parsed = urlsplit(self.base_url)
        host = (parsed.hostname or "").lower()
        allowed = {item.lower() for item in self.allowed_hosts}
        local = host in {"localhost", "127.0.0.1", "::1"}
        try:
            private_ip = ipaddress.ip_address(host).is_private
        except ValueError:
            private_ip = False
        private_http = parsed.scheme == "http" and self.allow_private_http and private_ip
        if (
            parsed.scheme != "https"
            and not (parsed.scheme == "http" and local)
            and not private_http
        ):
            raise ValueError("ONVIF base_url 只允许 HTTPS 或本机 HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("ONVIF base_url 禁止 userinfo、查询参数和 fragment")
        if host not in allowed or self.rtsp_probe_host.lower() not in allowed:
            raise ValueError("ONVIF/RTSP host 不在 allowlist")
        if self.authentication_mode is OnvifAuthenticationMode.HTTP_DIGEST:
            if self.max_http_exchanges_per_operation != 2:
                raise ValueError("HTTP Digest 必须显式预算两次 HTTP 交换")
        elif self.max_http_exchanges_per_operation != 1:
            raise ValueError("WS-Security 只允许一次 HTTP 交换")
        for path in (self.device_service_path, self.media_service_path):
            if not path.startswith("/") or "?" in path or "#" in path or ".." in path:
                raise ValueError("ONVIF service path 必须是受控绝对路径")
        lowered = self.credential_reference.lower()
        if any(token in lowered for token in ("=", "bearer ", "://", "@")):
            raise ValueError("credential_reference 不能包含实际凭证")
        return self


class OnvifReadOnlyAdapter:
    """读取 ONVIF Device/Media 事实；不暴露任何设备写方法。"""

    adapter_key = "onvif_read_only"

    def __init__(
        self,
        settings: OnvifReadOnlySettings,
        credential_resolver: CredentialResolverPort,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._credential_resolver = credential_resolver
        self._client = httpx.Client(
            base_url=settings.base_url.rstrip("/"),
            timeout=httpx.Timeout(
                settings.read_timeout_seconds,
                connect=settings.connect_timeout_seconds,
            ),
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OnvifReadOnlyAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _credentials(self) -> tuple[str, str]:
        resolved = self._credential_resolver.resolve(self._settings.credential_reference)
        try:
            value = resolved.value.get_secret_value()
            username, separator, password = value.partition(":")
            if not separator or not username or not password:
                raise DeviceAdapterError(
                    DeviceAdapterErrorKind.AUTHENTICATION, "resolve_credential"
                )
            return username, password
        finally:
            del resolved

    @staticmethod
    def _security_header(username: str, password: str) -> str:
        nonce = secrets.token_bytes(16)
        created = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        digest = base64.b64encode(
            hashlib.sha1(  # noqa: S324 - ONVIF PasswordDigest mandates SHA-1
                nonce + created.encode() + password.encode(), usedforsecurity=False
            ).digest()
        ).decode()
        return f"""
        <wsse:Security s:mustUnderstand="1"
          xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
          xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
          <wsse:UsernameToken>
            <wsse:Username>{escape(username)}</wsse:Username>
            <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>
            <wsse:Nonce>{base64.b64encode(nonce).decode()}</wsse:Nonce>
            <wsu:Created>{created}</wsu:Created>
          </wsse:UsernameToken>
        </wsse:Security>"""

    def _soap(self, service: str, body: str, operation: str) -> ElementTree.Element:
        username, password = self._credentials()
        security = (
            self._security_header(username, password)
            if self._settings.authentication_mode is OnvifAuthenticationMode.WS_SECURITY
            else ""
        )
        envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Header>{security}</s:Header>
          <s:Body>{body}</s:Body>
        </s:Envelope>"""
        auth = (
            httpx.DigestAuth(username, password)
            if self._settings.authentication_mode is OnvifAuthenticationMode.HTTP_DIGEST
            else None
        )
        del username, password, security
        namespace = (
            "http://www.onvif.org/ver10/device/wsdl"
            if service == "device"
            else "http://www.onvif.org/ver10/media/wsdl"
        )
        action = f"{namespace}/{''.join(part.title() for part in operation.split('_'))}"
        try:
            with self._client.stream(
                "POST",
                (
                    self._settings.device_service_path
                    if service == "device"
                    else self._settings.media_service_path
                ),
                content=envelope.encode(),
                headers={
                    "Content-Type": (
                        'application/soap+xml; charset=utf-8; action="'
                        f'{action}"'
                    ),
                    # 部分大华/IMOU 固件仍要求兼容 SOAPAction header。
                    "SOAPAction": f'"{action}"',
                },
                auth=auth,
            ) as response:
                if response.status_code in {401, 403}:
                    raise DeviceAdapterError(
                        DeviceAdapterErrorKind.AUTHENTICATION,
                        operation,
                        f"http_{response.status_code}",
                    )
                if response.status_code >= 500:
                    raise DeviceAdapterError(
                        DeviceAdapterErrorKind.UNAVAILABLE,
                        operation,
                        "http_5xx",
                    )
                if response.status_code >= 400 or response.is_redirect:
                    code = "redirect" if response.is_redirect else f"http_{response.status_code}"
                    raise DeviceAdapterError(
                        DeviceAdapterErrorKind.INVALID_RESPONSE,
                        operation,
                        code,
                    )
                content = read_limited_response(
                    response,
                    max_bytes=self._settings.max_response_bytes,
                    operation=operation,
                )
        except httpx.TimeoutException as exc:
            raise DeviceAdapterError(DeviceAdapterErrorKind.TIMEOUT, operation) from exc
        except httpx.RequestError as exc:
            raise DeviceAdapterError(DeviceAdapterErrorKind.UNAVAILABLE, operation) from exc
        finally:
            del auth, envelope
        root = self._parse_xml(content, operation)
        if any(element.tag.endswith("Fault") for element in root.iter()):
            raise DeviceAdapterError(
                DeviceAdapterErrorKind.AUTHENTICATION,
                operation,
                "soap_fault",
            )
        return root

    def _parse_xml(self, content: bytes, operation: str) -> ElementTree.Element:
        """解析受限 SOAP XML；拒绝 DTD/实体并限制树规模、深度、属性和文本。

        DTD / 实体检测是**编码无关**的（见 ``_contains_forbidden_markup``），
        因此 UTF-16 / UTF-32 无法借"字符间夹 NUL 字节"绕过字节子串检查。所有越界
        统一映射为稳定的 INVALID_RESPONSE，不回显原始 XML。

        使用 Expat 声明事件进行结构化预检，再由有界 TreeBuilder 建树。字节级检测
        仅作为纵深防御，不承担 DTD/实体安全边界。
        """
        if _contains_forbidden_markup(content):
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)
        try:
            _reject_forbidden_xml_constructs(content)
            parser = ElementTree.XMLParser(target=_BoundedTreeBuilder(self._settings))
            root = ElementTree.fromstring(content, parser=parser)
        except (ElementTree.ParseError, expat.ExpatError, _XmlLimitExceeded, ValueError) as exc:
            raise DeviceAdapterError(
                DeviceAdapterErrorKind.INVALID_RESPONSE,
                operation,
                "xml_invalid",
            ) from exc
        return root

    @staticmethod
    def _text(root: ElementTree.Element, local_name: str) -> str | None:
        for element in root.iter():
            element_name = element.tag.rsplit("}", 1)[-1]
            if element_name == local_name and element.text and (value := element.text.strip()):
                return value
        return None

    def _device_information(self) -> dict[str, str | None]:
        root = self._soap(
            "device",
            '<tds:GetDeviceInformation xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>',
            "get_device_information",
        )
        return {
            "manufacturer": self._text(root, "Manufacturer"),
            "model": self._text(root, "Model"),
            "firmware": self._text(root, "FirmwareVersion"),
        }

    @staticmethod
    def _integer(root: ElementTree.Element, local_name: str) -> int | None:
        value = OnvifReadOnlyAdapter._text(root, local_name)
        if value is None:
            return None
        try:
            return int(float(value))
        except ValueError:
            return None

    def _profiles(self) -> tuple[_MediaProfile, ...]:
        root = self._soap(
            "media",
            '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>',
            "get_profiles",
        )
        profiles: list[_MediaProfile] = []
        for element in root.iter():
            if (
                element.tag.rsplit("}", 1)[-1] == "Profiles"
                and (token := element.attrib.get("token"))
            ):
                profiles.append(
                    _MediaProfile(
                        token=token,
                        name=self._text(element, "Name") or token,
                        encoding=self._text(element, "Encoding"),
                        width=self._integer(element, "Width"),
                        height=self._integer(element, "Height"),
                        frame_rate=self._integer(element, "FrameRateLimit"),
                        bitrate_kbps=self._integer(element, "BitrateLimit"),
                    )
                )
        return tuple(profiles)

    @staticmethod
    def _profile(
        profiles: tuple[_MediaProfile, ...], stream_kind: StreamKind
    ) -> _MediaProfile | None:
        keywords = (
            ("profile_main", "main", "primary", "主码流", "高清")
            if stream_kind is StreamKind.MAIN
            else ("profile_sub", "sub", "secondary", "子码流", "辅码流")
        )
        named = [
            profile
            for profile in profiles
            if any(
                keyword in f"{profile.token} {profile.name}".lower()
                for keyword in keywords
            )
        ]
        if named:
            return min(named, key=lambda item: item.token)

        ranked = [profile for profile in profiles if profile.media_rank[0] > 0]
        if not ranked or (stream_kind is StreamKind.SUB and len(ranked) < 2):
            return None
        distinct_ranks = {profile.media_rank for profile in ranked}
        if len(ranked) > 1 and len(distinct_ranks) == 1:
            return None
        chooser = max if stream_kind is StreamKind.MAIN else min
        target_rank = chooser(distinct_ranks)
        matches = [profile for profile in ranked if profile.media_rank == target_rank]
        return min(matches, key=lambda item: item.token)

    def _stream_uri(self, profile_token: str) -> str:
        """取得并约束设备返回的 RTSP URI，返回不含 userinfo 的内存探针目标。"""
        escaped = escape(profile_token)
        root = self._soap(
            "media",
            f"""<trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
              xmlns:tt="http://www.onvif.org/ver10/schema">
              <trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>
              <tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>
              <trt:ProfileToken>{escaped}</trt:ProfileToken>
            </trt:GetStreamUri>""",
            "get_stream_uri",
        )
        uri = self._text(root, "Uri")
        if not uri:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, "get_stream_uri")
        try:
            parsed = urlsplit(uri)
            host = (parsed.hostname or "").lower()
            _port = parsed.port  # 触发非法端口校验；连接端口由受控配置决定。
        except ValueError as exc:
            raise DeviceAdapterError(
                DeviceAdapterErrorKind.INVALID_RESPONSE, "get_stream_uri"
            ) from exc
        allowed_hosts = {item.lower() for item in self._settings.allowed_hosts}
        try:
            private_stream_host = ipaddress.ip_address(host).is_private
        except ValueError:
            private_stream_host = False
        stream_host_allowed = host in allowed_hosts or (
            self._settings.allow_private_stream_uri_host_rewrite and private_stream_host
        )
        if (
            parsed.scheme.lower() != "rtsp"
            or not host
            or not stream_host_allowed
            or not parsed.path.startswith("/")
            or parsed.fragment
        ):
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, "get_stream_uri")
        # 连接目标由受控配置决定，以支持 Docker/NAT 端口映射；设备返回的 host
        # 仍必须在 allowlist 中，而 path/query 必须来自设备实际 Stream URI。
        probe_host = self._settings.rtsp_probe_host.lower()
        probe_port = self._settings.rtsp_probe_port
        netloc = (
            f"[{probe_host}]:{probe_port}"
            if ":" in probe_host
            else f"{probe_host}:{probe_port}"
        )
        return urlunsplit(("rtsp", netloc, parsed.path, parsed.query, ""))

    def _rtsp_diagnostic(self, stream_uri: str) -> RtspDiagnosticResult:
        """执行一次未认证 OPTIONS，并将结果压缩为非敏感类别。"""
        try:
            with socket.create_connection(
                (self._settings.rtsp_probe_host, self._settings.rtsp_probe_port),
                timeout=self._settings.connect_timeout_seconds,
            ) as connection:
                connection.settimeout(self._settings.read_timeout_seconds)
                connection.sendall(
                    (
                        f"OPTIONS {stream_uri} RTSP/1.0\r\n"
                        "CSeq: 1\r\n"
                        "User-Agent: security-diagnosis-harness\r\n\r\n"
                    ).encode("ascii")
                )
                response = connection.recv(512)
                status_line = response.split(b"\r\n", 1)[0]
                parts = status_line.split()
                if len(parts) < 2 or not parts[0].startswith(b"RTSP/") or not parts[1].isdigit():
                    return RtspDiagnosticResult("response_received", "invalid_response")
                status = int(parts[1])
                if 200 <= status < 300:
                    category = "success"
                elif status == 401:
                    category = "authentication_required"
                elif status in {405, 501}:
                    category = "method_unsupported"
                elif 400 <= status < 500:
                    category = "client_error"
                elif 500 <= status < 600:
                    category = "server_error"
                else:
                    category = "other_status"
                scheme = None
                if status == 401:
                    lowered = response.lower()
                    if b"www-authenticate:" in lowered:
                        if b"digest " in lowered:
                            scheme = "digest"
                        elif b"basic " in lowered:
                            scheme = "basic"
                        else:
                            scheme = "other"
                return RtspDiagnosticResult("response_received", category, scheme)
        except TimeoutError:
            return RtspDiagnosticResult("read_or_connect_timeout")
        except ConnectionRefusedError:
            return RtspDiagnosticResult("connection_refused")
        except ConnectionResetError:
            return RtspDiagnosticResult("connection_reset")
        except (OSError, UnicodeEncodeError):
            return RtspDiagnosticResult("network_or_encoding_error")

    def _rtsp_available(self, stream_uri: str) -> bool:
        """对 ONVIF 返回的具体 URI 做一次未认证 OPTIONS，仅 2xx 视为可用。"""
        return self._rtsp_diagnostic(stream_uri).reachable

    @staticmethod
    def _digest_parameters(response: bytes) -> dict[str, str]:
        """从有界 RTSP 响应中提取 Digest challenge，拒绝未知算法与 qop。"""
        normalized_lines = response.replace(b"\r\n", b"\n").split(b"\n")
        header = next(
            (
                line.decode("ascii", errors="ignore")
                for line in normalized_lines
                if line.lower().startswith(b"www-authenticate:")
            ),
            "",
        )
        _, _, value = header.partition(":")
        if not value.strip().lower().startswith("digest "):
            return {}
        parameters = {
            match.group(1).lower(): match.group(2) or match.group(3) or ""
            for match in _DIGEST_PARAMETER.finditer(value)
        }
        algorithm = parameters.get("algorithm", "MD5").upper()
        qop_values = {
            item.strip().lower()
            for item in parameters.get("qop", "").split(",")
            if item.strip()
        }
        if algorithm != "MD5" or (qop_values and "auth" not in qop_values):
            return {}
        if not parameters.get("realm") or not parameters.get("nonce"):
            return {}
        return parameters

    @staticmethod
    def _md5_hex(value: str) -> str:
        return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()

    def _rtsp_digest_diagnostic(self, stream_uri: str) -> RtspDiagnosticResult:
        """执行严格两步 RTSP Digest OPTIONS；不重试、不保留 challenge 或凭证。"""
        username, password = self._credentials()
        try:
            with socket.create_connection(
                (self._settings.rtsp_probe_host, self._settings.rtsp_probe_port),
                timeout=self._settings.connect_timeout_seconds,
            ) as connection:
                connection.settimeout(self._settings.read_timeout_seconds)
                first = (
                    f"OPTIONS {stream_uri} RTSP/1.0\r\n"
                    "CSeq: 1\r\n"
                    "User-Agent: security-diagnosis-harness\r\n\r\n"
                ).encode("ascii")
                connection.sendall(first)
                challenge_response = connection.recv(2048)
                parameters = self._digest_parameters(challenge_response)
                if not parameters:
                    return RtspDiagnosticResult("challenge_received", "invalid_digest_challenge")

                realm = parameters["realm"]
                nonce = parameters["nonce"]
                cnonce = secrets.token_hex(8)
                nc = "00000001"
                ha1 = self._md5_hex(f"{username}:{realm}:{password}")
                ha2 = self._md5_hex(f"OPTIONS:{stream_uri}")
                if parameters.get("qop"):
                    response_digest = self._md5_hex(
                        f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}"
                    )
                    qop_fragment = f", qop=auth, nc={nc}, cnonce=\"{cnonce}\""
                else:
                    response_digest = self._md5_hex(f"{ha1}:{nonce}:{ha2}")
                    qop_fragment = ""
                opaque_fragment = (
                    f', opaque="{parameters["opaque"]}"'
                    if parameters.get("opaque")
                    else ""
                )
                authorization = (
                    f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
                    f'uri="{stream_uri}", response="{response_digest}", algorithm=MD5'
                    f"{opaque_fragment}{qop_fragment}"
                )
                second = (
                    f"OPTIONS {stream_uri} RTSP/1.0\r\n"
                    "CSeq: 2\r\n"
                    "User-Agent: security-diagnosis-harness\r\n"
                    f"Authorization: {authorization}\r\n\r\n"
                ).encode("ascii")
                del ha1, response_digest, authorization
                connection.sendall(second)
                final_response = connection.recv(512)
                status_line = final_response.split(b"\r\n", 1)[0].split()
                if (
                    len(status_line) >= 2
                    and status_line[0].startswith(b"RTSP/")
                    and status_line[1].isdigit()
                ):
                    status = int(status_line[1])
                    if 200 <= status < 300:
                        return RtspDiagnosticResult("authenticated_response", "success", "digest")
                    if status == 401:
                        return RtspDiagnosticResult(
                            "authenticated_response", "authentication_rejected", "digest"
                        )
                    if status in {405, 501}:
                        return RtspDiagnosticResult(
                            "authenticated_response", "method_unsupported", "digest"
                        )
                    return RtspDiagnosticResult(
                        "authenticated_response", "other_status", "digest"
                    )
                return RtspDiagnosticResult(
                    "authenticated_response", "invalid_response", "digest"
                )
        except TimeoutError:
            return RtspDiagnosticResult("read_or_connect_timeout")
        except ConnectionRefusedError:
            return RtspDiagnosticResult("connection_refused")
        except ConnectionResetError:
            return RtspDiagnosticResult("connection_reset")
        except (OSError, UnicodeEncodeError):
            return RtspDiagnosticResult("network_or_encoding_error")
        finally:
            del password

    def query_status(self, device_id: str) -> DeviceSnapshot:
        info = self._device_information()
        return DeviceSnapshot(
            device_id=device_id,
            online=True,
            stream_status=StreamStatus.UNKNOWN,
            recording_status=RecordingStatus.UNKNOWN,
            source=self.adapter_key,
            extra={key: value for key, value in info.items() if value},
        )

    def probe_once(self) -> OnvifSingleProbeResult:
        """按固定顺序执行一次认证、Profile、Stream URI 与 RTSP 探活。

        每个网络阶段至多调用一次，不重试；返回值刻意省略设备标识、地址、完整 URI、
        Profile token 与原始响应，供授权真机验收使用。
        """
        info = self._device_information()
        profiles = self._profiles()
        main = self._profile(profiles, StreamKind.MAIN)
        rtsp_reachable = False
        if main is not None:
            stream_uri = self._stream_uri(main.token)
            try:
                rtsp_reachable = self._rtsp_available(stream_uri)
            finally:
                del stream_uri
        return OnvifSingleProbeResult(
            authenticated=True,
            manufacturer_present=bool(info.get("manufacturer")),
            model_present=bool(info.get("model")),
            firmware_present=bool(info.get("firmware")),
            profile_count=len(profiles),
            main_profile_present=main is not None,
            main_encoding=main.encoding if main else None,
            main_resolution=main.resolution if main else None,
            rtsp_reachable=rtsp_reachable,
        )

    def probe_media_once(self) -> OnvifSingleProbeResult:
        """仅探测 Profiles、主码流 URI 与 RTSP，可用于已完成认证后的授权复验。"""
        profiles = self._profiles()
        main = self._profile(profiles, StreamKind.MAIN)
        rtsp_reachable = False
        if main is not None:
            stream_uri = self._stream_uri(main.token)
            try:
                rtsp_reachable = self._rtsp_available(stream_uri)
            finally:
                del stream_uri
        return OnvifSingleProbeResult(
            authenticated=True,
            manufacturer_present=False,
            model_present=False,
            firmware_present=False,
            profile_count=len(profiles),
            main_profile_present=main is not None,
            main_encoding=main.encoding if main else None,
            main_resolution=main.resolution if main else None,
            rtsp_reachable=rtsp_reachable,
        )

    def probe_media_diagnostic_once(self) -> OnvifMediaDiagnosticResult:
        """查询媒体事实，并对设备给出的主码流 URI 发送一次未认证 OPTIONS。"""
        profiles = self._profiles()
        main = self._profile(profiles, StreamKind.MAIN)
        diagnostic = RtspDiagnosticResult("not_attempted")
        if main is not None:
            stream_uri = self._stream_uri(main.token)
            try:
                diagnostic = self._rtsp_diagnostic(stream_uri)
            finally:
                del stream_uri
        return OnvifMediaDiagnosticResult(
            profile_count=len(profiles),
            main_profile_present=main is not None,
            main_encoding=main.encoding if main else None,
            main_resolution=main.resolution if main else None,
            rtsp=diagnostic,
        )

    def probe_media_digest_once(self) -> OnvifMediaDiagnosticResult:
        """查询媒体事实，并对主码流执行一次严格两步 RTSP Digest OPTIONS。"""
        profiles = self._profiles()
        main = self._profile(profiles, StreamKind.MAIN)
        diagnostic = RtspDiagnosticResult("not_attempted")
        if main is not None:
            stream_uri = self._stream_uri(main.token)
            try:
                diagnostic = self._rtsp_digest_diagnostic(stream_uri)
            finally:
                del stream_uri
        return OnvifMediaDiagnosticResult(
            profile_count=len(profiles),
            main_profile_present=main is not None,
            main_encoding=main.encoding if main else None,
            main_resolution=main.resolution if main else None,
            rtsp=diagnostic,
        )

    def query_channel_snapshot(self, device_id: str) -> ChannelSnapshot:
        profiles = self._profiles()
        return ChannelSnapshot(
            device_id=device_id,
            channel_status=ChannelStatus.ONLINE if profiles else ChannelStatus.UNKNOWN,
            bound=bool(profiles),
            platform_registered=None,
            source=self.adapter_key,
            extra={"profile_count": len(profiles)},
        )

    def query_stream_snapshot(
        self, device_id: str, stream_kind: StreamKind = StreamKind.MAIN
    ) -> StreamSnapshot:
        profile = self._profile(self._profiles(), stream_kind)
        if profile is None:
            return StreamSnapshot(
                device_id=device_id,
                stream_kind=stream_kind,
                pull_status=PullStatus.FAILED,
                error_code="PROFILE_NOT_FOUND",
                source=self.adapter_key,
            )
        stream_uri = self._stream_uri(profile.token)
        available = self._rtsp_available(stream_uri)
        return StreamSnapshot(
            device_id=device_id,
            stream_kind=stream_kind,
            pull_status=PullStatus.SUCCESS if available else PullStatus.FAILED,
            encoding=profile.encoding,
            resolution=profile.resolution,
            frame_rate=profile.frame_rate,
            bitrate_kbps=profile.bitrate_kbps,
            error_code=None if available else "RTSP_UNREACHABLE",
            source=self.adapter_key,
        )

    def read_config_snapshot(self, device_id: str) -> DeviceConfigSnapshot:
        info = self._device_information()
        return DeviceConfigSnapshot(
            device_id=device_id,
            enabled=True,
            config=info,
            source=self.adapter_key,
        )

    @staticmethod
    def _unsupported(operation: str) -> Any:
        raise DeviceAdapterError(DeviceAdapterErrorKind.UNSUPPORTED_CAPABILITY, operation)

    def query_platform_pull_status(self, device_id: str) -> Any:
        return self._unsupported("query_platform_pull_status")

    def search_alarm_events(
        self, device_id: str, keyword: str | None = None, limit: int = 10
    ) -> Any:
        return self._unsupported("search_alarm_events")

    def query_recording_plan(self, device_id: str, channel_id: str) -> Any:
        return self._unsupported("query_recording_plan")

    def query_storage_status(self, device_id: str, channel_id: str) -> Any:
        return self._unsupported("query_storage_status")

    def check_recording_playback(self, device_id: str, channel_id: str, start_at, end_at) -> Any:
        return self._unsupported("check_recording_playback")

    def query_access_controller(self, device_id: str) -> Any:
        return self._unsupported("query_access_controller")

    def query_door(self, device_id: str, door_id: str) -> Any:
        return self._unsupported("query_door")

    def query_credential(self, credential_id: str) -> Any:
        return self._unsupported("query_credential")

    def query_access_policy(self, person_id: str, door_id: str) -> Any:
        return self._unsupported("query_access_policy")

    def search_access_events(
        self, device_id: str, door_id: str, credential_id: str, limit: int = 10
    ) -> Any:
        return self._unsupported("search_access_events")

    def query_alarm_rule(self, device_id: str, rule_id: str) -> Any:
        return self._unsupported("query_alarm_rule")

    def query_alarm_signal(self, device_id: str, alarm_id: str) -> Any:
        return self._unsupported("query_alarm_signal")

    def query_alarm_environment(self, device_id: str, alarm_id: str) -> Any:
        return self._unsupported("query_alarm_environment")

    def query_alarm_verification(self, device_id: str, alarm_id: str) -> Any:
        return self._unsupported("query_alarm_verification")

    def query_alarm_correlation(self, device_id: str, alarm_id: str) -> Any:
        return self._unsupported("query_alarm_correlation")
