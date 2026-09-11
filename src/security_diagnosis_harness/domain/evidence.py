"""诊断证据。

Evidence 必须属于某个 Diagnosis，并且具备类型、来源、hash、可信度、脱敏状态。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import content_hash, new_id, utc_now
from security_diagnosis_harness.domain.redaction import redact_mapping, redact_text


class EvidenceType(StrEnum):
    """证据类型。

    `DEVICE_CHANNEL` / `DEVICE_STREAM` / `PLATFORM_PULL` 为 Phase 1 摄像头黑屏深化新增，
    它们同属于设备事实类证据。

    `RECORDING_PLAN` / `STORAGE_STATUS` / `PLAYBACK_CHECK` 为 Phase 2B 录像缺失深化新增。

    `ACCESS_CONTROLLER` / `ACCESS_DOOR` / `ACCESS_CREDENTIAL` /
    `ACCESS_POLICY` / `ACCESS_EVENT` 为 Phase 3B 门禁刷卡异常深化新增。

    `ALARM_RULE` / `ALARM_SIGNAL` / `ALARM_ENVIRONMENT` /
    `ALARM_VERIFICATION` / `ALARM_CORRELATION` 为 Phase 4B 报警误报深化新增。
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
    ACCESS_CONTROLLER = "access_controller"
    ACCESS_DOOR = "access_door"
    ACCESS_CREDENTIAL = "access_credential"
    ACCESS_POLICY = "access_policy"
    ACCESS_EVENT = "access_event"
    ALARM_RULE = "alarm_rule"
    ALARM_SIGNAL = "alarm_signal"
    ALARM_ENVIRONMENT = "alarm_environment"
    ALARM_VERIFICATION = "alarm_verification"
    ALARM_CORRELATION = "alarm_correlation"
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
    def _redact_then_hash(self) -> DiagnosisEvidence:
        """先脱敏，再基于**最终内容**重新计算 content_hash。

        顺序不可颠倒：`content_hash` 必须对应实际持久化的脱敏内容。

        关键点：每次 Domain 校验都重新计算 hash，而不是「非空就沿用」。
        这样对象在构造后被就地修改（例如 `payload["password"] = ...`）时，
        重新 `model_validate` 得到的哈希会反映脱敏后的新内容，
        不会出现「payload 已变化但 hash 仍是旧值」。
        """
        changed = False
        cleaned_summary, summary_changed = redact_text(self.summary)
        if summary_changed:
            self.summary = cleaned_summary
            changed = True

        cleaned_payload, payload_changed = redact_mapping(self.payload)
        if payload_changed:
            self.payload = cleaned_payload
            changed = True

        if changed:
            self.redacted = True

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
