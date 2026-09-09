"""StaticDeviceGateway 对 Phase 2B 录像缺失样例数据的支持。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.domain.recording import (
    PlaybackCheckResult,
    PlaybackStatus,
    RecordingMode,
    RecordingPlanSnapshot,
    RecordingPlanStatus,
    StorageSnapshot,
    StorageStatus,
)
from security_diagnosis_harness.ports.device_gateway import (
    DeviceGateway,
    DeviceGatewayDataError,
    DeviceNotFoundError,
)

from ..conftest import RECORDING_CASES_DATA_PATH

EXPECTED_CASE_IDS = [
    "recording_plan_disabled",
    "recording_schedule_gap",
    "storage_full",
    "storage_offline",
    "playback_index_missing",
]

EXPECTED_DEVICES = [
    "cam-rec-plan-disabled-01",
    "cam-rec-schedule-gap-01",
    "cam-rec-storage-full-01",
    "cam-rec-storage-offline-01",
    "cam-rec-index-missing-01",
]

CHANNEL_ID = "1"

# 查询窗口：覆盖样例中的 2026-09-08 ~ 2026-09-09（+08:00）。
QUERY_START = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
QUERY_END = datetime(2026, 9, 8, 13, 0, tzinfo=UTC)


@pytest.fixture
def gateway() -> StaticDeviceGateway:
    return StaticDeviceGateway(RECORDING_CASES_DATA_PATH)


def test_recording_cases_file_is_loadable():
    gateway = StaticDeviceGateway(RECORDING_CASES_DATA_PATH)

    assert [case.case_id for case in gateway.list_cases()] == EXPECTED_CASE_IDS


def test_all_five_cases_are_listed(gateway):
    cases = gateway.list_cases()

    assert len(cases) == 5
    assert {case.device_id for case in cases} == set(EXPECTED_DEVICES)
    for case in cases:
        assert case.expected_label


def test_gateway_satisfies_device_gateway_protocol(gateway):
    assert isinstance(gateway, DeviceGateway)


# ------------------------------------------------------------ 录像计划
def test_query_recording_plan_returns_snapshot(gateway):
    plan = gateway.query_recording_plan("cam-rec-plan-disabled-01", CHANNEL_ID)

    assert isinstance(plan, RecordingPlanSnapshot)
    assert plan.device_id == "cam-rec-plan-disabled-01"
    assert plan.channel_id == CHANNEL_ID
    assert plan.plan_id == "plan-disabled-001"
    assert plan.status is RecordingPlanStatus.DISABLED
    assert plan.mode is RecordingMode.MANUAL
    assert plan.is_plan_active is False


def test_query_recording_plan_for_schedule_gap_case(gateway):
    plan = gateway.query_recording_plan("cam-rec-schedule-gap-01", CHANNEL_ID)

    assert plan.status is RecordingPlanStatus.ENABLED
    assert plan.mode is RecordingMode.EVENT_TRIGGERED
    assert plan.retention_days == 14
    # 计划启用但只覆盖工作日 09:00-18:00，对全天候排查而言存在时间空隙。
    assert plan.has_time_ranges is True
    assert len(plan.time_ranges) == 1


def test_query_recording_plan_for_continuous_case(gateway):
    plan = gateway.query_recording_plan("cam-rec-storage-full-01", CHANNEL_ID)

    assert plan.mode is RecordingMode.CONTINUOUS
    assert plan.is_plan_active is True
    assert plan.time_ranges[0].is_all_day is True


def test_query_recording_plan_unknown_channel_fails(gateway):
    with pytest.raises(DeviceGatewayDataError):
        gateway.query_recording_plan("cam-rec-plan-disabled-01", "99")


def test_query_recording_plan_unknown_device_fails(gateway):
    with pytest.raises(DeviceNotFoundError):
        gateway.query_recording_plan("cam-not-exist", CHANNEL_ID)


# ------------------------------------------------------------ 存储状态
def test_query_storage_status_returns_snapshot(gateway):
    storage = gateway.query_storage_status("cam-rec-plan-disabled-01", CHANNEL_ID)

    assert isinstance(storage, StorageSnapshot)
    assert storage.storage_id == "storage-pool-a"
    assert storage.status is StorageStatus.NORMAL
    assert storage.is_capacity_low is False
    assert storage.is_available is True


def test_query_storage_status_for_full_case(gateway):
    storage = gateway.query_storage_status("cam-rec-storage-full-01", CHANNEL_ID)

    assert storage.status is StorageStatus.FULL
    assert storage.is_capacity_low is True
    assert storage.is_available is False
    assert storage.last_error


def test_query_storage_status_for_offline_case(gateway):
    storage = gateway.query_storage_status("cam-rec-storage-offline-01", CHANNEL_ID)

    assert storage.status is StorageStatus.OFFLINE
    assert storage.is_available is False
    assert storage.is_capacity_low is False
    assert storage.last_error == "storage node unreachable"


def test_query_storage_status_unknown_channel_fails(gateway):
    with pytest.raises(DeviceGatewayDataError):
        gateway.query_storage_status("cam-rec-plan-disabled-01", "99")


def test_query_storage_status_unknown_device_fails(gateway):
    with pytest.raises(DeviceNotFoundError):
        gateway.query_storage_status("cam-not-exist", CHANNEL_ID)


# ------------------------------------------------------------ 回放检查
def test_check_recording_playback_returns_result(gateway):
    result = gateway.check_recording_playback(
        "cam-rec-plan-disabled-01", CHANNEL_ID, QUERY_START, QUERY_END
    )

    assert isinstance(result, PlaybackCheckResult)
    assert result.device_id == "cam-rec-plan-disabled-01"
    assert result.channel_id == CHANNEL_ID
    assert result.start_at == QUERY_START
    assert result.end_at == QUERY_END
    assert result.status is PlaybackStatus.MISSING
    assert result.playable is False
    assert result.file_count == 0
    assert result.failure_reason


@pytest.mark.parametrize(
    ("device_id", "expected_status"),
    [
        ("cam-rec-plan-disabled-01", PlaybackStatus.MISSING),
        ("cam-rec-schedule-gap-01", PlaybackStatus.MISSING),
        ("cam-rec-storage-full-01", PlaybackStatus.MISSING),
        ("cam-rec-storage-offline-01", PlaybackStatus.MISSING),
        ("cam-rec-index-missing-01", PlaybackStatus.INDEX_MISSING),
    ],
)
def test_check_recording_playback_for_each_case(gateway, device_id, expected_status):
    result = gateway.check_recording_playback(device_id, CHANNEL_ID, QUERY_START, QUERY_END)

    assert result.status is expected_status
    assert result.playable is (expected_status is PlaybackStatus.AVAILABLE)


def test_check_recording_playback_index_missing_keeps_file_count(gateway):
    result = gateway.check_recording_playback(
        "cam-rec-index-missing-01", CHANNEL_ID, QUERY_START, QUERY_END
    )

    assert result.file_count == 12
    assert result.has_files is True
    assert result.playable is False


def test_check_recording_playback_out_of_sample_window_fails(gateway):
    far_start = datetime(2030, 1, 1, 0, 0, tzinfo=UTC)

    with pytest.raises(DeviceGatewayDataError, match="没有回放检查数据"):
        gateway.check_recording_playback(
            "cam-rec-plan-disabled-01", CHANNEL_ID, far_start, far_start + timedelta(hours=1)
        )


def test_check_recording_playback_unknown_channel_fails(gateway):
    with pytest.raises(DeviceGatewayDataError):
        gateway.check_recording_playback(
            "cam-rec-plan-disabled-01", "99", QUERY_START, QUERY_END
        )


def test_check_recording_playback_unknown_device_fails(gateway):
    with pytest.raises(DeviceNotFoundError):
        gateway.check_recording_playback("cam-not-exist", CHANNEL_ID, QUERY_START, QUERY_END)


def test_check_recording_playback_matches_by_overlap(gateway):
    """查询窗口与样例窗口部分重叠时仍能匹配。

    样例窗口为 2026-09-08T00:00+08:00 ~ 2026-09-09T00:00+08:00
    （即 2026-09-07T16:00Z ~ 2026-09-08T16:00Z），这里取其内部的 UTC 窗口。
    """
    overlapping_start = datetime(2026, 9, 8, 8, 0, tzinfo=UTC)
    overlapping_end = datetime(2026, 9, 8, 9, 0, tzinfo=UTC)

    result = gateway.check_recording_playback(
        "cam-rec-plan-disabled-01", CHANNEL_ID, overlapping_start, overlapping_end
    )

    assert result.status is PlaybackStatus.MISSING


def test_check_recording_playback_accepts_naive_query_window(gateway):
    """查询时间不带时区（naive）时按 UTC 处理，不应崩溃。"""
    result = gateway.check_recording_playback(
        "cam-rec-plan-disabled-01",
        CHANNEL_ID,
        datetime(2026, 9, 8, 12, 0),
        datetime(2026, 9, 8, 13, 0),
    )

    assert result.status is PlaybackStatus.MISSING
    assert result.start_at.tzinfo is None
    assert result.end_at.tzinfo is None


# ------------------------------------------------------------ 兼容性与脱敏
def test_camera_cases_file_still_works_without_recording_facts():
    """Phase 1 样例没有录像事实时必须受控失败，不能返回伪造数据。"""
    from security_diagnosis_harness.bootstrap.container import CAMERA_CASES_DATA_PATH

    gateway = StaticDeviceGateway(CAMERA_CASES_DATA_PATH)

    with pytest.raises(DeviceGatewayDataError):
        gateway.query_recording_plan("cam-stream-failed-01", CHANNEL_ID)
    with pytest.raises(DeviceGatewayDataError):
        gateway.query_storage_status("cam-stream-failed-01", CHANNEL_ID)
    with pytest.raises(DeviceGatewayDataError):
        gateway.check_recording_playback(
            "cam-stream-failed-01", CHANNEL_ID, QUERY_START, QUERY_END
        )


def test_old_phase0_sample_file_still_loadable(device_data_file):
    gateway = StaticDeviceGateway(device_data_file)

    assert gateway.query_status("camera-3f-001").online is True
    assert gateway.list_cases() == []


def test_config_credentials_are_redacted(gateway):
    from security_diagnosis_harness.domain.device import DeviceConfigSnapshot

    for device_id in EXPECTED_DEVICES:
        config = gateway.read_config_snapshot(device_id)

        assert isinstance(config, DeviceConfigSnapshot)
        assert config.redacted is True
        assert config.config["admin_password"] == REDACTED_VALUE
        assert "sample-admin-pwd-not-real" not in str(config.config)


def test_recording_facts_do_not_leak_credentials(gateway):
    """录像事实（计划/存储/回放）中不得出现未脱敏凭证。"""
    plan = gateway.query_recording_plan("cam-rec-plan-disabled-01", CHANNEL_ID)
    storage = gateway.query_storage_status("cam-rec-plan-disabled-01", CHANNEL_ID)
    playback = gateway.check_recording_playback(
        "cam-rec-plan-disabled-01", CHANNEL_ID, QUERY_START, QUERY_END
    )

    for snapshot in (plan, storage, playback):
        assert "sample-admin-pwd-not-real" not in str(snapshot.model_dump(mode="json"))
        assert hasattr(snapshot, "redacted")
