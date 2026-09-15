"""运行 Device Lab 八个真实故障场景；不访问外部网络或真实设备。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import httpx

from security_diagnosis_harness.adapters.device_gateway.http_security_platform import (
    SecurityPlatformHttpAdapter,
    SecurityPlatformHttpSettings,
)
from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
    ResolvedCredential,
)

_ROOT = Path(__file__).resolve().parents[1]
_LAB_RUNTIME = _ROOT / ".device-lab" / "runtime"
_CONTROL_URL = "http://127.0.0.1:28474"
_CONTRACT_PROXY = "security-platform-contract"
_ONVIF_RTSP_PROXY = "onvif-rtsp"
_FFMPEG_IMAGE = (
    "jrottenberg/ffmpeg:7.1-alpine@"
    "sha256:8ec1ee1f6a0fcd37c97725827b6b7832795c9596e3439b8da56d7700d61ae778"
)


class _Resolver:
    def __init__(self, value: str) -> None:
        self._value = value

    def resolve(self, credential_reference: str) -> ResolvedCredential:
        if credential_reference != "device-lab/runtime":
            raise ValueError("未知凭证引用")
        return ResolvedCredential(value=self._value)


def _adapter(credential: str, timeout: float = 2.0) -> SecurityPlatformHttpAdapter:
    return SecurityPlatformHttpAdapter(
        SecurityPlatformHttpSettings(
            base_url="http://127.0.0.1:28081",
            allowed_hosts={"127.0.0.1"},
            credential_reference="device-lab/runtime",
            connect_timeout_seconds=timeout,
            read_timeout_seconds=timeout,
            total_timeout_seconds=timeout,
        ),
        _Resolver(credential),
    )


def _set_proxy_enabled(control: httpx.Client, name: str, enabled: bool) -> None:
    current = control.get(f"/proxies/{name}").json()
    current["enabled"] = enabled
    response = control.post(f"/proxies/{name}", json=current)
    response.raise_for_status()


def _add_toxic(
    control: httpx.Client,
    *,
    proxy: str,
    name: str,
    toxic_type: str,
    stream: str,
    attributes: dict[str, int],
) -> None:
    _remove_toxic(control, proxy, name)
    response = control.post(
        f"/proxies/{proxy}/toxics",
        json={
            "name": name,
            "type": toxic_type,
            "stream": stream,
            "toxicity": 1.0,
            "attributes": attributes,
        },
    )
    response.raise_for_status()


def _remove_toxic(control: httpx.Client, proxy: str, name: str) -> None:
    response = control.delete(f"/proxies/{proxy}/toxics/{name}")
    if response.status_code not in {204, 404}:
        response.raise_for_status()


def _rtsp_responds(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            connection.sendall(
                b"OPTIONS rtsp://127.0.0.1/profile_main RTSP/1.0\r\nCSeq: 1\r\n\r\n"
            )
            return connection.recv(512).startswith(b"RTSP/1.0")
    except OSError:
        return False


def _username_token(username: str, password: str) -> str:
    nonce = secrets.token_bytes(16)
    created = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()  # noqa: S324
    ).decode()
    return f"""
      <wsse:Security s:mustUnderstand="1"
        xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
        xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
        <wsse:UsernameToken>
          <wsse:Username>{username}</wsse:Username>
          <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>
          <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce>
          <wsu:Created>{created}</wsu:Created>
        </wsse:UsernameToken>
      </wsse:Security>"""


def _soap_request(
    port: int,
    body: str,
    *,
    username: str,
    password: str,
    service: str = "device",
) -> httpx.Response:
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
    <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
      <s:Header>{_username_token(username, password)}</s:Header>
      <s:Body>{body}</s:Body>
    </s:Envelope>"""
    return httpx.post(
        f"http://127.0.0.1:{port}/onvif/{service}_service",
        content=envelope.encode(),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
        timeout=5,
    )


def _is_valid_onvif_auth(password: str) -> bool:
    response = _soap_request(
        28080,
        '<tds:GetDeviceInformation xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>',
        username="device-lab",
        password=password,
    )
    return response.status_code == 200 and "SimCam-Lab" in response.text


