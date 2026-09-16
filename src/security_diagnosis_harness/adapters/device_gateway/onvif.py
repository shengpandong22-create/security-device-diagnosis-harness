"""严格只读的 ONVIF Device/Media Adapter。"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
            response = self._client.post(
                f"/onvif/{quote(service, safe='')}_service",
                content=envelope.encode(),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            )
        except httpx.TimeoutException as exc:
            raise DeviceAdapterError(DeviceAdapterErrorKind.TIMEOUT, operation) from exc
        except httpx.RequestError as exc:
            raise DeviceAdapterError(DeviceAdapterErrorKind.UNAVAILABLE, operation) from exc
        finally:
            del envelope
        if response.status_code in {401, 403}:
            raise DeviceAdapterError(DeviceAdapterErrorKind.AUTHENTICATION, operation)
        if response.status_code >= 500:
            raise DeviceAdapterError(DeviceAdapterErrorKind.UNAVAILABLE, operation)
        if response.status_code >= 400 or response.is_redirect:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)
        if len(response.content) > self._settings.max_response_bytes:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation)
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            raise DeviceAdapterError(DeviceAdapterErrorKind.INVALID_RESPONSE, operation) from exc
        if any(element.tag.endswith("Fault") for element in root.iter()):
            raise DeviceAdapterError(DeviceAdapterErrorKind.AUTHENTICATION, operation)
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

    def _profiles(self) -> dict[str, str]:
        root = self._soap(
            "media",
            '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>',
            "get_profiles",
        )
        profiles: dict[str, str] = {}
        for element in root.iter():
            if (
                element.tag.rsplit("}", 1)[-1] == "Profiles"
                and (token := element.attrib.get("token"))
            ):
                name = self._text(element, "Name") or token
                profiles[token] = name
        return profiles

    @staticmethod
    def _profile_token(profiles: dict[str, str], stream_kind: StreamKind) -> str | None:
        candidates = (
            ("profile_main", "main")
            if stream_kind is StreamKind.MAIN
            else ("profile_sub", "sub")
        )
        for token, name in profiles.items():
            lowered = f"{token} {name}".lower()
            if any(candidate in lowered for candidate in candidates):
                return token
        return None

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
        token = self._profile_token(self._profiles(), stream_kind)
        if token is None:
            return StreamSnapshot(
                device_id=device_id,
                stream_kind=stream_kind,
                pull_status=PullStatus.FAILED,
                error_code="PROFILE_NOT_FOUND",
                source=self.adapter_key,
            )
        stream_uri = self._stream_uri(token)
        available = self._rtsp_available(stream_uri)
        return StreamSnapshot(
            device_id=device_id,
            stream_kind=stream_kind,
            pull_status=PullStatus.SUCCESS if available else PullStatus.FAILED,
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
