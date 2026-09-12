"""Phase 7D 争议案例的人工复核与数据集准入预检。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.redaction import redact_text
from security_diagnosis_harness.evaluation.dataset import (
    DatasetCase,
    DatasetProtocolError,
    DatasetRegistry,
    DatasetSplit,
)


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    NEEDS_REVISION = "needs_revision"


class DatasetAdmissionError(DatasetProtocolError):
    """争议案例尚不满足正式数据集准入条件。"""


class DatasetAdmissionReview(BaseModel):
    """人工复核记录；它只授权准入候选，不自动修改数据集文件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposed_case: DatasetCase
    decision: ReviewDecision
    reviewer: str = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _redact_review_text(self) -> DatasetAdmissionReview:
        reviewer, _ = redact_text(self.reviewer)
        rationale, _ = redact_text(self.rationale)
        object.__setattr__(self, "reviewer", reviewer)
        object.__setattr__(self, "rationale", rationale)
        return self


def validate_dataset_admission(
    review: DatasetAdmissionReview, registry: DatasetRegistry
) -> DatasetCase:
    """验证人工批准与跨集合隔离；不自动写入或改变版本。"""
    if review.decision is not ReviewDecision.APPROVE:
        raise DatasetAdmissionError("只有人工 approve 的案例才能成为数据集候选")
    # 防止调用方通过 model_copy 或构造后修改绕过 DatasetCase validator。
    try:
        proposed = DatasetCase.model_validate(review.proposed_case.model_dump(mode="python"))
    except ValueError as exc:
        raise DatasetAdmissionError("候选案例未通过安全协议复验") from exc
    datasets = {split: registry.cases(split, allow_test=True) for split in DatasetSplit}
    existing = [case for cases in datasets.values() for case in cases]
    if any(case.case_id == proposed.case_id for case in existing):
        raise DatasetAdmissionError("候选案例未通过数据集隔离检查：case_id 已存在")
    datasets[proposed.split] = (*datasets[proposed.split], proposed)
    try:
        DatasetRegistry(datasets)
    except DatasetProtocolError as exc:
        raise DatasetAdmissionError("候选案例未通过数据集隔离检查") from exc
    return proposed
