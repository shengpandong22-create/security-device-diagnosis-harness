"""Phase 4B 报警误报样例数据与 StaticDeviceGateway 验收。"""

from __future__ import annotations

from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.domain.alarm import (
    AlarmCorrelationSnapshot,
    AlarmEnvironmentSnapshot,
    AlarmRuleSensitivity,
    AlarmRuleSnapshot,
    AlarmSignalSnapshot,
    AlarmSignalStatus,
    AlarmVerificationSnapshot,
    CorrelationPattern,
    EnvironmentInterferenceType,
    VerificationResult,
)
from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.ports.device_gateway import DeviceGatewayDataError

from ..conftest import ALARM_CASES_DATA_PATH, RECORDING_CASES_DATA_PATH


def test_alarm_cases_dataset_can_be_loaded():
    gateway = StaticDeviceGateway(ALARM_CASES_DATA_PATH)

    cases = gateway.list_cases()

    assert len(cases) == 5
    assert {item.case_id for item in cases} == {
        "rule_too_sensitive",
        "environment_interference",
        "sensor_noise",
        "verification_negative",
        "duplicate_alarm_burst",
    }


def test_query_alarm_rule_returns_snapshot(alarm_gateway):
    rule = alarm_gateway.query_alarm_rule("alarm-rule-sensitive-01", "rule-1")

    assert isinstance(rule, AlarmRuleSnapshot)
    assert rule.sensitivity is AlarmRuleSensitivity.HIGH
    assert rule.is_over_sensitive is True


def test_query_alarm_signal_returns_snapshot(alarm_gateway):
    signal = alarm_gateway.query_alarm_signal("alarm-sensor-noise-01", "alarm-1")

    assert isinstance(signal, AlarmSignalSnapshot)
    assert signal.status is AlarmSignalStatus.NOISY
    assert signal.is_noisy is True


def test_query_alarm_environment_returns_snapshot(alarm_gateway):
    environment = alarm_gateway.query_alarm_environment(
        "alarm-environment-rain-01", "alarm-1"
    )

    assert isinstance(environment, AlarmEnvironmentSnapshot)
    assert EnvironmentInterferenceType.RAIN in environment.interference_types
    assert environment.has_interference is True


def test_query_alarm_verification_returns_snapshot_and_redacts_extra(alarm_gateway):
    verification = alarm_gateway.query_alarm_verification(
        "alarm-verification-negative-01", "alarm-1"
    )

    assert isinstance(verification, AlarmVerificationSnapshot)
    assert verification.result is VerificationResult.NO_TARGET_FOUND
    assert verification.indicates_false_alarm is True
    assert verification.extra["snapshot_url"] == REDACTED_VALUE


def test_query_alarm_correlation_returns_snapshot(alarm_gateway):
    correlation = alarm_gateway.query_alarm_correlation("alarm-duplicate-burst-01", "alarm-1")

    assert isinstance(correlation, AlarmCorrelationSnapshot)
    assert correlation.pattern is CorrelationPattern.BURST
    assert correlation.is_burst is True


def test_missing_alarm_rule_becomes_controlled_failure(alarm_gateway):
    try:
        alarm_gateway.query_alarm_rule("alarm-rule-sensitive-01", "rule-missing")
    except DeviceGatewayDataError as exc:
        assert "缺少报警规则数据" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected DeviceGatewayDataError")


def test_old_dataset_has_no_alarm_facts():
    gateway = StaticDeviceGateway(RECORDING_CASES_DATA_PATH)

    try:
        gateway.query_alarm_signal("cam-rec-plan-disabled-01", "alarm-1")
    except DeviceGatewayDataError as exc:
        assert "缺少触发信号数据" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected DeviceGatewayDataError")
