"""严格只读的 ONVIF Device/Media Adapter。"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from xml.etree import ElementTree
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

__all__ = ["OnvifReadOnlyAdapter", "OnvifReadOnlySettings"]


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
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ValueError("ONVIF base_url 只允许 HTTPS 或本机 HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("ONVIF base_url 禁止 userinfo、查询参数和 fragment")
        if host not in allowed or self.rtsp_probe_host.lower() not in allowed:
            raise ValueError("ONVIF/RTSP host 不在 allowlist")
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
        envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Header>{self._security_header(username, password)}</s:Header>
          <s:Body>{body}</s:Body>
        </s:Envelope>"""
        del username, password
        try:
            with self._client.stream(
                "POST",
                f"/onvif/{quote(service, safe='')}_service",
                content=envelope.encode(),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            ) as response:
                if response.status_code in {401, 403}:
                    raise DeviceAdapterError(DeviceAdapterErrorKind.AUTHENTICATION, operation)
                if response.status_code >= 500:
                    raise DeviceAdapterError(DeviceAdapterErrorKind.UNAVAILABLE, operation)
                if response.status_code >= 400 or response.is_redirect:
                    raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)
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
            del envelope
        root = self._parse_xml(content, operation)
        if any(element.tag.endswith("Fault") for element in root.iter()):
            raise DeviceAdapterError(DeviceAdapterErrorKind.AUTHENTICATION, operation)
        return root

    def _parse_xml(self, content: bytes, operation: str) -> ElementTree.Element:
        """解析受限 SOAP XML；拒绝 DTD/实体并限制树规模、深度、属性和文本。

        DTD / 实体检测是**编码无关**的（见 ``_contains_forbidden_markup``），
        因此 UTF-16 / UTF-32 无法借"字符间夹 NUL 字节"绕过字节子串检查。所有越界
        统一映射为稳定的 INVALID_RESPONSE，不回显原始 XML。

        仅使用标准库（ElementTree + 有界 TreeBuilder + 字节级检测）：不引入
        defusedxml 等依赖，因为"禁止 DTD/实体 + 有界建树"已由标准库覆盖，且解析器
        不接触网络或本地文件（expat 默认不加载外部实体）。
        """
        if _contains_forbidden_markup(content):
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)
        try:
            parser = ElementTree.XMLParser(target=_BoundedTreeBuilder(self._settings))
            root = ElementTree.fromstring(content, parser=parser)
        except (ElementTree.ParseError, _XmlLimitExceeded, ValueError) as exc:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation) from exc
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
        if (
            parsed.scheme.lower() != "rtsp"
            or not host
            or host not in allowed_hosts
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

    def _rtsp_available(self, stream_uri: str) -> bool:
        """对 ONVIF 返回的具体 URI 做只读 OPTIONS 探针，仅 2xx 视为可用。"""
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
                status_line = connection.recv(512).split(b"\r\n", 1)[0]
                parts = status_line.split()
                return (
                    len(parts) >= 2
                    and parts[0].startswith(b"RTSP/")
                    and parts[1].isdigit()
                    and 200 <= int(parts[1]) < 300
                )
        except (OSError, UnicodeEncodeError):
            return False

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
