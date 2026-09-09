"""摄像头事实模型验收。"""

from __future__ import annotations

import pydantic
import pytest

from security_diagnosis_harness.domain.camera import (
    ChannelSnapshot,
    ChannelStatus,
    PlatformPullStatus,
    PullStatus,
    StreamKind,
    StreamSnapshot,
)
from security_diagnosis_harness.domain.device import REDACTED_VALUE

DEVICE_ID = "cam-stream-failed-01"


def test_channel_snapshot_can_be_created():
    channel = ChannelSnapshot(
        device_id=DEVICE_ID,
        channel_id="1",
        channel_status=ChannelStatus.ONLINE,
        bound=True,
        platform_registered=True,
    )

    assert channel.device_id == DEVICE_ID
    assert channel.channel_id == "1"
    assert channel.channel_status is ChannelStatus.ONLINE
    assert channel.bound is True
    assert channel.platform_registered is True
    assert channel.snapshot_id
    assert channel.captured_at is not None


def test_stream_snapshot_can_be_created():
    stream = StreamSnapshot(
        device_id=DEVICE_ID,
        stream_kind=StreamKind.MAIN,
        pull_status=PullStatus.FAILED,
        encoding="H.265",
        resolution="1920x1080",
        frame_rate=25,
        bitrate_kbps=2048,
        error_code="STREAM_PUBLISH_FAILED",
    )

    assert stream.stream_kind is StreamKind.MAIN
    assert stream.pull_status is PullStatus.FAILED
    assert stream.encoding == "H.265"
    assert stream.resolution == "1920x1080"
    assert stream.frame_rate == 25
    assert stream.bitrate_kbps == 2048
    assert stream.error_code == "STREAM_PUBLISH_FAILED"


def test_platform_pull_status_can_be_created():
    pull = PlatformPullStatus(
        device_id=DEVICE_ID,
        platform="vms-platform-a",
        pull_status=PullStatus.TIMEOUT,
        error_code="ENCODER_TIMEOUT",
    )

    assert pull.device_id == DEVICE_ID
    assert pull.platform == "vms-platform-a"
    assert pull.pull_status is PullStatus.TIMEOUT
    assert pull.error_code == "ENCODER_TIMEOUT"
    assert pull.last_failed_at is None


def test_enum_values_are_valid():
    assert {item.value for item in ChannelStatus} == {"online", "offline", "unknown"}
    assert {item.value for item in StreamKind} == {"main", "sub"}
    assert {item.value for item in PullStatus} == {"success", "failed", "timeout", "unknown"}


def test_defaults_are_unknown():
    assert ChannelSnapshot(device_id=DEVICE_ID).channel_status is ChannelStatus.UNKNOWN
    assert StreamSnapshot(device_id=DEVICE_ID).pull_status is PullStatus.UNKNOWN
    assert StreamSnapshot(device_id=DEVICE_ID).stream_kind is StreamKind.MAIN
    assert PlatformPullStatus(device_id=DEVICE_ID).pull_status is PullStatus.UNKNOWN


def test_blank_device_id_is_rejected():
    with pytest.raises(pydantic.ValidationError):
        ChannelSnapshot(device_id="")


@pytest.mark.parametrize(
    ("model_cls", "extra"),
    [
        (ChannelSnapshot, {"admin_password": "plain-secret"}),
        (StreamSnapshot, {"api_token": "plain-token"}),
        (PlatformPullStatus, {"access_key": "plain-key"}),
    ],
)
def test_sensitive_fields_are_redacted(model_cls, extra):
    instance = model_cls(device_id=DEVICE_ID, extra=extra)

    assert instance.redacted is True
    for key in extra:
        assert instance.extra[key] == REDACTED_VALUE
    for value in extra.values():
        assert value not in str(instance.extra)


def test_non_sensitive_extra_is_preserved():
    channel = ChannelSnapshot(device_id=DEVICE_ID, extra={"firmware": "V5.7.0"})

    assert channel.redacted is False
    assert channel.extra["firmware"] == "V5.7.0"


def test_models_do_not_express_conclusions():
    """事实模型不应包含任何结论字段。"""
    for model_cls in (ChannelSnapshot, StreamSnapshot, PlatformPullStatus):
        fields = set(model_cls.model_fields)
        assert "confidence" not in fields
        assert "root_cause" not in fields
        assert "conclusion" not in fields
