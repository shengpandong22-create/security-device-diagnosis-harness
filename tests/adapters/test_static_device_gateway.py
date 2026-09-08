"""StaticDeviceGateway 验收。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.domain.device import (
    REDACTED_VALUE,
    RecordingStatus,
    StreamStatus,
)
from security_diagnosis_harness.ports.device_gateway import (
    DeviceGateway,
    DeviceGatewayDataError,
    DeviceNotFoundError,
)

from ..conftest import DEVICE_ID, write_device_dataset


def test_static_gateway_satisfies_device_gateway_protocol(static_gateway):
    assert isinstance(static_gateway, DeviceGateway)


def test_query_status_returns_device_snapshot(static_gateway):
    snapshot = static_gateway.query_status(DEVICE_ID)

    assert snapshot.device_id == DEVICE_ID
    assert snapshot.online is True
    assert snapshot.channel_online is True
    assert snapshot.stream_status is StreamStatus.ABNORMAL
    assert snapshot.recording_status is RecordingStatus.RECORDING


def test_search_alarm_events_returns_events(static_gateway):
    events = static_gateway.search_alarm_events(DEVICE_ID)

    assert [event.event_type for event in events] == [
        "STREAM_PUBLISH_FAILED",
        "ENCODER_TIMEOUT",
        "DEVICE_ONLINE",
    ]
    assert all(event.device_id == DEVICE_ID for event in events)


def test_search_alarm_events_filters_by_keyword(static_gateway):
    events = static_gateway.search_alarm_events(DEVICE_ID, keyword="encoder")

    assert [event.event_type for event in events] == ["ENCODER_TIMEOUT"]


def test_search_alarm_events_respects_limit(static_gateway):
    events = static_gateway.search_alarm_events(DEVICE_ID, limit=2)

    assert len(events) == 2


def test_read_config_snapshot_returns_config(static_gateway):
    config = static_gateway.read_config_snapshot(DEVICE_ID)

    assert config.device_id == DEVICE_ID
    assert config.encoding == "H.265"
    assert config.resolution == "2560x1440"
    assert config.frame_rate == 25
    assert config.bitrate_kbps == 8192


def test_read_config_snapshot_redacts_credentials(static_gateway):
    config = static_gateway.read_config_snapshot(DEVICE_ID)

    assert config.redacted is True
    assert config.config["admin_password"] == REDACTED_VALUE
    assert "sample-placeholder-not-a-real-credential" not in str(config.config)


def test_unknown_device_raises_device_not_found(static_gateway):
    with pytest.raises(DeviceNotFoundError):
        static_gateway.query_status("camera-not-exist")


def test_missing_file_raises_data_error(tmp_path: Path):
    with pytest.raises(DeviceGatewayDataError):
        StaticDeviceGateway(tmp_path / "missing.json")


def test_invalid_json_raises_data_error(tmp_path: Path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")

    with pytest.raises(DeviceGatewayDataError):
        StaticDeviceGateway(broken)


def test_unexpected_schema_raises_data_error(tmp_path: Path):
    dataset = {"devices": [{"snapshot": {"online": True}}]}
    path = write_device_dataset(tmp_path / "bad-schema.json", dataset)  # type: ignore[arg-type]

    with pytest.raises(DeviceGatewayDataError):
        StaticDeviceGateway(path)


def test_device_without_snapshot_raises_data_error(tmp_path: Path):
    dataset = {"version": 1, "devices": [{"device_id": DEVICE_ID, "config": {"enabled": True}}]}
    path = write_device_dataset(tmp_path / "no-snapshot.json", dataset)  # type: ignore[arg-type]

    gateway = StaticDeviceGateway(path)
    with pytest.raises(DeviceGatewayDataError):
        gateway.query_status(DEVICE_ID)


def test_shipped_sample_file_is_loadable():
    sample_path = Path(__file__).resolve().parents[2] / "samples/devices/static_devices.sample.json"
    assert sample_path.exists()

    gateway = StaticDeviceGateway(sample_path)
    snapshot = gateway.query_status(DEVICE_ID)

    assert snapshot.stream_status is StreamStatus.ABNORMAL
    assert gateway.search_alarm_events(DEVICE_ID, keyword="stream")
    assert json.loads(sample_path.read_text(encoding="utf-8"))["version"] == 1
