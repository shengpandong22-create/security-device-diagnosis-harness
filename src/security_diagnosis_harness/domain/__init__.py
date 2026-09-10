"""安防设备诊断领域模型。

领域层只允许依赖标准库与 pydantic，禁止依赖 FastAPI、SQLAlchemy、
HTTP 客户端或具体 LLM SDK。
"""

from security_diagnosis_harness.domain.access import (
    ALL_WEEKDAYS as ACCESS_ALL_WEEKDAYS,
)
from security_diagnosis_harness.domain.access import (
    AccessControllerHealth,
    AccessControllerSnapshot,
    AccessControllerStatus,
    AccessDecision,
    AccessDenyReason,
    AccessEvent,
    AccessPolicySnapshot,
    AccessTimeRange,
    CredentialSnapshot,
    CredentialStatus,
    CredentialType,
    DoorLockStatus,
    DoorSnapshot,
    DoorStatus,
    is_access_sensitive_key,
    redact_access_sensitive_values,
)
from security_diagnosis_harness.domain.access import (
    Weekday as AccessWeekday,
)
from security_diagnosis_harness.domain.alarm import (
    ALL_WEEKDAYS as ALARM_ALL_WEEKDAYS,
)
from security_diagnosis_harness.domain.alarm import (
    AlarmCorrelationSnapshot,
    AlarmEnvironmentSnapshot,
    AlarmRuleSensitivity,
    AlarmRuleSnapshot,
    AlarmSeverityLevel,
    AlarmSignalSnapshot,
    AlarmSignalStatus,
    AlarmTimeRange,
    AlarmType,
    AlarmVerificationSnapshot,
    CorrelationPattern,
    EnvironmentInterferenceType,
    VerificationResult,
    is_alarm_sensitive_key,
    redact_alarm_sensitive_values,
)
from security_diagnosis_harness.domain.alarm import (
    Weekday as AlarmWeekday,
)
from security_diagnosis_harness.domain.camera import (
    ChannelSnapshot,
    ChannelStatus,
    PlatformPullStatus,
    PullStatus,
    StreamKind,
    StreamSnapshot,
)
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.common import new_id, utc_now
from security_diagnosis_harness.domain.conclusion import (
    ConclusionConfidence,
    DiagnosisConclusion,
)
from security_diagnosis_harness.domain.device import (
    AlarmSeverity,
    Device,
    DeviceAlarmEvent,
    DeviceConfigSnapshot,
    DeviceSnapshot,
    DeviceType,
    RecordingStatus,
    StreamStatus,
)
from security_diagnosis_harness.domain.enums import (
    SecurityDiagnosisStatus,
    SecurityFaultType,
)
from security_diagnosis_harness.domain.evidence import (
    DiagnosisEvidence,
    EvidenceSource,
    EvidenceType,
    Reliability,
)
from security_diagnosis_harness.domain.recording import (
    PlaybackCheckResult,
    PlaybackStatus,
    RecordingMode,
    RecordingPlanSnapshot,
    RecordingPlanStatus,
    RecordingTimeRange,
    StorageSnapshot,
    StorageStatus,
)
from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction

__all__ = [
    "ACCESS_ALL_WEEKDAYS",
    "ALARM_ALL_WEEKDAYS",
    "AccessControllerHealth",
    "AccessControllerSnapshot",
    "AccessControllerStatus",
    "AccessDecision",
    "AccessDenyReason",
    "AccessEvent",
    "AccessPolicySnapshot",
    "AccessTimeRange",
    "AccessWeekday",
    "AlarmCorrelationSnapshot",
    "AlarmEnvironmentSnapshot",
    "AlarmRuleSensitivity",
    "AlarmRuleSnapshot",
    "AlarmSeverity",
    "AlarmSeverityLevel",
    "AlarmSignalSnapshot",
    "AlarmSignalStatus",
    "AlarmTimeRange",
    "AlarmType",
    "AlarmVerificationSnapshot",
    "AlarmWeekday",
    "ChannelSnapshot",
    "ChannelStatus",
    "ConclusionConfidence",
    "CorrelationPattern",
    "CredentialSnapshot",
    "CredentialStatus",
    "CredentialType",
    "Device",
    "DeviceAlarmEvent",
    "DeviceConfigSnapshot",
    "DeviceSnapshot",
    "DeviceType",
    "DiagnosisConclusion",
    "DiagnosisEvidence",
    "DoorLockStatus",
    "DoorSnapshot",
    "DoorStatus",
    "EvidenceSource",
    "EvidenceType",
    "EnvironmentInterferenceType",
    "HumanReview",
    "HumanReviewAction",
    "PlaybackCheckResult",
    "PlaybackStatus",
    "PlatformPullStatus",
    "PullStatus",
    "RecordingMode",
    "RecordingPlanSnapshot",
    "RecordingPlanStatus",
    "RecordingStatus",
    "RecordingTimeRange",
    "Reliability",
    "SecurityDiagnosisCase",
    "SecurityDiagnosisStatus",
    "SecurityFaultType",
    "StorageSnapshot",
    "StorageStatus",
    "StreamKind",
    "StreamSnapshot",
    "StreamStatus",
    "VerificationResult",
    "is_access_sensitive_key",
    "is_alarm_sensitive_key",
    "new_id",
    "redact_access_sensitive_values",
    "redact_alarm_sensitive_values",
    "utc_now",
]
