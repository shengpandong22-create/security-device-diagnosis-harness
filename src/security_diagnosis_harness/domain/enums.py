"""安防诊断核心枚举。"""

from __future__ import annotations

from enum import StrEnum


class SecurityFaultType(StrEnum):
    """Phase 0 支持的安防故障类型。"""

    CAMERA_BLACK_SCREEN = "camera_black_screen"
    RECORDING_MISSING = "recording_missing"
    ACCESS_CARD_FAILED = "access_card_failed"
    ALARM_FALSE_POSITIVE = "alarm_false_positive"


class SecurityDiagnosisStatus(StrEnum):
    """一次诊断用例的生命周期状态。

    `CONFIRMED` 是终态，且只能由人工 review 动作产生。
    """

    CREATED = "created"
    INVESTIGATING = "investigating"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
