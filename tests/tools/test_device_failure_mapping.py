"""Phase 9C-3 设备失败分类与安全边界。"""

from security_diagnosis_harness.domain.device_integration import (
    DeviceAdapterError,
    DeviceAdapterErrorKind,
)
from security_diagnosis_harness.tools.device_failures import (
    DeviceFailureKind,
    classify_device_failure,
    device_failure_result,
)


def test_adapter_error_kinds_have_stable_mapping():
    for source, expected in (
        (DeviceAdapterErrorKind.AUTHENTICATION, DeviceFailureKind.AUTHENTICATION),
        (DeviceAdapterErrorKind.TIMEOUT, DeviceFailureKind.TIMEOUT),
        (DeviceAdapterErrorKind.RATE_LIMITED, DeviceFailureKind.RATE_LIMITED),
        (DeviceAdapterErrorKind.UNAVAILABLE, DeviceFailureKind.UNAVAILABLE),
        (
            DeviceAdapterErrorKind.UNSUPPORTED_CAPABILITY,
            DeviceFailureKind.UNSUPPORTED_CAPABILITY,
        ),
        (DeviceAdapterErrorKind.INVALID_RESPONSE, DeviceFailureKind.INVALID_RESPONSE),
    ):
        assert classify_device_failure(DeviceAdapterError(source, "query_status")) is expected


def test_unknown_error_does_not_leak_exception_text():
    secret = "token=plain-secret https://private.invalid device-sensitive adapter-secret"
    kind = classify_device_failure(RuntimeError(secret))
    result = device_failure_result("device__query_status", kind, "query_status")

    assert kind is DeviceFailureKind.UNEXPECTED
    assert result.evidence_drafts == []
    assert result.metadata == {"failure_kind": "unexpected", "operation": "query_status"}
    assert all(part not in (result.error or "") for part in secret.split())


def test_failure_result_metadata_is_low_cardinality():
    result = device_failure_result(
        "device__query_status", DeviceFailureKind.TIMEOUT, "query_status"
    )

    assert set(result.metadata) == {"failure_kind", "operation"}
    assert result.ok is False
    assert result.evidence_drafts == []
