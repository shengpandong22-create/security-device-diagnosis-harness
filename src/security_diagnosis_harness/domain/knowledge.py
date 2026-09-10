"""知识候选领域模型。

Phase 5A 只定义知识沉淀的领域边界，不接数据库、不改工具、不做检索：

- 系统可以从已确认诊断中提取候选知识；
- 自动流程只能生成 `candidate`；
- `confirmed` knowledge 只能由人工审核产生；
- 知识内容只保存可复用摘要和 Evidence ID 引用，不保存完整 Evidence payload。
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from security_diagnosis_harness.domain.access import is_access_sensitive_key
from security_diagnosis_harness.domain.alarm import is_alarm_sensitive_key
from security_diagnosis_harness.domain.common import new_id, utc_now
from security_diagnosis_harness.domain.device import (
    REDACTED_VALUE,
    is_sensitive_key,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.errors import (
    EvidenceDiagnosisMismatch,
    KnowledgeReviewNotAllowed,
)

TEXT_MAX_LENGTH = 1_000
TITLE_MAX_LENGTH = 120
LIST_ITEM_MAX_LENGTH = 500

_SENSITIVE_TEXT_PATTERN = re.compile(
    r"https?://\S+"
    r"|AKIA[0-9A-Z]{16}"
    r"|sk-[A-Za-z0-9_-]{12,}"
    r"|Bearer\s+[A-Za-z0-9._-]+"
    r"|(?:password|passwd|pwd|token|secret)\s*[:=]\s*\S+"
    r"|(?:card[_-]?(?:no|number|id)|person[_-]?id|id[_-]?card"
    r"|face[_-]?id|license[_-]?plate)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


class KnowledgeCandidateStatus(StrEnum):
    """知识候选生命周期状态。"""

    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    RETIRED = "retired"


class KnowledgeCandidateSource(StrEnum):
    """知识候选来源。"""

    DIAGNOSIS_CONFIRMATION = "diagnosis_confirmation"
    MANUAL_SEED = "manual_seed"
    IMPORTED = "imported"


class KnowledgeReviewAction(StrEnum):
    """知识候选人工审核动作。"""

    CONFIRM = "confirm"
    REJECT = "reject"
    RETIRE = "retire"


def is_knowledge_sensitive_key(key: str) -> bool:
    """判断知识字段中的键名是否需要脱敏。"""
    return (
        is_sensitive_key(key)
        or is_access_sensitive_key(key)
        or is_alarm_sensitive_key(key)
    )


def _redact_text(value: str) -> tuple[str, bool]:
    cleaned = _SENSITIVE_TEXT_PATTERN.sub(REDACTED_VALUE, value)
    return cleaned, cleaned != value


def _redact_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        changed = False
        items: list[Any] = []
        for item in value:
            cleaned, item_changed = _redact_value(item)
            items.append(cleaned)
            changed = changed or item_changed
        return items, changed
    if isinstance(value, dict):
        return redact_knowledge_sensitive_values(value)
    return value, False


def redact_knowledge_sensitive_values(values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """递归脱敏知识候选中的敏感键和值。"""
    redacted: dict[str, Any] = {}
    changed = False
    for key, value in values.items():
        if value in (None, ""):
            redacted[key] = value
        elif is_knowledge_sensitive_key(str(key)):
            redacted[key] = REDACTED_VALUE
            changed = True
        else:
            cleaned, item_changed = _redact_value(value)
            redacted[key] = cleaned
            changed = changed or item_changed
    return redacted, changed


class KnowledgeReview(BaseModel):
    """一条知识候选人工审核记录。"""

    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(default_factory=lambda: new_id("krev"))
    knowledge_id: str = Field(min_length=1)
    action: KnowledgeReviewAction
    reviewer: str = Field(min_length=1, max_length=80)
    comment: str = Field(default="", max_length=TEXT_MAX_LENGTH)
    reviewed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _redact_comment(self) -> KnowledgeReview:
        cleaned, changed = _redact_text(self.comment)
        if changed:
            self.comment = cleaned
        return self

    def belongs_to(self, knowledge_id: str) -> bool:
        return self.knowledge_id == knowledge_id


class KnowledgeCandidate(BaseModel):
    """一条可审核的知识候选。"""

    model_config = ConfigDict(extra="forbid")

    knowledge_id: str = Field(default_factory=lambda: new_id("knw"))
    fault_type: SecurityFaultType
    candidate_label: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    summary: str = Field(min_length=1, max_length=TEXT_MAX_LENGTH)
    symptoms: list[str] = Field(min_length=1, default_factory=list)
    root_cause: str = Field(min_length=1, max_length=TEXT_MAX_LENGTH)
    troubleshooting_steps: list[str] = Field(min_length=1, default_factory=list)
    excluded_causes: list[str] = Field(default_factory=list)
    source: KnowledgeCandidateSource = KnowledgeCandidateSource.DIAGNOSIS_CONFIRMATION
    source_diagnosis_id: str = Field(min_length=1)
    source_conclusion_id: str = Field(min_length=1)
    source_evidence_ids: list[str] = Field(min_length=1)
    status: KnowledgeCandidateStatus = KnowledgeCandidateStatus.CANDIDATE
    reviews: list[KnowledgeReview] = Field(default_factory=list)
    redacted: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _validate_and_redact(self) -> KnowledgeCandidate:
        if self.status is not KnowledgeCandidateStatus.CANDIDATE and not self.reviews:
            raise KnowledgeReviewNotAllowed(
                "知识候选初始状态只能是 candidate，confirmed/rejected/retired 必须由人工审核产生"
            )

        changed = False
        self.title, item_changed = _redact_text(self.title)
        changed = changed or item_changed
        self.summary, item_changed = _redact_text(self.summary)
        changed = changed or item_changed
        self.root_cause, item_changed = _redact_text(self.root_cause)
        changed = changed or item_changed

        self.symptoms, item_changed = _redact_text_list(self.symptoms, "symptoms")
        changed = changed or item_changed
        self.troubleshooting_steps, item_changed = _redact_text_list(
            self.troubleshooting_steps, "troubleshooting_steps"
        )
        changed = changed or item_changed
        self.excluded_causes, item_changed = _redact_text_list(
            self.excluded_causes, "excluded_causes"
        )
        changed = changed or item_changed

        self.metadata, item_changed = redact_knowledge_sensitive_values(self.metadata)
        changed = changed or item_changed

        if changed:
            self.redacted = True
        return self

    def apply_review(self, review: KnowledgeReview) -> KnowledgeReview:
        """执行知识审核。confirmed knowledge 只能由这里产生。"""
        if not review.belongs_to(self.knowledge_id):
            raise EvidenceDiagnosisMismatch(
                f"review {review.review_id} 不属于知识候选 {self.knowledge_id}"
            )

        if review.action is KnowledgeReviewAction.CONFIRM:
            if self.status is not KnowledgeCandidateStatus.CANDIDATE:
                raise KnowledgeReviewNotAllowed(
                    f"只有 candidate 状态可以确认，当前状态 {self.status.value}"
                )
            target = KnowledgeCandidateStatus.CONFIRMED
        elif review.action is KnowledgeReviewAction.REJECT:
            if self.status is not KnowledgeCandidateStatus.CANDIDATE:
                raise KnowledgeReviewNotAllowed(
                    f"只有 candidate 状态可以驳回，当前状态 {self.status.value}"
                )
            target = KnowledgeCandidateStatus.REJECTED
        else:
            if self.status is not KnowledgeCandidateStatus.CONFIRMED:
                raise KnowledgeReviewNotAllowed(
                    f"只有 confirmed 状态可以 retired，当前状态 {self.status.value}"
                )
            target = KnowledgeCandidateStatus.RETIRED

        self.reviews.append(review)
        self.status = target
        self._touch()
        return review

    def _touch(self) -> None:
        self.updated_at = utc_now()


def _redact_text_list(values: list[str], field_name: str) -> tuple[list[str], bool]:
    changed = False
    cleaned_values: list[str] = []
    for value in values:
        if len(value) > LIST_ITEM_MAX_LENGTH:
            raise ValueError(f"{field_name} 单项长度不能超过 {LIST_ITEM_MAX_LENGTH}")
        cleaned, item_changed = _redact_text(value)
        cleaned_values.append(cleaned)
        changed = changed or item_changed
    return cleaned_values, changed
