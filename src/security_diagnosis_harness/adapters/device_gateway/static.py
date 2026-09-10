"""StaticDeviceGateway：从本地 JSON 文件读取设备样例数据。

只读取构造参数指定的那一个文件，不访问任何真实设备网络，
也不读取系统上的真实设备配置。

兼容两种数据形态：

- Phase 0 `static_devices.sample.json`：只有 snapshot / alarms / config；
- Phase 1 `camera_black_screen_cases.json`：额外包含 channel / streams /
  platform_pull / case_id / expected_label；
- Phase 2B `recording_missing_cases.json`：额外包含 recording_plans / storage /
  playback（三者都按 channel_id 分键）。
- Phase 3B `access_card_failed_cases.json`：额外包含 access_controller /
  doors / credentials / access_policies / access_events。
- Phase 4B `alarm_false_positive_cases.json`：额外包含 alarm_rules /
  alarm_signals / alarm_environments / alarm_verifications / alarm_correlations。

旧文件仍然可用于 Phase 0 demo；读取旧文件里不存在的摄像头/录像事实时，
抛出受控的 `DeviceGatewayDataError`，而不是返回伪造数据。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from security_diagnosis_harness.domain.access import (
    AccessControllerSnapshot,
    AccessEvent,
    AccessPolicySnapshot,
    CredentialSnapshot,
    DoorSnapshot,
)
from security_diagnosis_harness.domain.alarm import (
    AlarmCorrelationSnapshot,
    AlarmEnvironmentSnapshot,
    AlarmRuleSnapshot,
    AlarmSignalSnapshot,
    AlarmVerificationSnapshot,
)
from security_diagnosis_harness.domain.camera import (
    ChannelSnapshot,
    PlatformPullStatus,
    StreamKind,
    StreamSnapshot,
)
from security_diagnosis_harness.domain.device import (
    DeviceAlarmEvent,
    DeviceConfigSnapshot,
    DeviceSnapshot,
)
from security_diagnosis_harness.domain.recording import (
    PlaybackCheckResult,
    RecordingPlanSnapshot,
    StorageSnapshot,
)
from security_diagnosis_harness.ports.device_gateway import (
    DeviceGatewayDataError,
    DeviceNotFoundError,
)


def _parse_datetime(value: Any) -> datetime | None:
    """解析 ISO 时间字符串；无法解析时返回 None。

    兼容以 `Z` 结尾的 UTC 写法（Python 3.11+ 原生支持，这里统一处理）。
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ensure_aware(value: datetime) -> datetime:
    """把 naive datetime 按 UTC 处理，保证可以与 aware datetime 比较。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class StaticDeviceEntry(BaseModel):
    """单个设备的静态样例数据。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    channel: dict[str, Any] = Field(default_factory=dict)
    streams: dict[str, dict[str, Any]] = Field(default_factory=dict)
    platform_pull: dict[str, Any] = Field(default_factory=dict)
    # Phase 2B：录像事实，均按 channel_id 分键。
    recording_plans: dict[str, dict[str, Any]] = Field(default_factory=dict)
    storage: dict[str, dict[str, Any]] = Field(default_factory=dict)
    playback: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    # Phase 3B：门禁事实。
    access_controller: dict[str, Any] = Field(default_factory=dict)
    doors: dict[str, dict[str, Any]] = Field(default_factory=dict)
    credentials: dict[str, dict[str, Any]] = Field(default_factory=dict)
    access_policies: list[dict[str, Any]] = Field(default_factory=list)
    access_events: list[dict[str, Any]] = Field(default_factory=list)
    # Phase 4B：报警误报事实。
    alarm_rules: dict[str, dict[str, Any]] = Field(default_factory=dict)
    alarm_signals: dict[str, dict[str, Any]] = Field(default_factory=dict)
    alarm_environments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    alarm_verifications: dict[str, dict[str, Any]] = Field(default_factory=dict)
    alarm_correlations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    alarms: list[dict[str, Any]] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    case_id: str | None = None
    expected_label: str | None = None


