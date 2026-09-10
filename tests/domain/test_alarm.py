"""报警误报领域事实模型验收。"""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from security_diagnosis_harness.domain import alarm as alarm_module
from security_diagnosis_harness.domain.alarm import (
    AlarmCorrelationSnapshot,
    AlarmEnvironmentSnapshot,
    AlarmRuleSensitivity,
    AlarmRuleSnapshot,
    AlarmSeverityLevel,
    AlarmSignalSnapshot,
    AlarmSignalStatus,
    AlarmTimeRange,
    AlarmType,
    AlarmVerificationSnapshot,
    CorrelationPattern,
    EnvironmentInterferenceType,
    VerificationResult,
    is_alarm_sensitive_key,
    redact_alarm_sensitive_values,
)
from security_diagnosis_harness.domain.device import REDACTED_VALUE


def test_alarm_rule_expresses_sensitive_rule():
    rule = AlarmRuleSnapshot(
        device_id="alarm-device-1",
        rule_id="rule-1",
        alarm_type=AlarmType.INTRUSION,
        sensitivity=AlarmRuleSensitivity.HIGH,
        threshold=25,
        debounce_seconds=1,
        armed_time_ranges=[AlarmTimeRange(start=time(0, 0), end=time(0, 0))],
    )

    assert rule.enabled is True
    assert rule.severity is AlarmSeverityLevel.WARNING
    assert rule.has_armed_schedule is True
    assert rule.is_over_sensitive is True


def test_alarm_rule_normal_threshold_is_not_over_sensitive():
    rule = AlarmRuleSnapshot(
        device_id="alarm-device-1",
        rule_id="rule-1",
        alarm_type=AlarmType.MOTION,
        sensitivity=AlarmRuleSensitivity.LOW,
        threshold=80,
        debounce_seconds=10,
    )

    assert rule.is_over_sensitive is False


@pytest.mark.parametrize("field", ["device_id", "rule_id"])
def test_alarm_rule_rejects_blank_identifiers(field):
    payload = {
        "device_id": "alarm-device-1",
        "rule_id": "rule-1",
        "threshold": 50,
    }
    payload[field] = ""

    with pytest.raises(ValidationError):
        AlarmRuleSnapshot(**payload)


@pytest.mark.parametrize(
    "payload",
    [{"threshold": -1}, {"debounce_seconds": -1}],
)
def test_alarm_rule_rejects_negative_values(payload):
    with pytest.raises(ValidationError):
        AlarmRuleSnapshot(device_id="alarm-device-1", rule_id="rule-1", **payload)


def test_alarm_time_range_supports_crossing_midnight():
    time_range = AlarmTimeRange(start=time(22, 0), end=time(6, 0))

    assert time_range.crosses_midnight is True
    assert time_range.is_all_day is False


def test_alarm_time_range_all_day_is_not_crossing():
    time_range = AlarmTimeRange(start=time(0, 0), end=time(0, 0))

    assert time_range.is_all_day is True
    assert time_range.crosses_midnight is False


@pytest.mark.parametrize("weekday", [0, 8, -1])
def test_alarm_time_range_rejects_invalid_weekday(weekday):
    with pytest.raises(ValidationError):
        AlarmTimeRange(start=time(8, 0), end=time(18, 0), weekdays=[weekday])


def test_alarm_signal_expresses_noisy_signal():
    signal = AlarmSignalSnapshot(
        device_id="alarm-device-1",
        alarm_id="alarm-1",
        sensor_id="sensor-1",
        signal_value=85,
        threshold=60,
        noise_level=0.8,
        status=AlarmSignalStatus.NOISY,
    )

    assert signal.exceeds_threshold is True
    assert signal.is_noisy is True


def test_alarm_signal_stable_low_noise_is_not_noisy():
    signal = AlarmSignalSnapshot(
        device_id="alarm-device-1",
        alarm_id="alarm-1",
        sensor_id="sensor-1",
        signal_value=55,
        threshold=60,
        noise_level=0.1,
        status=AlarmSignalStatus.STABLE,
    )

    assert signal.exceeds_threshold is False
    assert signal.is_noisy is False


@pytest.mark.parametrize("field", ["signal_value", "threshold"])
def test_alarm_signal_rejects_negative_values(field):
    payload = {
        "device_id": "alarm-device-1",
        "alarm_id": "alarm-1",
        "sensor_id": "sensor-1",
        "signal_value": 80,
        "threshold": 60,
    }
    payload[field] = -1

    with pytest.raises(ValidationError):
        AlarmSignalSnapshot(**payload)


@pytest.mark.parametrize("noise", [-0.1, 1.1])
def test_alarm_signal_rejects_noise_out_of_range(noise):
    with pytest.raises(ValidationError):
        AlarmSignalSnapshot(
            device_id="alarm-device-1",
            alarm_id="alarm-1",
            sensor_id="sensor-1",
            signal_value=80,
            threshold=60,
            noise_level=noise,
        )


def test_alarm_environment_expresses_interference():
    environment = AlarmEnvironmentSnapshot(
        device_id="alarm-device-1",
        alarm_id="alarm-1",
        interference_types=[
            EnvironmentInterferenceType.RAIN,
            EnvironmentInterferenceType.STRONG_LIGHT,
        ],
        visibility="low",
        illumination_lux=12000,
        wind_speed=12,
    )

    assert environment.has_interference is True


