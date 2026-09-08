"""设备事实对象验收：Device / DeviceSnapshot / DeviceAlarmEvent / DeviceConfigSnapshot。"""

from __future__ import annotations

from security_diagnosis_harness.domain.device import (
    REDACTED_VALUE,
    AlarmSeverity,
    DeviceType,
    RecordingStatus,
    StreamStatus,
    is_sensitive_key,
)

from ..conftest import DEVICE_ID


def test_device_expresses_master_data(device):
    assert device.device_id == DEVICE_ID
    assert device.device_type is DeviceType.CAMERA
    assert device.location == "3 号楼 1F 大厅"
    assert device.firmware_version == "V5.7.0"


def test_snapshot_expresses_device_status(snapshot):
    assert snapshot.device_id == DEVICE_ID
    assert snapshot.online is True
    assert snapshot.channel_online is True
    assert snapshot.stream_status is StreamStatus.ABNORMAL
    assert snapshot.recording_status is RecordingStatus.RECORDING
    assert snapshot.source == "static_device_gateway"


def test_alarm_event_expresses_device_alarm(alarm_event):
    assert alarm_event.device_id == DEVICE_ID
    assert alarm_event.event_type == "STREAM_PUBLISH_FAILED"
    assert alarm_event.severity is AlarmSeverity.CRITICAL
    assert alarm_event.message == "主码流发布失败"


def test_config_snapshot_expresses_device_config(config_snapshot):
    assert config_snapshot.device_id == DEVICE_ID
    assert config_snapshot.enabled is True
    assert config_snapshot.encoding == "H.265"
    assert config_snapshot.resolution == "2560x1440"
    assert config_snapshot.frame_rate == 25
    assert config_snapshot.bitrate_kbps == 8192


def test_config_snapshot_redacts_credentials(config_snapshot):
    assert config_snapshot.redacted is True
    assert config_snapshot.config["admin_password"] == REDACTED_VALUE
    assert "should-not-be-stored" not in str(config_snapshot.config)
    assert config_snapshot.config["bitrate_mode"] == "CBR"


def test_sensitive_key_detection():
    assert is_sensitive_key("admin_password")
    assert is_sensitive_key("access_token")
    assert is_sensitive_key("client_secret")
    assert not is_sensitive_key("bitrate_mode")
