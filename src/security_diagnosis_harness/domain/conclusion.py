"""模型候选结论。

结论永远是"候选"，把诊断标记为 confirmed 的唯一途径是人工 review。
因此 `ConclusionConfidence` 中不存在 `confirmed` 等级。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.common import new_id, utc_now
from security_diagnosis_harness.domain.enums import SecurityFaultType


class ConclusionConfidence(StrEnum):
    """候选结论可信等级。

    - `possible`：依据较弱，例如主要来自知识库 SOP；
    - `probable`：至少引用了一条设备事实 Evidence。
    """

    POSSIBLE = "possible"
    PROBABLE = "probable"


class DiagnosisConclusion(BaseModel):
    """一次诊断的模型候选结论。"""

    model_config = ConfigDict(extra="forbid")

    conclusion_id: str = Field(default_factory=lambda: new_id("con"))
    diagnosis_id: str = Field(min_length=1)
    fault_type: SecurityFaultType
    summary: str = Field(min_length=1)
    root_cause: str | None = None
    confidence: ConclusionConfidence = ConclusionConfidence.POSSIBLE
    cited_evidence_ids: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    created_by: str = "model"
    created_at: datetime = Field(default_factory=utc_now)

    def belongs_to(self, diagnosis_id: str) -> bool:
        return self.diagnosis_id == diagnosis_id
