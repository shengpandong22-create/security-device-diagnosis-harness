"""人工审核。

`HumanReview` 是唯一能把诊断推进到 `confirmed` 的动作来源。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.common import new_id, utc_now
from security_diagnosis_harness.domain.redaction import redact_text


class HumanReviewAction(StrEnum):
    """人工审核动作。"""

    CONFIRM = "confirm"
    REJECT = "reject"
    REQUEST_MORE_INFO = "request_more_info"


class HumanReview(BaseModel):
    """一条人工审核记录。"""

    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(default_factory=lambda: new_id("rev"))
    diagnosis_id: str = Field(min_length=1)
    action: HumanReviewAction
    reviewer: str = Field(min_length=1)
    comment: str = ""
    reviewed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _redact_comment(self) -> HumanReview:
        """审核意见是自由文本，进入领域对象前先脱敏。"""
        self.comment, _ = redact_text(self.comment)
        return self

    def belongs_to(self, diagnosis_id: str) -> bool:
        return self.diagnosis_id == diagnosis_id
