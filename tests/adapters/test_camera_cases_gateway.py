"""StaticDeviceGateway 对 Phase 1 摄像头案例数据的支持。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.bootstrap.container import CAMERA_CASES_DATA_PATH
from security_diagnosis_harness.domain.camera import (
    ChannelStatus,
    PullStatus,
    StreamKind,
)
from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.ports.device_gateway import (
    DeviceGatewayDataError,
    DeviceNotFoundError,
)

EXPECTED_CASE_IDS = [
    "camera_offline",
    "channel_offline",
    "stream_publish_failed",
    "high_bitrate_encoder_timeout",
    "platform_pull_failed",
]

EXPECTED_DEVICES = [
    "cam-offline-01",
    "cam-channel-offline-01",
    "cam-stream-failed-01",
    "cam-high-bitrate-01",
    "cam-platform-pull-01",
]


@pytest.fixture
def gateway() -> StaticDeviceGateway:
    return StaticDeviceGateway(CAMERA_CASES_DATA_PATH)


def test_camera_cases_file_is_loadable(gateway):
    cases = gateway.list_cases()

    assert [case.case_id for case in cases] == EXPECTED_CASE_IDS


def test_all_five_devices_are_resolvable(gateway):
    for device_id in EXPECTED_DEVICES:
        snapshot = gateway.query_status(device_id)

        assert snapshot.device_id == device_id


def test_expected_labels_are_declared(gateway):
    labels = {case.case_id: case.expected_label for case in gateway.list_cases()}

    assert labels["camera_offline"] == "device_offline_or_network_unreachable"
    assert labels["channel_offline"] == "channel_binding_or_platform_access_issue"
    assert labels["stream_publish_failed"] == "stream_publish_or_encoder_issue"
    assert labels["high_bitrate_encoder_timeout"] == "overloaded_encoding_configuration"
    assert labels["platform_pull_failed"] == "platform_pull_or_access_path_issue"


def test_query_channel_snapshot(gateway):
    offline = gateway.query_channel_snapshot("cam-offline-01")
    normal = gateway.query_channel_snapshot("cam-stream-failed-01")

    assert offline.channel_status is ChannelStatus.OFFLINE
    assert normal.channel_status is ChannelStatus.ONLINE
    assert normal.platform_registered is True


def test_query_stream_snapshot_main_and_sub(gateway):
    main = gateway.query_stream_snapshot("cam-stream-failed-01")
    sub = gateway.query_stream_snapshot("cam-stream-failed-01", StreamKind.SUB)

    assert main.stream_kind is StreamKind.MAIN
    assert main.pull_status is PullStatus.FAILED
    assert sub.stream_kind is StreamKind.SUB
    assert sub.pull_status is PullStatus.SUCCESS


def test_query_platform_pull_status(gateway):
    pull = gateway.query_platform_pull_status("cam-platform-pull-01")

    assert pull.pull_status is PullStatus.FAILED
    assert pull.error_code == "PLATFORM_PULL_FAILED"


def test_config_credentials_are_redacted(gateway):
    for device_id in EXPECTED_DEVICES:
        config = gateway.read_config_snapshot(device_id)

        assert config.redacted is True
        assert config.config["admin_password"] == REDACTED_VALUE
        assert "sample-admin-pwd-not-real" not in str(config.config)


def test_unknown_device_is_controlled_failure(gateway):
    with pytest.raises(DeviceNotFoundError):
        gateway.query_status("cam-not-exist")
    with pytest.raises(DeviceNotFoundError):
        gateway.query_channel_snapshot("cam-not-exist")
    with pytest.raises(DeviceNotFoundError):
        gateway.query_stream_snapshot("cam-not-exist")
    with pytest.raises(DeviceNotFoundError):
        gateway.query_platform_pull_status("cam-not-exist")


def test_old_sample_file_is_still_compatible(device_data_file):
    """Phase 0 旧样例仍可用于 status / alarms / config。"""
    gateway = StaticDeviceGateway(device_data_file)

    snapshot = gateway.query_status("camera-3f-001")
    assert snapshot.online is True
    assert gateway.search_alarm_events("camera-3f-001")
    assert gateway.read_config_snapshot("camera-3f-001").bitrate_kbps == 8192
    assert gateway.list_cases() == []


def test_old_sample_file_fails_controlled_for_camera_facts(device_data_file):
    """旧样例缺少通道/码流/平台拉流数据时，必须受控失败而不是伪造。"""
    gateway = StaticDeviceGateway(device_data_file)

    with pytest.raises(DeviceGatewayDataError):
        gateway.query_channel_snapshot("camera-3f-001")
    with pytest.raises(DeviceGatewayDataError):
        gateway.query_stream_snapshot("camera-3f-001")
    with pytest.raises(DeviceGatewayDataError):
        gateway.query_platform_pull_status("camera-3f-001")


def test_missing_stream_kind_fails_controlled(gateway):
    with pytest.raises(DeviceGatewayDataError):
        gateway.query_stream_snapshot("cam-offline-01", StreamKind.SUB)
