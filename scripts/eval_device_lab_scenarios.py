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

import httpx

from security_diagnosis_harness.adapters.device_assets.in_memory import (
    InMemoryDeviceAssetCatalog,
)
from security_diagnosis_harness.adapters.device_gateway.cross_source import (
    CrossSourceCameraGateway,
)
from security_diagnosis_harness.adapters.device_gateway.http_security_platform import (
    SecurityPlatformHttpAdapter,
    SecurityPlatformHttpSettings,
)
from security_diagnosis_harness.adapters.device_gateway.onvif import (
    OnvifReadOnlyAdapter,
    OnvifReadOnlySettings,
)
from security_diagnosis_harness.adapters.device_gateway.registry import (
    InMemoryDeviceAdapterRegistry,
)
from security_diagnosis_harness.adapters.device_gateway.routed import RoutedDeviceGateway
from security_diagnosis_harness.adapters.llm.fake import FakeLLM
from security_diagnosis_harness.agent.runner import ToolLoopBudget, ToolLoopRunner
from security_diagnosis_harness.application.diagnoses import SecurityDiagnosisApplicationService
from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository
from security_diagnosis_harness.domain.camera import PullStatus, StreamKind
from security_diagnosis_harness.domain.citation_policy import CitationPolicy
from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
    DeviceAsset,
    DeviceCapability,
    ResolvedCredential,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.ports.llm import (
    ChatRole,
    ConclusionDraft,
    FinishReason,
    LLMRequest,
    LLMResponse,
    ToolCall,
)
from security_diagnosis_harness.tools.device_channel import DeviceChannelTool
from security_diagnosis_harness.tools.device_status import DeviceStatusTool
from security_diagnosis_harness.tools.device_stream import DeviceStreamTool
from security_diagnosis_harness.tools.platform_pull import PlatformPullStatusTool
from security_diagnosis_harness.tools.registry import ToolRegistry

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
    def __init__(self, value: str, reference: str = "device-lab/runtime") -> None:
        self._value = value
        self._reference = reference

    def resolve(self, credential_reference: str) -> ResolvedCredential:
        if credential_reference != self._reference:
            raise ValueError("未知凭证引用")
        return ResolvedCredential(value=self._value)


def _onvif_adapter(password: str) -> OnvifReadOnlyAdapter:
    return OnvifReadOnlyAdapter(
        OnvifReadOnlySettings(
            base_url="http://127.0.0.1:28080",
            allowed_hosts={"127.0.0.1"},
            credential_reference="device-lab/onvif",
            rtsp_probe_host="127.0.0.1",
            rtsp_probe_port=28554,
            connect_timeout_seconds=1,
            read_timeout_seconds=2,
        ),
        _Resolver(f"device-lab:{password}", "device-lab/onvif"),
    )


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


