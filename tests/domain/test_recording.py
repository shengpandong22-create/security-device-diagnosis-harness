"""录像缺失 / 录像异常事实模型验收。"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pydantic
import pytest

from security_diagnosis_harness.domain import recording as recording_module
from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.domain.recording import (
    ALL_WEEKDAYS,
    EXPECTED_PLAYABLE_BY_STATUS,
    LOW_FREE_PERCENT_THRESHOLD,
    PlaybackCheckResult,
    PlaybackStatus,
    RecordingMode,
    RecordingPlanSnapshot,
    RecordingPlanStatus,
    RecordingTimeRange,
    StorageSnapshot,
    StorageStatus,
)

DEVICE_ID = "cam-rec-01"
CHANNEL_ID = "1"
PLAN_ID = "plan-001"
STORAGE_ID = "storage-pool-a"

BASE_TIME = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------- 录像计划
def test_recording_plan_expresses_enabled_plan():
    plan = RecordingPlanSnapshot(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        plan_id=PLAN_ID,
        status=RecordingPlanStatus.ENABLED,
        mode=RecordingMode.CONTINUOUS,
        time_ranges=[RecordingTimeRange(start=time(0, 0), end=time(23, 59))],
        retention_days=30,
    )

    assert plan.device_id == DEVICE_ID
    assert plan.channel_id == CHANNEL_ID
    assert plan.plan_id == PLAN_ID
    assert plan.status is RecordingPlanStatus.ENABLED
    assert plan.mode is RecordingMode.CONTINUOUS
    assert plan.retention_days == 30
    assert plan.has_time_ranges is True
    assert plan.is_plan_active is True
    assert plan.has_schedule_gap is False


def test_recording_plan_disabled_is_not_active():
    plan = RecordingPlanSnapshot(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        plan_id=PLAN_ID,
        status=RecordingPlanStatus.DISABLED,
        mode=RecordingMode.MANUAL,
    )

    assert plan.is_plan_active is False
    assert plan.has_schedule_gap is False


def test_recording_plan_enabled_without_ranges_is_schedule_gap():
    """计划启用但没有时间段：能表达"看似正常但不会录像"的异常。"""
    plan = RecordingPlanSnapshot(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        plan_id=PLAN_ID,
        status=RecordingPlanStatus.ENABLED,
        mode=RecordingMode.CONTINUOUS,
    )

    assert plan.has_time_ranges is False
    assert plan.has_schedule_gap is True
    assert plan.is_plan_active is False


@pytest.mark.parametrize(
    "field",
    ["device_id", "channel_id", "plan_id"],
)
def test_recording_plan_rejects_blank_identifiers(field):
    payload = {
        "device_id": DEVICE_ID,
        "channel_id": CHANNEL_ID,
        "plan_id": PLAN_ID,
        "status": RecordingPlanStatus.ENABLED,
        "mode": RecordingMode.CONTINUOUS,
    }
    payload[field] = ""

    with pytest.raises(pydantic.ValidationError):
        RecordingPlanSnapshot(**payload)


def test_recording_plan_rejects_negative_retention_days():
    with pytest.raises(pydantic.ValidationError):
        RecordingPlanSnapshot(
            device_id=DEVICE_ID,
            channel_id=CHANNEL_ID,
            plan_id=PLAN_ID,
            status=RecordingPlanStatus.ENABLED,
            mode=RecordingMode.CONTINUOUS,
            retention_days=-1,
        )


def test_recording_plan_allows_zero_retention_days():
    plan = RecordingPlanSnapshot(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        plan_id=PLAN_ID,
        status=RecordingPlanStatus.ENABLED,
        mode=RecordingMode.EVENT_TRIGGERED,
        retention_days=0,
    )

    assert plan.retention_days == 0


# --------------------------------------------------------------- 时间段
def test_time_range_supports_normal_range():
    time_range = RecordingTimeRange(start=time(8, 0), end=time(18, 0))

    assert time_range.crosses_midnight is False
    assert time_range.is_all_day is False
    assert time_range.weekdays == list(ALL_WEEKDAYS)


def test_time_range_supports_crossing_midnight():
    time_range = RecordingTimeRange(start=time(22, 0), end=time(6, 0))

    assert time_range.crosses_midnight is True
    assert time_range.is_all_day is False


def test_time_range_all_day_is_not_crossing():
    time_range = RecordingTimeRange(start=time(0, 0), end=time(0, 0))

    assert time_range.is_all_day is True
    assert time_range.crosses_midnight is False


def test_plan_exposes_crossing_time_ranges():
    plan = RecordingPlanSnapshot(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        plan_id=PLAN_ID,
        status=RecordingPlanStatus.ENABLED,
        mode=RecordingMode.CONTINUOUS,
        time_ranges=[
            RecordingTimeRange(start=time(8, 0), end=time(18, 0)),
            RecordingTimeRange(start=time(22, 0), end=time(6, 0)),
        ],
    )

    assert len(plan.crossing_time_ranges) == 1
    assert plan.crossing_time_ranges[0].start == time(22, 0)


@pytest.mark.parametrize("weekday", [0, 8, -1])
def test_time_range_rejects_invalid_weekday(weekday):
    with pytest.raises(pydantic.ValidationError):
        RecordingTimeRange(start=time(8, 0), end=time(18, 0), weekdays=[weekday])


def test_time_range_accepts_weekdays_one_to_seven():
    time_range = RecordingTimeRange(
        start=time(8, 0), end=time(18, 0), weekdays=[1, 5, 7]
    )

    assert time_range.weekdays == [1, 5, 7]


# --------------------------------------------------------------- 存储
def test_storage_expresses_normal_status():
    storage = StorageSnapshot(
        storage_id=STORAGE_ID,
        status=StorageStatus.NORMAL,
        total_gb=2000.0,
        free_gb=1200.0,
        used_percent=40.0,
    )

    assert storage.storage_id == STORAGE_ID
    assert storage.status is StorageStatus.NORMAL
    assert storage.total_gb == 2000.0
    assert storage.free_gb == 1200.0
    assert storage.free_percent == pytest.approx(60.0)
    assert storage.is_capacity_low is False
    assert storage.is_available is True


def test_storage_rejects_blank_storage_id():
    with pytest.raises(pydantic.ValidationError):
        StorageSnapshot(storage_id="", status=StorageStatus.NORMAL)


@pytest.mark.parametrize(
    ("field", "value"),
    [("total_gb", -1.0), ("free_gb", -0.5)],
)
def test_storage_rejects_negative_capacity(field, value):
    payload = {"storage_id": STORAGE_ID, "status": StorageStatus.NORMAL}
    payload[field] = value

    with pytest.raises(pydantic.ValidationError):
        StorageSnapshot(**payload)


@pytest.mark.parametrize("used_percent", [-1.0, 101.0])
def test_storage_rejects_used_percent_out_of_range(used_percent):
    with pytest.raises(pydantic.ValidationError):
        StorageSnapshot(
            storage_id=STORAGE_ID,
            status=StorageStatus.NORMAL,
            used_percent=used_percent,
        )


@pytest.mark.parametrize("used_percent", [0.0, 100.0])
def test_storage_accepts_used_percent_boundaries(used_percent):
    storage = StorageSnapshot(
        storage_id=STORAGE_ID, status=StorageStatus.NORMAL, used_percent=used_percent
    )

    assert storage.used_percent == used_percent


def test_storage_detects_low_capacity_by_used_percent():
    storage = StorageSnapshot(
        storage_id=STORAGE_ID,
        status=StorageStatus.NORMAL,
        used_percent=100.0 - LOW_FREE_PERCENT_THRESHOLD + 1.0,
    )

    assert storage.is_capacity_low is True
    assert storage.is_available is False


def test_storage_detects_low_capacity_by_absolute_capacity():
    storage = StorageSnapshot(
        storage_id=STORAGE_ID,
        status=StorageStatus.NORMAL,
        total_gb=1000.0,
        free_gb=50.0,
    )

    assert storage.free_percent == pytest.approx(5.0)
    assert storage.is_capacity_low is True


def test_storage_full_is_capacity_low_even_without_numbers():
    storage = StorageSnapshot(storage_id=STORAGE_ID, status=StorageStatus.FULL)

    assert storage.free_percent is None
    assert storage.is_capacity_low is True
    assert storage.is_available is False


def test_storage_offline_is_not_available():
    storage = StorageSnapshot(
        storage_id=STORAGE_ID,
        status=StorageStatus.OFFLINE,
        last_error="storage node unreachable",
    )

    assert storage.status is StorageStatus.OFFLINE
    assert storage.last_error == "storage node unreachable"
    assert storage.is_available is False


def test_storage_free_percent_is_none_when_information_missing():
    storage = StorageSnapshot(storage_id=STORAGE_ID, status=StorageStatus.DEGRADED)

    assert storage.free_percent is None
    assert storage.is_capacity_low is False


# --------------------------------------------------------------- 回放检查
def test_playback_expresses_playable_recording():
    result = PlaybackCheckResult(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        start_at=BASE_TIME - timedelta(hours=1),
        end_at=BASE_TIME,
        status=PlaybackStatus.AVAILABLE,
        file_count=6,
        playable=True,
    )

    assert result.status is PlaybackStatus.AVAILABLE
    assert result.playable is True
    assert result.file_count == 6
    assert result.has_files is True
    assert result.is_missing is False


def test_playback_expresses_missing_recording():
    result = PlaybackCheckResult(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        start_at=BASE_TIME - timedelta(hours=1),
        end_at=BASE_TIME,
        status=PlaybackStatus.MISSING,
        file_count=0,
        playable=False,
        failure_reason="该时间段没有检索到录像文件",
    )

    assert result.playable is False
    assert result.has_files is False
    assert result.is_missing is True
    assert result.failure_reason


@pytest.mark.parametrize(
    ("status", "playable"),
    [
        (PlaybackStatus.CORRUPTED, False),
        (PlaybackStatus.INDEX_MISSING, False),
    ],
)
def test_playback_expresses_unplayable_statuses(status, playable):
    result = PlaybackCheckResult(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        start_at=BASE_TIME - timedelta(hours=1),
        end_at=BASE_TIME,
        status=status,
        file_count=3,
        playable=playable,
        failure_reason="录像无法解码",
    )

    assert result.status is status
    assert result.playable is False


def test_playback_rejects_non_positive_window():
    with pytest.raises(pydantic.ValidationError, match="end_at 必须大于 start_at"):
        PlaybackCheckResult(
            device_id=DEVICE_ID,
            channel_id=CHANNEL_ID,
            start_at=BASE_TIME,
            end_at=BASE_TIME,
            status=PlaybackStatus.AVAILABLE,
            file_count=1,
            playable=True,
        )


def test_playback_rejects_reversed_window():
    with pytest.raises(pydantic.ValidationError, match="end_at 必须大于 start_at"):
        PlaybackCheckResult(
            device_id=DEVICE_ID,
            channel_id=CHANNEL_ID,
            start_at=BASE_TIME,
            end_at=BASE_TIME - timedelta(hours=1),
            status=PlaybackStatus.MISSING,
            playable=False,
        )


def test_playback_rejects_negative_file_count():
    with pytest.raises(pydantic.ValidationError):
        PlaybackCheckResult(
            device_id=DEVICE_ID,
            channel_id=CHANNEL_ID,
            start_at=BASE_TIME - timedelta(hours=1),
            end_at=BASE_TIME,
            status=PlaybackStatus.AVAILABLE,
            file_count=-1,
            playable=True,
        )


def test_playback_rejects_status_and_playable_contradiction():
    with pytest.raises(pydantic.ValidationError, match="playable 应为"):
        PlaybackCheckResult(
            device_id=DEVICE_ID,
            channel_id=CHANNEL_ID,
            start_at=BASE_TIME - timedelta(hours=1),
            end_at=BASE_TIME,
            status=PlaybackStatus.MISSING,
            playable=True,
        )


def test_expected_playable_mapping_covers_all_statuses():
    assert set(EXPECTED_PLAYABLE_BY_STATUS) == set(PlaybackStatus)
    assert EXPECTED_PLAYABLE_BY_STATUS[PlaybackStatus.AVAILABLE] is True
    for status in (
        PlaybackStatus.MISSING,
        PlaybackStatus.CORRUPTED,
        PlaybackStatus.INDEX_MISSING,
    ):
        assert EXPECTED_PLAYABLE_BY_STATUS[status] is False


def test_playback_available_requires_positive_file_count():
    """available 表示录像可用且可回放，不允许 file_count=0。"""
    with pytest.raises(pydantic.ValidationError, match="file_count 必须大于 0"):
        PlaybackCheckResult(
            device_id=DEVICE_ID,
            channel_id=CHANNEL_ID,
            start_at=BASE_TIME - timedelta(hours=1),
            end_at=BASE_TIME,
            status=PlaybackStatus.AVAILABLE,
            playable=True,
        )


def test_playback_missing_requires_zero_file_count():
    """missing 表示该时间段没有录像，不允许带文件计数。"""
    with pytest.raises(pydantic.ValidationError, match="file_count 必须为 0"):
        PlaybackCheckResult(
            device_id=DEVICE_ID,
            channel_id=CHANNEL_ID,
            start_at=BASE_TIME - timedelta(hours=1),
            end_at=BASE_TIME,
            status=PlaybackStatus.MISSING,
            file_count=3,
            playable=False,
        )


def test_playback_corrupted_allows_existing_files():
    """corrupted 允许存在文件但损坏，file_count > 0 合法。"""
    result = PlaybackCheckResult(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        start_at=BASE_TIME - timedelta(hours=1),
        end_at=BASE_TIME,
        status=PlaybackStatus.CORRUPTED,
        file_count=2,
        playable=False,
        failure_reason="录像文件无法解码",
    )

    assert result.file_count == 2
    assert result.has_files is True
    assert result.playable is False


@pytest.mark.parametrize("file_count", [0, 3])
def test_playback_index_missing_does_not_force_file_count(file_count):
    """index_missing 的平台可见性不一致，file_count 不强制。"""
    result = PlaybackCheckResult(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        start_at=BASE_TIME - timedelta(hours=1),
        end_at=BASE_TIME,
        status=PlaybackStatus.INDEX_MISSING,
        file_count=file_count,
        playable=False,
    )

    assert result.file_count == file_count
    assert result.playable is False


def test_playback_available_always_has_files():
    """校验通过后，available 与 has_files / is_missing 语义保持一致。"""
    result = PlaybackCheckResult(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        start_at=BASE_TIME - timedelta(hours=1),
        end_at=BASE_TIME,
        status=PlaybackStatus.AVAILABLE,
        file_count=1,
        playable=True,
    )

    assert result.has_files is True
    assert result.is_missing is False


# --------------------------------------------------------------- 通用边界
def _plan_with_extra(extra: dict) -> RecordingPlanSnapshot:
    return RecordingPlanSnapshot(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        plan_id=PLAN_ID,
        status=RecordingPlanStatus.ENABLED,
        mode=RecordingMode.CONTINUOUS,
        extra=extra,
    )


def _storage_with_extra(extra: dict) -> StorageSnapshot:
    return StorageSnapshot(
        storage_id=STORAGE_ID,
        status=StorageStatus.NORMAL,
        extra=extra,
    )


def _playback_with_extra(extra: dict) -> PlaybackCheckResult:
    return PlaybackCheckResult(
        device_id=DEVICE_ID,
        channel_id=CHANNEL_ID,
        start_at=BASE_TIME - timedelta(hours=1),
        end_at=BASE_TIME,
        status=PlaybackStatus.MISSING,
        playable=False,
        extra=extra,
    )


@pytest.mark.parametrize(
    ("factory", "extra"),
    [
        (_plan_with_extra, {"admin_password": "plain-secret"}),
        (_storage_with_extra, {"api_token": "plain-token"}),
        (_playback_with_extra, {"access_key": "plain-key"}),
    ],
)
def test_sensitive_extra_fields_are_redacted(factory, extra):
    instance = factory(extra)

    assert instance.redacted is True
    for key in extra:
        assert instance.extra[key] == REDACTED_VALUE
    for value in extra.values():
        assert value not in str(instance.extra)


@pytest.mark.parametrize(
    ("factory", "extra"),
    [
        (_plan_with_extra, {"storage_path": "/volume1/record"}),
        (_storage_with_extra, {"raid_level": "raid5"}),
        (_playback_with_extra, {"codec": "H.265"}),
    ],
)
def test_non_sensitive_extra_is_preserved(factory, extra):
    instance = factory(extra)

    assert instance.redacted is False
    for key, value in extra.items():
        assert instance.extra[key] == value


@pytest.mark.parametrize(
    "model_cls",
    [RecordingPlanSnapshot, StorageSnapshot, PlaybackCheckResult],
)
def test_models_do_not_express_conclusions(model_cls):
    """事实模型不应包含任何结论字段。"""
    fields = set(model_cls.model_fields)
    assert "confidence" not in fields
    assert "root_cause" not in fields
    assert "conclusion" not in fields


@pytest.mark.parametrize(
    "model_cls",
    [RecordingTimeRange, RecordingPlanSnapshot, StorageSnapshot, PlaybackCheckResult],
)
def test_models_are_pydantic_models(model_cls):
    assert issubclass(model_cls, pydantic.BaseModel)


def test_recording_module_has_no_infrastructure_dependencies():
    """领域层不得引入 FastAPI / SQLAlchemy / LLM SDK 等基础设施依赖。"""
    source = Path(recording_module.__file__).read_text(encoding="utf-8").lower()

    for forbidden in ("fastapi", "sqlalchemy", "alembic", "openai", "httpx", "requests"):
        assert forbidden not in source


def test_enum_values_match_specification():
    assert {item.value for item in RecordingPlanStatus} == {
        "enabled",
        "disabled",
        "misconfigured",
    }
    assert {item.value for item in RecordingMode} == {
        "continuous",
        "event_triggered",
        "manual",
    }
    assert {item.value for item in StorageStatus} == {
        "normal",
        "full",
        "offline",
        "degraded",
    }
    assert {item.value for item in PlaybackStatus} == {
        "available",
        "missing",
        "corrupted",
        "index_missing",
    }
