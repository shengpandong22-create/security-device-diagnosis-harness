from unittest.mock import Mock

from security_diagnosis_harness.adapters.device_gateway.cross_source import (
    CrossSourceCameraGateway,
)
from security_diagnosis_harness.domain.camera import PullStatus, StreamKind, StreamSnapshot


def _stream() -> StreamSnapshot:
    return StreamSnapshot(
        device_id="camera-1",
        stream_kind=StreamKind.MAIN,
        pull_status=PullStatus.SUCCESS,
    )


def test_cross_source_gateway_uses_fixed_sources_without_fallback() -> None:
    onvif = Mock()
    platform = Mock()
    onvif.query_status.return_value = object()
    platform.query_platform_pull_status.return_value = object()
    gateway = CrossSourceCameraGateway(onvif, platform)

    gateway.query_status("camera-1")
    gateway.query_platform_pull_status("camera-1")

    onvif.query_status.assert_called_once_with("camera-1")
    platform.query_platform_pull_status.assert_called_once_with("camera-1")
    assert not platform.query_status.called
    assert not onvif.query_platform_pull_status.called


def test_content_probe_only_enriches_explicit_device_main_stream() -> None:
    onvif = Mock()
    platform = Mock()
    onvif.query_stream_snapshot.return_value = _stream()
    probe = Mock(return_value=True)
    gateway = CrossSourceCameraGateway(
        onvif,
        platform,
        content_probe=probe,
        content_probe_device_ids=frozenset({"camera-1"}),
    )

    enriched = gateway.query_stream_snapshot("camera-1", StreamKind.MAIN)
    untouched = gateway.query_stream_snapshot("camera-2", StreamKind.MAIN)

    assert enriched.extra == {
        "content_black": True,
        "content_analysis": "ffmpeg_blackdetect",
    }
    assert untouched.extra == {}
    assert probe.call_count == 1


def test_cross_source_gateway_does_not_swallow_source_failure() -> None:
    onvif = Mock()
    onvif.query_status.side_effect = RuntimeError("controlled failure")
    gateway = CrossSourceCameraGateway(onvif, Mock())

    try:
        gateway.query_status("camera-1")
    except RuntimeError as exc:
        assert str(exc) == "controlled failure"
    else:
        raise AssertionError("来源失败不得被 fallback 掩盖")
