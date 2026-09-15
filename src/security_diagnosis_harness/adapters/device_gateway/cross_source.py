"""摄像头跨来源组合网关：ONVIF 设备事实 + 平台业务事实。"""

from __future__ import annotations

from collections.abc import Callable

from security_diagnosis_harness.domain.camera import StreamKind, StreamSnapshot
from security_diagnosis_harness.ports.device_gateway import DeviceGateway

__all__ = ["CrossSourceCameraGateway"]


class CrossSourceCameraGateway:
    """按操作固定委派，不 fallback、不重试，也不让模型选择来源。"""

    adapter_key = "cross_source_camera"

    def __init__(
        self,
        onvif: DeviceGateway,
        platform: DeviceGateway,
        *,
        content_probe: Callable[[], bool] | None = None,
        content_probe_device_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._onvif = onvif
        self._platform = platform
        self._content_probe = content_probe
        self._content_probe_device_ids = content_probe_device_ids

    def query_status(self, device_id: str):
        return self._onvif.query_status(device_id)

    def query_channel_snapshot(self, device_id: str):
        return self._onvif.query_channel_snapshot(device_id)

    def query_stream_snapshot(
        self, device_id: str, stream_kind: StreamKind = StreamKind.MAIN
    ) -> StreamSnapshot:
        snapshot = self._onvif.query_stream_snapshot(device_id, stream_kind)
        if (
            stream_kind is StreamKind.MAIN
            and device_id in self._content_probe_device_ids
            and self._content_probe is not None
            and snapshot.pull_status.value == "success"
        ):
            copied = snapshot.model_copy(deep=True)
            copied.extra["content_black"] = self._content_probe()
            copied.extra["content_analysis"] = "ffmpeg_blackdetect"
            return copied
        return snapshot

    def query_platform_pull_status(self, device_id: str):
        return self._platform.query_platform_pull_status(device_id)

    def read_config_snapshot(self, device_id: str):
        return self._onvif.read_config_snapshot(device_id)

    def search_alarm_events(self, device_id: str, keyword: str | None = None, limit: int = 10):
        return self._platform.search_alarm_events(device_id, keyword, limit)

    def __getattr__(self, name: str):
        """其余既有只读能力固定交给平台 Adapter；不做动态来源选择。"""
        if name.startswith(("query_", "check_", "search_")):
            return getattr(self._platform, name)
        raise AttributeError(name)

