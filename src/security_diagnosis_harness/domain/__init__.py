"""安防设备诊断领域模型。

领域层只允许依赖标准库与 pydantic，禁止依赖 FastAPI、SQLAlchemy、
HTTP 客户端或具体 LLM SDK。
"""

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
from security_diagnosis_harness.domain.review import HumanReview, HumanReviewAction

__all__ = [
    "AlarmSeverity",
    "ConclusionConfidence",
    "Device",
    "DeviceAlarmEvent",
    "DeviceConfigSnapshot",
    "DeviceSnapshot",
    "DeviceType",
    "DiagnosisConclusion",
    "DiagnosisEvidence",
    "EvidenceSource",
    "EvidenceType",
    "HumanReview",
    "HumanReviewAction",
    "RecordingStatus",
    "Reliability",
    "SecurityDiagnosisCase",
    "SecurityDiagnosisStatus",
    "SecurityFaultType",
    "StreamStatus",
    "new_id",
    "utc_now",
]
