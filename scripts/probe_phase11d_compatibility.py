"""Phase 11D 单次发现 + HTTP Digest 只读兼容复验。"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from security_diagnosis_harness.adapters.device_gateway.onvif import (  # noqa: E402
    OnvifAuthenticationMode,
    OnvifReadOnlyAdapter,
    OnvifReadOnlySettings,
)
from security_diagnosis_harness.adapters.device_gateway.onvif_discovery import (  # noqa: E402
    extract_xaddrs,
    summarize_xaddr,
)
from security_diagnosis_harness.domain.device_integration import (  # noqa: E402
    DeviceAdapterError,
    ResolvedCredential,
)


class _EnvironmentCredentialResolver:
    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password

    def resolve(self, credential_reference: str) -> ResolvedCredential:
        if credential_reference != "phase11d:process-env":
            raise ValueError("unexpected credential reference")
        if not self._username or not self._password:
            raise ValueError("missing process-local ONVIF credential")
        return ResolvedCredential(value=SecretStr(f"{self._username}:{self._password}"))


def _discover_once(expected_host: str | None) -> tuple[str, dict[str, object]]:
    message = f'''<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <e:Header><w:MessageID>uuid:{uuid.uuid4()}</w:MessageID>
 <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
 <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>
 <e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>
</e:Envelope>'''.encode()
    matches: list[str] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as client:
        client.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        client.settimeout(3.0)
        client.sendto(message, ("239.255.255.250", 3702))
        while True:
            try:
                content, _address = client.recvfrom(65_536)
            except TimeoutError:
                break
            for value in extract_xaddrs(content):
                if (
                    (expected_host is None or urlsplit(value).hostname == expected_host)
                    and value not in matches
                ):
                    matches.append(value)
            del content, _address
    if len(matches) != 1:
        raise ValueError("expected exactly one authorized device XAddr")
    endpoint = matches[0]
    return endpoint, asdict(summarize_xaddr(endpoint))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth-only", action="store_true")
    parser.add_argument("--media-only", action="store_true")
    parser.add_argument("--rtsp-digest", action="store_true")
    arguments = parser.parse_args()
    selected_modes = sum((arguments.auth_only, arguments.media_only, arguments.rtsp_digest))
    if selected_modes > 1:
        parser.error("probe modes are mutually exclusive")
    started = perf_counter()
    report: dict[str, object] = {
        "report_kind": "authorized_device_e2e",
        "probe_scope": (
            "onvif_rtsp_digest"
            if arguments.rtsp_digest
            else (
                "onvif_digest_media"
                if arguments.media_only
                else (
                    "onvif_digest_authentication"
                    if arguments.auth_only
                    else "onvif_digest_compatibility"
                )
            )
        ),
        "automatic_retry": False,
        "write_operation_performed": False,
        "external_model_called": False,
        "llm_call_count": 0,
        "input_tokens": None,
        "output_tokens": None,
        "authentication_mode": "http_digest",
        "maximum_network_exchanges": (
            6
            if arguments.rtsp_digest
            else (5 if arguments.media_only else (3 if arguments.auth_only else 8))
        ),
        "device_address_persisted": False,
        "complete_xaddr_persisted": False,
    }
    try:
        configured_host = os.environ.get("SECURITY_DIAGNOSIS_DEVICE_HOST", "").strip()
        username = os.environ.get("SECURITY_DIAGNOSIS_DEVICE_USERNAME", "admin").strip()
        password = os.environ.get("SECURITY_DIAGNOSIS_DEVICE_PASSWORD")
        if password is None:
            password = getpass.getpass("请输入摄像头本地/ONVIF密码（输入隐藏）: ")
        if not username or not password:
            raise ValueError("missing process-local ONVIF credential")
        if arguments.media_only or arguments.rtsp_digest:
            if not configured_host:
                raise ValueError("media-only probe requires an existing authorized host")
            endpoint = f"http://{configured_host}/onvif/device_service"
            shape = asdict(summarize_xaddr(endpoint))
        else:
            endpoint, shape = _discover_once(configured_host or None)
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        if not host:
            raise ValueError("discovered XAddr has no host")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        rtsp_port = int(os.environ.get("SECURITY_DIAGNOSIS_DEVICE_RTSP_PORT", "554"))
        report["xaddr_shape"] = shape
        settings = OnvifReadOnlySettings(
            base_url=f"{parsed.scheme}://{host}:{port}",
            allowed_hosts=frozenset({host}),
            credential_reference="phase11d:process-env",
            rtsp_probe_host=host,
            rtsp_probe_port=rtsp_port,
            allow_private_http=True,
            authentication_mode=OnvifAuthenticationMode.HTTP_DIGEST,
            max_http_exchanges_per_operation=2,
            device_service_path=parsed.path,
        )
        resolver = _EnvironmentCredentialResolver(username, password)
        del username, password
        with OnvifReadOnlyAdapter(settings, resolver) as adapter:
            if arguments.auth_only:
                snapshot = adapter.query_status("authorized-device")
                report.update(
                    {
                        "completed": True,
                        "failure_kind": None,
                        "authenticated": True,
                        "manufacturer_present": bool(snapshot.extra.get("manufacturer")),
                        "model_present": bool(snapshot.extra.get("model")),
                        "firmware_present": bool(snapshot.extra.get("firmware")),
                    }
                )
            elif arguments.media_only or arguments.rtsp_digest:
                result = (
                    adapter.probe_media_digest_once()
                    if arguments.rtsp_digest
                    else adapter.probe_media_diagnostic_once()
                )
                report.update(
                    {
                        "completed": True,
                        "failure_kind": None,
                        "profile_count": result.profile_count,
                        "main_profile_present": result.main_profile_present,
                        "main_encoding": result.main_encoding,
                        "main_resolution": result.main_resolution,
                        "rtsp_connection_stage": result.rtsp.connection_stage,
                        "rtsp_status_category": result.rtsp.status_category,
                        "rtsp_authentication_scheme": result.rtsp.authentication_scheme,
                        "rtsp_reachable": result.rtsp.reachable,
                    }
                )
            else:
                result = adapter.probe_once()
                report.update({"completed": True, "failure_kind": None, **result.__dict__})
    except DeviceAdapterError as exc:
        report.update(
            {
                "completed": False,
                "failure_kind": exc.kind.value,
                "failure_operation": exc.operation,
                "diagnostic_code": exc.diagnostic_code,
            }
        )
    except (OSError, TypeError, ValueError):
        report.update({"completed": False, "failure_kind": "configuration_error"})
    report["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    output = ROOT / "demo-output" / "phase11d-authorized-device-probe.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.get("completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
