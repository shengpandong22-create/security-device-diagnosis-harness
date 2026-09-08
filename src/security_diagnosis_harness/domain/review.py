"""人工审核。

`HumanReview` 是唯一能把诊断推进到 `confirmed` 的动作来源。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.common import new_id, utc_now


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

    def belongs_to(self, diagnosis_id: str) -> bool:
        return self.diagnosis_id == diagnosis_id