def test_alarm_environment_none_means_no_interference():
    environment = AlarmEnvironmentSnapshot(
        device_id="alarm-device-1",
        alarm_id="alarm-1",
        interference_types=[EnvironmentInterferenceType.NONE],
    )

    assert environment.has_interference is False


def test_alarm_verification_expresses_negative_result():
    verification = AlarmVerificationSnapshot(
        device_id="alarm-device-1",
        alarm_id="alarm-1",
        result=VerificationResult.NO_TARGET_FOUND,
        target_count=0,
    )

    assert verification.indicates_false_alarm is True


def test_alarm_verification_target_found_is_not_false_alarm():
    verification = AlarmVerificationSnapshot(
        device_id="alarm-device-1",
        alarm_id="alarm-1",
        result=VerificationResult.TARGET_FOUND,
        target_count=1,
    )

    assert verification.indicates_false_alarm is False


def test_alarm_verification_rejects_negative_target_count():
    with pytest.raises(ValidationError):
        AlarmVerificationSnapshot(
            device_id="alarm-device-1",
            alarm_id="alarm-1",
            result=VerificationResult.NO_TARGET_FOUND,
            target_count=-1,
        )


def test_alarm_correlation_expresses_burst():
    correlation = AlarmCorrelationSnapshot(
        device_id="alarm-device-1",
        alarm_id="alarm-1",
        repeated_count=8,
        neighbor_alarm_count=0,
        pattern=CorrelationPattern.BURST,
    )

    assert correlation.is_burst is True
    assert correlation.has_neighbor_correlation is False


def test_alarm_correlation_expresses_multi_device():
    correlation = AlarmCorrelationSnapshot(
        device_id="alarm-device-1",
        alarm_id="alarm-1",
        repeated_count=1,
        neighbor_alarm_count=3,
        pattern=CorrelationPattern.MULTI_DEVICE,
    )

    assert correlation.is_burst is False
    assert correlation.has_neighbor_correlation is True


@pytest.mark.parametrize("field", ["repeated_count", "neighbor_alarm_count", "window_seconds"])
def test_alarm_correlation_rejects_negative_counts(field):
    payload = {
        "device_id": "alarm-device-1",
        "alarm_id": "alarm-1",
        "repeated_count": 1,
        "neighbor_alarm_count": 1,
        "window_seconds": 300,
    }
    payload[field] = -1

    with pytest.raises(ValidationError):
        AlarmCorrelationSnapshot(**payload)


@pytest.mark.parametrize(
    "key",
    ["snapshot_url", "video_url", "person_id", "card_no", "license_plate", "token"],
)
def test_alarm_sensitive_key_detection(key):
    assert is_alarm_sensitive_key(key)


def test_alarm_extra_fields_are_redacted_recursively():
    cleaned, changed = redact_alarm_sensitive_values(
        {
            "snapshot_url": "http://example.local/snap.jpg",
            "nested": {"person_id": "person-001", "normal": "kept"},
            "items": [{"card_no": "card-001"}],
        }
    )

    assert changed is True
    assert cleaned["snapshot_url"] == REDACTED_VALUE
    assert cleaned["nested"]["person_id"] == REDACTED_VALUE
    assert cleaned["nested"]["normal"] == "kept"
    assert cleaned["items"][0]["card_no"] == REDACTED_VALUE


def test_alarm_model_extra_redaction_sets_flag():
    snapshot = AlarmEnvironmentSnapshot(
        device_id="alarm-device-1",
        alarm_id="alarm-1",
        extra={"video_url": "rtsp://example.local/live"},
    )

    assert snapshot.redacted is True
    assert snapshot.extra["video_url"] == REDACTED_VALUE


@pytest.mark.parametrize(
    "model_cls,payload",
    [
        (
            AlarmRuleSnapshot,
            {"device_id": "d1", "rule_id": "r1", "threshold": 50},
        ),
        (
            AlarmSignalSnapshot,
            {
                "device_id": "d1",
                "alarm_id": "a1",
                "sensor_id": "s1",
                "signal_value": 80,
                "threshold": 60,
            },
        ),
        (AlarmEnvironmentSnapshot, {"device_id": "d1", "alarm_id": "a1"}),
        (AlarmVerificationSnapshot, {"device_id": "d1", "alarm_id": "a1"}),
        (AlarmCorrelationSnapshot, {"device_id": "d1", "alarm_id": "a1"}),
    ],
)
def test_alarm_models_do_not_express_conclusions(model_cls, payload):
    model = model_cls(**payload)

    for forbidden in ("confidence", "root_cause", "conclusion", "final_status"):
        assert not hasattr(model, forbidden)


def test_alarm_module_has_no_infrastructure_dependencies():
    source = Path(alarm_module.__file__).read_text(encoding="utf-8").lower()

    for forbidden in ("fastapi", "sqlalchemy", "alembic", "openai", "httpx", "requests"):
        assert forbidden not in source


def test_alarm_enum_values_match_specification():
    assert AlarmType.MOTION.value == "motion"
    assert AlarmRuleSensitivity.HIGH.value == "high"
    assert AlarmSignalStatus.NOISY.value == "noisy"
    assert EnvironmentInterferenceType.STRONG_LIGHT.value == "strong_light"
    assert VerificationResult.NO_TARGET_FOUND.value == "no_target_found"
    assert CorrelationPattern.BURST.value == "burst"
