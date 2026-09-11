"""追加式安全审计事件。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import new_id, utc_now
from security_diagnosis_harness.domain.redaction import redact_mapping, redact_text


class AuditEntityType(StrEnum):
    DIAGNOSIS = "diagnosis"
    KNOWLEDGE = "knowledge"


class AuditOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AuditEvent(BaseModel):
    """不可变更的业务动作摘要；不保存设备事实正文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=lambda: new_id("aud"))
    entity_type: AuditEntityType
    entity_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=80)
    outcome: AuditOutcome = AuditOutcome.SUCCEEDED
    actor: str = Field(default="system", min_length=1, max_length=80)
    previous_state: str | None = Field(default=None, max_length=64)
    current_state: str | None = Field(default=None, max_length=64)
    previous_version: int | None = Field(default=None, ge=0)
    current_version: int | None = Field(default=None, ge=0)
    summary: str = Field(default="", max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _sanitize(self) -> AuditEvent:
        actor, _ = redact_text(self.actor)
        summary, _ = redact_text(self.summary)
        metadata, _ = redact_mapping(self.metadata)
        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "metadata", metadata)
        return self
