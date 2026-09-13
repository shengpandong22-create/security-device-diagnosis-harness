"""Phase 9 Runtime 能力装配探针（Phase 9C-2B）。

用默认 `build_runtime_container()` 真实验证：

1. 默认 gateway 是 `RoutedDeviceGateway`（StaticDeviceGateway 只是内部 Adapter）；
2. `supported_fault_types` 来自 `resolve_fault_support()` 的推导结果；
3. 默认摄像头黑屏闭环可完成；
4. 非摄像头域在 create 处受控拒绝。

输出只包含布尔值、故障域 value 与判定 reason 码；
不包含 endpoint、credential、真实标识或绝对路径。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许以脚本方式直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security_diagnosis_harness.adapters.device_gateway.routed import (  # noqa: E402
    RoutedDeviceGateway,
)
from security_diagnosis_harness.application.errors import (  # noqa: E402
    UnsupportedFaultTypeError,
)
from security_diagnosis_harness.config import RuntimeSettings  # noqa: E402
from security_diagnosis_harness.domain.enums import SecurityFaultType  # noqa: E402
from security_diagnosis_harness.runtime import build_runtime_container  # noqa: E402

NON_CAMERA_FAULT_TYPES = (
    SecurityFaultType.RECORDING_MISSING,
    SecurityFaultType.ACCESS_CARD_FAILED,
    SecurityFaultType.ALARM_FALSE_POSITIVE,
)


def main() -> int:
    runtime = build_runtime_container(RuntimeSettings(repository_mode="memory"))
    try:
        gateway_is_routed = isinstance(runtime.gateway, RoutedDeviceGateway)

        service_types = frozenset(runtime.service.supported_fault_types or ())
        resolver_types = frozenset(runtime.capability_support.supported_fault_types)
        service_matches_resolver = service_types == resolver_types

        verdicts = {
            verdict.fault_type.value: verdict.reason.value
            for verdict in runtime.capability_support.verdicts
        }

        case = runtime.service.create_diagnosis(
            device_id="camera-3f-001",
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="probe",
        )
        camera_run_ok = bool(runtime.service.run_diagnosis(case.diagnosis_id).ok)

        non_camera_create_rejected = True
        for fault_type in NON_CAMERA_FAULT_TYPES:
            try:
                runtime.service.create_diagnosis(
                    device_id="dev-1",
                    fault_type=fault_type,
                    reporter="probe",
                )
            except UnsupportedFaultTypeError:
                continue
            non_camera_create_rejected = False
    finally:
        runtime.close()

    report = {
        "gateway_is_routed": gateway_is_routed,
        "service_matches_resolver": service_matches_resolver,
        "supported_fault_types": sorted(resolver_types, key=lambda item: item.value),
        "verdicts": dict(sorted(verdicts.items())),
        "camera_run_ok": camera_run_ok,
        "non_camera_create_rejected": non_camera_create_rejected,
    }
    print(f"GATEWAY_IS_ROUTED: {gateway_is_routed}")
    print(f"SERVICE_MATCHES_RESOLVER: {service_matches_resolver}")
    for fault_type_value, reason in report["verdicts"].items():
        print(f"VERDICT {fault_type_value}: {reason}")
    print(f"CAMERA_RUN_OK: {camera_run_ok}")
    print(f"NON_CAMERA_CREATE_REJECTED: {non_camera_create_rejected}")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    ok = (
        gateway_is_routed
        and service_matches_resolver
        and report["supported_fault_types"] == ["camera_black_screen"]
        and camera_run_ok
        and non_camera_create_rejected
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