def _profile_tokens(password: str) -> set[str]:
    response = _soap_request(
        28080,
        '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>',
        username="device-lab",
        password=password,
        service="media",
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    return {
        element.attrib["token"]
        for element in root.iter()
        if element.tag.endswith("Profiles") and "token" in element.attrib
    }


def _is_black_rtsp_stream(username: str, password: str) -> bool:
    url = f"rtsp://{username}:{password}@host.docker.internal:28554/profile_main"
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            _FFMPEG_IMAGE,
            "-rtsp_transport",
            "tcp",
            "-i",
            url,
            "-t",
            "2",
            "-vf",
            "blackdetect=d=0.5:pix_th=0.02",
            "-an",
            "-f",
            "null",
            "-",
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    return completed.returncode == 0 and "black_start:" in combined


def main() -> None:
    contract_credential = os.environ.get("SECURITY_DIAGNOSIS_LAB_CREDENTIAL")
    if not contract_credential:
        raise SystemExit("缺少 SECURITY_DIAGNOSIS_LAB_CREDENTIAL")
    onvif_password = (_LAB_RUNTIME / "onvif-credential.txt").read_text().strip()
    control = httpx.Client(base_url=_CONTROL_URL, timeout=5)
    results: list[dict[str, object]] = []
    try:
        # 1. ONVIF 正常，但经代理的 RTSP 被关闭。
        _set_proxy_enabled(control, _ONVIF_RTSP_PROXY, False)
        results.append(
            {
                "scenario": "onvif_ok_rtsp_unreachable",
                "passed": _is_valid_onvif_auth(onvif_password)
                and _rtsp_responds(28555)
                and not _rtsp_responds(28554),
            }
        )
        _set_proxy_enabled(control, _ONVIF_RTSP_PROXY, True)

        # 2. 错误密码被拒，正确的一次性密码可用。
        results.append(
            {
                "scenario": "onvif_authentication_failed",
                "passed": not _is_valid_onvif_auth("deliberately-wrong")
                and _is_valid_onvif_auth(onvif_password),
            }
        )

        # 3. 只有可用 main Profile，明确缺少 sub Profile。
        tokens = _profile_tokens(onvif_password)
        results.append(
            {
                "scenario": "main_stream_ok_sub_stream_missing",
                "passed": "profile_main" in tokens
                and "profile_sub" not in tokens
                and _rtsp_responds(28554),
            }
        )

        # 4. 设备在线且码流正常，但平台拉流事实失败。
        with _adapter(contract_credential) as adapter:
            status = adapter.query_status("cam-platform-pull-01")
            pull = adapter.query_platform_pull_status("cam-platform-pull-01")
        results.append(
            {
                "scenario": "device_online_platform_pull_failed",
                "passed": status.online and pull.pull_status.value == "failed",
            }
        )

        # 5. 固定交替开关 latency，得到可复现的间歇性 2 成功 + 2 超时。
        intermittent: list[str] = []
        for delayed in (False, True, False, True):
            if delayed:
                _add_toxic(
                    control,
                    proxy=_CONTRACT_PROXY,
                    name="intermittent-latency",
                    toxic_type="latency",
                    stream="downstream",
                    attributes={"latency": 800, "jitter": 0},
                )
            else:
                _remove_toxic(control, _CONTRACT_PROXY, "intermittent-latency")
            try:
                with _adapter(contract_credential, timeout=0.15) as adapter:
                    adapter.query_status("cam-offline-01")
                intermittent.append("ok")
            except DeviceAdapterError as exc:
                intermittent.append(exc.kind.value)
        _remove_toxic(control, _CONTRACT_PROXY, "intermittent-latency")
        results.append(
            {
                "scenario": "wifi_intermittent_high_latency",
                "passed": intermittent == ["ok", "timeout", "ok", "timeout"],
                "observations": intermittent,
            }
        )

        # 6. 对端在响应链路重置连接，Adapter 必须稳定归类为 unavailable。
        _add_toxic(
            control,
            proxy=_CONTRACT_PROXY,
            name="connection-reset",
            toxic_type="reset_peer",
            stream="downstream",
            attributes={"timeout": 0},
        )
        reset_kind: str | None = None
        try:
            with _adapter(contract_credential) as adapter:
                adapter.query_status("cam-offline-01")
        except DeviceAdapterError as exc:
            reset_kind = exc.kind.value
        _remove_toxic(control, _CONTRACT_PROXY, "connection-reset")
        results.append(
            {
                "scenario": "connection_reset_during_request",
                "passed": reset_kind == DeviceAdapterErrorKind.UNAVAILABLE.value,
                "classified_as": reset_kind,
            }
        )

        # 7. 服务仅返回在线事实；缺失字段保留为 unknown，而不是伪造确定值。
        with _adapter(contract_credential) as adapter:
            partial = adapter.query_status("lab-partial-facts")
        results.append(
            {
                "scenario": "platform_partial_facts",
                "passed": partial.online
                and partial.channel_online is None
                and partial.stream_status.value == "unknown"
                and partial.recording_status.value == "unknown",
            }
        )

        # 8. ONVIF/RTSP 正常，但实际抽取帧由 blackdetect 判定为持续纯黑。
        results.append(
            {
                "scenario": "black_video_with_healthy_protocol_and_stream",
                "passed": _is_valid_onvif_auth(onvif_password)
                and _rtsp_responds(28554)
                and _is_black_rtsp_stream("device-lab", onvif_password),
            }
        )
    finally:
        for proxy, toxic in (
            (_CONTRACT_PROXY, "intermittent-latency"),
            (_CONTRACT_PROXY, "connection-reset"),
        ):
            _remove_toxic(control, proxy, toxic)
        _set_proxy_enabled(control, _ONVIF_RTSP_PROXY, True)
        control.close()

    report = {
        "total": len(results),
        "passed": sum(bool(item["passed"]) for item in results),
        "failed": [item["scenario"] for item in results if not item["passed"]],
        "scenarios": results,
        "external_model_called": False,
        "real_device_accessed": False,
    }
    (_LAB_RUNTIME / "scenario-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