class DeviceCaseRef(BaseModel):
    """评测用例引用。"""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    case_id: str | None = None
    expected_label: str | None = None


class StaticDeviceDataset(BaseModel):
    """静态设备数据文件结构。"""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    devices: list[StaticDeviceEntry] = Field(default_factory=list)


class StaticDeviceGateway:
    """只读静态设备网关。"""

    def __init__(self, data_path: str | Path) -> None:
        self._data_path = Path(data_path)
        self._entries: dict[str, StaticDeviceEntry] = self._load()

    @property
    def data_path(self) -> Path:
        return self._data_path

    def _load(self) -> dict[str, StaticDeviceEntry]:
        try:
            raw = self._data_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DeviceGatewayDataError(f"设备数据文件不可读: {self._data_path}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeviceGatewayDataError(f"设备数据文件不是合法 JSON: {self._data_path}") from exc

        try:
            dataset = StaticDeviceDataset.model_validate(payload)
        except ValidationError as exc:
            raise DeviceGatewayDataError(f"设备数据结构不符合预期: {exc}") from exc

        return {entry.device_id: entry for entry in dataset.devices}

    def _require_entry(self, device_id: str) -> StaticDeviceEntry:
        try:
            return self._entries[device_id]
        except KeyError as exc:
            raise DeviceNotFoundError(f"设备 {device_id} 不存在于静态样例数据中") from exc

    def query_status(self, device_id: str) -> DeviceSnapshot:
        entry = self._require_entry(device_id)
        if not entry.snapshot:
            raise DeviceGatewayDataError(f"设备 {device_id} 缺少状态快照数据")
        return DeviceSnapshot.model_validate({"device_id": device_id, **entry.snapshot})

    def query_channel_snapshot(self, device_id: str) -> ChannelSnapshot:
        entry = self._require_entry(device_id)
        if not entry.channel:
            raise DeviceGatewayDataError(f"设备 {device_id} 缺少通道快照数据")
        return ChannelSnapshot.model_validate({"device_id": device_id, **entry.channel})

    def query_stream_snapshot(
        self,
        device_id: str,
        stream_kind: StreamKind = StreamKind.MAIN,
    ) -> StreamSnapshot:
        entry = self._require_entry(device_id)
        stream = entry.streams.get(stream_kind.value)
        if stream is None:
            raise DeviceGatewayDataError(
                f"设备 {device_id} 缺少 {stream_kind.value} 码流快照数据"
            )
        return StreamSnapshot.model_validate(
            {"device_id": device_id, "stream_kind": stream_kind.value, **stream}
        )

    def query_platform_pull_status(self, device_id: str) -> PlatformPullStatus:
        entry = self._require_entry(device_id)
        if not entry.platform_pull:
            raise DeviceGatewayDataError(f"设备 {device_id} 缺少平台拉流状态数据")
        return PlatformPullStatus.model_validate({"device_id": device_id, **entry.platform_pull})

    # ------------------------------------------------------------ Phase 2B 录像事实
    def query_recording_plan(self, device_id: str, channel_id: str) -> RecordingPlanSnapshot:
        entry = self._require_entry(device_id)
        plan = entry.recording_plans.get(channel_id)
        if plan is None:
            raise DeviceGatewayDataError(
                f"设备 {device_id} 通道 {channel_id} 缺少录像计划数据"
            )
        return RecordingPlanSnapshot.model_validate(
            {"device_id": device_id, "channel_id": channel_id, **plan}
        )

    def query_storage_status(self, device_id: str, channel_id: str) -> StorageSnapshot:
        entry = self._require_entry(device_id)
        storage = entry.storage.get(channel_id)
        if storage is None:
            raise DeviceGatewayDataError(
                f"设备 {device_id} 通道 {channel_id} 缺少录像存储状态数据"
            )
        return StorageSnapshot.model_validate(storage)

    def check_recording_playback(
        self,
        device_id: str,
        channel_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> PlaybackCheckResult:
        entry = self._require_entry(device_id)
        candidates = entry.playback.get(channel_id) or []
        if not candidates:
            raise DeviceGatewayDataError(
                f"设备 {device_id} 通道 {channel_id} 缺少录像回放检查数据"
            )

        matched = self._match_playback_window(candidates, start_at, end_at)
        if matched is None:
            raise DeviceGatewayDataError(
                f"设备 {device_id} 通道 {channel_id} 在 "
                f"{start_at.isoformat()} ~ {end_at.isoformat()} 时间段没有回放检查数据"
            )
        return PlaybackCheckResult.model_validate(
            {
                "device_id": device_id,
                "channel_id": channel_id,
                "start_at": start_at,
                "end_at": end_at,
                **{
                    key: value
                    for key, value in matched.items()
                    if key not in ("start_at", "end_at")
                },
            }
        )

    @staticmethod
    def _match_playback_window(
        candidates: list[dict[str, Any]],
        start_at: datetime,
        end_at: datetime,
    ) -> dict[str, Any] | None:
        """按查询时间窗匹配样例中的回放记录。

        优先返回完全覆盖查询窗口的记录，其次返回有重叠的第一条；
        都没有时返回 None，由调用方转成受控失败。

        查询与样例窗口可能一个带时区一个不带（naive），比较前统一按 UTC 处理，
        避免 `can't compare offset-naive and offset-aware datetimes`。
        """
        query_start = _ensure_aware(start_at)
        query_end = _ensure_aware(end_at)

        for candidate in candidates:
            window_start = _parse_datetime(candidate.get("start_at"))
            window_end = _parse_datetime(candidate.get("end_at"))
            if window_start is None or window_end is None:
                continue
            window_start = _ensure_aware(window_start)
            window_end = _ensure_aware(window_end)
            if window_start <= query_start and window_end >= query_end:
                return candidate

        for candidate in candidates:
            window_start = _parse_datetime(candidate.get("start_at"))
            window_end = _parse_datetime(candidate.get("end_at"))
            if window_start is None or window_end is None:
                continue
            if _ensure_aware(window_start) < query_end and _ensure_aware(window_end) > query_start:
                return candidate
        return None

    # ------------------------------------------------------------ Phase 3B 门禁事实
    def query_access_controller(self, device_id: str) -> AccessControllerSnapshot:
        entry = self._require_entry(device_id)
        if not entry.access_controller:
            raise DeviceGatewayDataError(f"设备 {device_id} 缺少门禁控制器状态数据")
        return AccessControllerSnapshot.model_validate(
            {"device_id": device_id, **entry.access_controller}
        )

    def query_door(self, device_id: str, door_id: str) -> DoorSnapshot:
        entry = self._require_entry(device_id)
        door = entry.doors.get(door_id)
        if door is None:
            raise DeviceGatewayDataError(f"设备 {device_id} 门 {door_id} 缺少门状态数据")
        return DoorSnapshot.model_validate({"device_id": device_id, "door_id": door_id, **door})

    def query_credential(self, credential_id: str) -> CredentialSnapshot:
        for entry in self._entries.values():
            credential = entry.credentials.get(credential_id)
            if credential is not None:
                return CredentialSnapshot.model_validate(
                    {"device_id": entry.device_id, "credential_id": credential_id, **credential}
                )
        raise DeviceGatewayDataError(f"凭证 {credential_id} 不存在于静态样例数据中")

    def query_access_policy(self, person_id: str, door_id: str) -> AccessPolicySnapshot:
        for entry in self._entries.values():
            for policy in entry.access_policies:
                if policy.get("person_id") == person_id and policy.get("door_id") == door_id:
                    return AccessPolicySnapshot.model_validate(
                        {"device_id": entry.device_id, **policy}
                    )
        raise DeviceGatewayDataError(
            f"人员 {person_id} 与门 {door_id} 缺少门禁授权策略数据"
        )

    def search_access_events(
        self,
        device_id: str,
        door_id: str,
        credential_id: str,
        limit: int = 10,
    ) -> list[AccessEvent]:
        entry = self._require_entry(device_id)
        matched: list[AccessEvent] = []
        for event in entry.access_events:
            if event.get("door_id") != door_id:
                continue
            if event.get("credential_id") != credential_id:
                continue
            matched.append(AccessEvent.model_validate({"device_id": device_id, **event}))
        return matched[:limit]

    # ------------------------------------------------------------ Phase 4B 报警事实
    def query_alarm_rule(self, device_id: str, rule_id: str) -> AlarmRuleSnapshot:
        entry = self._require_entry(device_id)
        rule = entry.alarm_rules.get(rule_id)
        if rule is None:
            raise DeviceGatewayDataError(f"设备 {device_id} 规则 {rule_id} 缺少报警规则数据")
        return AlarmRuleSnapshot.model_validate(
            {"device_id": device_id, "rule_id": rule_id, **rule}
        )

    def query_alarm_signal(self, device_id: str, alarm_id: str) -> AlarmSignalSnapshot:
        entry = self._require_entry(device_id)
        signal = entry.alarm_signals.get(alarm_id)
        if signal is None:
            raise DeviceGatewayDataError(f"设备 {device_id} 告警 {alarm_id} 缺少触发信号数据")
        return AlarmSignalSnapshot.model_validate(
            {"device_id": device_id, "alarm_id": alarm_id, **signal}
        )

    def query_alarm_environment(
        self,
        device_id: str,
        alarm_id: str,
    ) -> AlarmEnvironmentSnapshot:
        entry = self._require_entry(device_id)
        environment = entry.alarm_environments.get(alarm_id)
        if environment is None:
            raise DeviceGatewayDataError(f"设备 {device_id} 告警 {alarm_id} 缺少环境数据")
        return AlarmEnvironmentSnapshot.model_validate(
            {"device_id": device_id, "alarm_id": alarm_id, **environment}
        )

    def query_alarm_verification(
        self,
        device_id: str,
        alarm_id: str,
    ) -> AlarmVerificationSnapshot:
        entry = self._require_entry(device_id)
        verification = entry.alarm_verifications.get(alarm_id)
        if verification is None:
            raise DeviceGatewayDataError(f"设备 {device_id} 告警 {alarm_id} 缺少复核数据")
        return AlarmVerificationSnapshot.model_validate(
            {"device_id": device_id, "alarm_id": alarm_id, **verification}
        )

    def query_alarm_correlation(
        self,
        device_id: str,
        alarm_id: str,
    ) -> AlarmCorrelationSnapshot:
        entry = self._require_entry(device_id)
        correlation = entry.alarm_correlations.get(alarm_id)
        if correlation is None:
            raise DeviceGatewayDataError(f"设备 {device_id} 告警 {alarm_id} 缺少关联告警数据")
        return AlarmCorrelationSnapshot.model_validate(
            {"device_id": device_id, "alarm_id": alarm_id, **correlation}
        )

    def search_alarm_events(
        self,
        device_id: str,
        keyword: str | None = None,
        limit: int = 10,
    ) -> list[DeviceAlarmEvent]:
        entry = self._require_entry(device_id)
        events = [
            DeviceAlarmEvent.model_validate({"device_id": device_id, **alarm})
            for alarm in entry.alarms
        ]
        if keyword:
            needle = keyword.lower()
            events = [
                event
                for event in events
                if needle in event.event_type.lower() or needle in event.message.lower()
            ]
        return events[:limit]

    def read_config_snapshot(self, device_id: str) -> DeviceConfigSnapshot:
        entry = self._require_entry(device_id)
        if not entry.config:
            raise DeviceGatewayDataError(f"设备 {device_id} 缺少配置快照数据")
        # 凭证字段脱敏由 DeviceConfigSnapshot 的校验器保证。
        return DeviceConfigSnapshot.model_validate({"device_id": device_id, **entry.config})

    def list_cases(self) -> list[DeviceCaseRef]:
        """列出带 case_id 的评测用例；没有 case_id 的设备会被跳过。"""
        return [
            DeviceCaseRef(
                device_id=entry.device_id,
                case_id=entry.case_id,
                expected_label=entry.expected_label,
            )
            for entry in self._entries.values()
            if entry.case_id
        ]