def _evaluate_agent_loop(
    control: httpx.Client,
    contract_credential: str,
    onvif_password: str,
) -> list[dict[str, object]]:
    """让四个核心案例进入正式 Runner/Registry/Gateway/Evidence/Citation 链路。"""
    onvif = _onvif_adapter(onvif_password)
    platform = _adapter(contract_credential)
    device_ids = (
        "lab-rtsp-unreachable",
        "lab-platform-pull-failed",
        "lab-sub-stream-missing",
        "lab-black-video",
    )
    composite = CrossSourceCameraGateway(
        onvif,
        platform,
        content_probe=lambda: _is_black_rtsp_stream("device-lab", onvif_password),
        content_probe_device_ids=frozenset({"lab-black-video"}),
    )
    assets = InMemoryDeviceAssetCatalog(
        DeviceAsset(
            device_id=device_id,
            device_type="camera",
            adapter_key=composite.adapter_key,
            capabilities=frozenset(
                {
                    DeviceCapability.STATUS,
                    DeviceCapability.CHANNEL,
                    DeviceCapability.STREAM,
                }
            ),
        )
        for device_id in device_ids
    )
    adapters = InMemoryDeviceAdapterRegistry()
    adapters.register(composite.adapter_key, composite, ready=True)
    gateway = RoutedDeviceGateway(assets, adapters)
    tools = ToolRegistry()
    for tool_type in (
        DeviceStatusTool,
        DeviceChannelTool,
        DeviceStreamTool,
        PlatformPullStatusTool,
    ):
        tools.register(tool_type())

    expected = {
        "lab-rtsp-unreachable": "stream_publish_or_encoder_issue",
        "lab-platform-pull-failed": "platform_pull_or_access_path_issue",
        "lab-sub-stream-missing": "stream_publish_or_encoder_issue",
        "lab-black-video": "video_content_black_or_obstructed",
    }

    def responder(request: LLMRequest) -> LLMResponse:
        device_id = str(request.metadata.get("device_id", ""))
        if not any(message.role is ChatRole.TOOL for message in request.messages):
            calls = [
                ToolCall(
                    call_id="status",
                    tool_name="device__query_status",
                    arguments={"device_id": device_id},
                ),
                ToolCall(
                    call_id="channel",
                    tool_name="device__query_channel",
                    arguments={"device_id": device_id},
                ),
                ToolCall(
                    call_id="main",
                    tool_name="device__query_stream",
                    arguments={"device_id": device_id, "stream_kind": "main"},
                ),
                ToolCall(
                    call_id="platform",
                    tool_name="platform__query_pull_status",
                    arguments={"device_id": device_id},
                ),
            ]
            if device_id == "lab-sub-stream-missing":
                calls.append(
                    ToolCall(
                        call_id="sub",
                        tool_name="device__query_stream",
                        arguments={"device_id": device_id, "stream_kind": "sub"},
                    )
                )
            return LLMResponse(tool_calls=calls, finish_reason=FinishReason.TOOL_CALLS)
        return LLMResponse(
            final_conclusion=ConclusionDraft(
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                summary="根据受控设备、码流和平台事实生成候选结论",
                confidence="probable",
                cited_evidence_ids=[],
                next_steps=["由人工核对跨来源证据后确认"],
            ),
            finish_reason=FinishReason.STOP,
        )

    llm = FakeLLM(responder=responder)
    runner = ToolLoopRunner(llm, tools, ToolLoopBudget(max_rounds=3, max_tool_calls=6))
    repository = InMemoryDiagnosisRepository()
    service = SecurityDiagnosisApplicationService(
        repository=repository,
        runner=runner,
        registry=tools,
        gateway=gateway,
        citation_policy=CitationPolicy(),
        tool_allowlist=[
            "device__query_status",
            "device__query_channel",
            "device__query_stream",
            "platform__query_pull_status",
        ],
        supported_fault_types=frozenset({SecurityFaultType.CAMERA_BLACK_SCREEN}),
    )
    outcomes: list[dict[str, object]] = []
    try:
        for device_id in device_ids:
            # 故障只在当前案例采集期间生效；案例结束立即恢复，避免污染后续案例。
            _set_proxy_enabled(
                control,
                _ONVIF_RTSP_PROXY,
                device_id != "lab-rtsp-unreachable",
            )
            case = service.create_diagnosis(
                device_id,
                SecurityFaultType.CAMERA_BLACK_SCREEN,
                reporter="device-lab",
                description="Device Lab 跨来源影子诊断",
            )
            result = service.run_diagnosis(case.diagnosis_id)
            label = result.candidate_label.value if result.candidate_label else None
            saved = service.get_diagnosis(case.diagnosis_id)
            outcomes.append(
                {
                    "device_alias": device_id,
                    "candidate_label": label,
                    "expected_label": expected[device_id],
                    "evidence_count": len(saved.evidence),
                    "citation_count": len(saved.conclusion.cited_evidence_ids)
                    if saved.conclusion
                    else 0,
                    "status": saved.status.value,
                    "passed": label == expected[device_id]
                    and len(saved.evidence) >= 3
                    and saved.conclusion is not None
                    and len(saved.conclusion.cited_evidence_ids) >= 2
                    and saved.status.value == "waiting_for_confirmation",
                }
            )
    finally:
        _set_proxy_enabled(control, _ONVIF_RTSP_PROXY, True)
        onvif.close()
        platform.close()
    return outcomes


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
        with _onvif_adapter(onvif_password) as onvif:
            device = onvif.query_status("lab-camera")
            main = onvif.query_stream_snapshot("lab-camera", StreamKind.MAIN)
        results.append(
            {
                "scenario": "onvif_ok_rtsp_unreachable",
                "passed": device.online
                and main.pull_status is PullStatus.FAILED
                and main.error_code == "RTSP_UNREACHABLE"
                and _rtsp_responds(28555)
                and not _rtsp_responds(28554),
            }
        )
        _set_proxy_enabled(control, _ONVIF_RTSP_PROXY, True)

        # 2. 错误密码被拒，正确的一次性密码可用。
        authentication_rejected = False
        try:
            with _onvif_adapter("deliberately-wrong") as onvif:
                onvif.query_status("lab-camera")
        except DeviceAdapterError as exc:
            authentication_rejected = exc.kind is DeviceAdapterErrorKind.AUTHENTICATION
        with _onvif_adapter(onvif_password) as onvif:
            correct_credential_accepted = onvif.query_status("lab-camera").online
        results.append(
            {
                "scenario": "onvif_authentication_failed",
                "passed": authentication_rejected and correct_credential_accepted,
            }
        )

        # 3. 只有可用 main Profile，明确缺少 sub Profile。
        with _onvif_adapter(onvif_password) as onvif:
            main = onvif.query_stream_snapshot("lab-camera", StreamKind.MAIN)
            sub = onvif.query_stream_snapshot("lab-camera", StreamKind.SUB)
        results.append(
            {
                "scenario": "main_stream_ok_sub_stream_missing",
                "passed": main.pull_status is PullStatus.SUCCESS
                and sub.pull_status is PullStatus.FAILED
                and sub.error_code == "PROFILE_NOT_FOUND",
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
        agent_loop = _evaluate_agent_loop(
            control,
            contract_credential,
            onvif_password,
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
        "agent_loop_total": len(agent_loop),
        "agent_loop_passed": sum(bool(item["passed"]) for item in agent_loop),
        "agent_loop_failed": [
            item["device_alias"] for item in agent_loop if not item["passed"]
        ],
        "agent_loop": agent_loop,
        "external_model_called": False,
        "real_device_accessed": False,
    }
    (_LAB_RUNTIME / "scenario-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if (
        report["passed"] != report["total"]
        or report["agent_loop_passed"] != report["agent_loop_total"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
