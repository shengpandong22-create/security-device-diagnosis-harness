"""诊断证据。

Evidence 必须属于某个 Diagnosis，并且具备类型、来源、hash、可信度、脱敏状态。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import content_hash, new_id, utc_now


class EvidenceType(StrEnum):
    """证据类型。

    `DEVICE_CHANNEL` / `DEVICE_STREAM` / `PLATFORM_PULL` 为 Phase 1 摄像头黑屏深化新增，
    它们同属于设备事实类证据。

    `RECORDING_PLAN` / `STORAGE_STATUS` / `PLAYBACK_CHECK` 为 Phase 2B 录像缺失深化新增。
    """

    DEVICE_STATUS = "device_status"
    DEVICE_ALARM = "device_alarm"
    DEVICE_CONFIG = "device_config"
    DEVICE_CHANNEL = "device_channel"
    DEVICE_STREAM = "device_stream"
    PLATFORM_PULL = "platform_pull"
    RECORDING_PLAN = "recording_plan"
    STORAGE_STATUS = "storage_status"
    PLAYBACK_CHECK = "playback_check"
    KNOWLEDGE_SOP = "knowledge_sop"
    HUMAN_FEEDBACK = "human_feedback"


class EvidenceSource(StrEnum):
    """证据来源。"""

    DEVICE_GATEWAY = "device_gateway"
    KNOWLEDGE_BASE = "knowledge_base"
    HUMAN = "human"


class Reliability(StrEnum):
    """证据可信度。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DiagnosisEvidence(BaseModel):
    """一条诊断证据，必须挂在一个诊断用例上。"""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(default_factory=lambda: new_id("evd"))
    diagnosis_id: str = Field(min_length=1)
    evidence_type: EvidenceType
    source: EvidenceSource
    summary: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    captured_at: datetime = Field(default_factory=utc_now)
    reliability: Reliability = Reliability.MEDIUM
    redacted: bool = False
    content_hash: str = ""

    @model_validator(mode="after")
    def _ensure_content_hash(self) -> DiagnosisEvidence:
        if not self.content_hash:
            self.content_hash = content_hash(
                {
                    "evidence_type": self.evidence_type.value,
                    "source": self.source.value,
                    "summary": self.summary,
                    "payload": self.payload,
                }
            )
        return self

    def belongs_to(self, diagnosis_id: str) -> bool:
        return self.diagnosis_id == diagnosis_id
