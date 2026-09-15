"""验证契约服务经 Toxiproxy 的正常、超时与恢复路径。"""

from __future__ import annotations

import json
import os

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

_CREDENTIAL_ENV = "SECURITY_DIAGNOSIS_LAB_CREDENTIAL"
_TOXIPROXY_API = "http://127.0.0.1:28474"
_PROXY_NAME = "security-platform-contract"
_TOXIC_NAME = "device-lab-forced-latency"


class _LabCredentialResolver:
    def __init__(self, value: str) -> None:
        self._value = value

    def resolve(self, credential_reference: str) -> ResolvedCredential:
        if credential_reference != "device-lab/runtime":
            raise ValueError("未知的 Device Lab 凭证引用")
        return ResolvedCredential(value=self._value)


def _settings(*, timeout: float) -> SecurityPlatformHttpSettings:
    return SecurityPlatformHttpSettings(
        base_url="http://127.0.0.1:28081",
        allowed_hosts={"127.0.0.1"},
        credential_reference="device-lab/runtime",
        connect_timeout_seconds=timeout,
        read_timeout_seconds=timeout,
        total_timeout_seconds=timeout,
    )


def _remove_toxic(client: httpx.Client) -> None:
    response = client.delete(f"/proxies/{_PROXY_NAME}/toxics/{_TOXIC_NAME}")
    if response.status_code not in {204, 404}:
        response.raise_for_status()


def main() -> None:
    credential = os.environ.get(_CREDENTIAL_ENV)
    if not credential:
        raise SystemExit(f"缺少 {_CREDENTIAL_ENV}")

    control = httpx.Client(base_url=_TOXIPROXY_API, timeout=5)
    resolver = _LabCredentialResolver(credential)
    result = {
        "normal_path_ok": False,
        "timeout_classified": False,
        "recovered_after_toxic_removed": False,
        "external_model_called": False,
        "real_device_accessed": False,
    }
    try:
        _remove_toxic(control)
        with SecurityPlatformHttpAdapter(_settings(timeout=3), resolver) as adapter:
            result["normal_path_ok"] = adapter.query_status("cam-offline-01").online is False

        response = control.post(
            f"/proxies/{_PROXY_NAME}/toxics",
            json={
                "name": _TOXIC_NAME,
                "type": "latency",
                "stream": "downstream",
                "toxicity": 1.0,
                "attributes": {"latency": 1200, "jitter": 0},
            },
        )
        response.raise_for_status()
        try:
            with SecurityPlatformHttpAdapter(_settings(timeout=0.2), resolver) as adapter:
                adapter.query_status("cam-offline-01")
        except DeviceAdapterError as exc:
            result["timeout_classified"] = exc.kind is DeviceAdapterErrorKind.TIMEOUT

        _remove_toxic(control)
        with SecurityPlatformHttpAdapter(_settings(timeout=3), resolver) as adapter:
            result["recovered_after_toxic_removed"] = (
                adapter.query_status("cam-offline-01").online is False
            )
    finally:
        _remove_toxic(control)
        control.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not all(
        result[key]
        for key in ("normal_path_ok", "timeout_classified", "recovered_after_toxic_removed")
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
