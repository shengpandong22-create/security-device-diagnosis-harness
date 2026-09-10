"""Phase 5A 知识候选领域模型验收。"""

from __future__ import annotations

import pydantic
import pytest

from security_diagnosis_harness.domain.device import REDACTED_VALUE
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.errors import (
    EvidenceDiagnosisMismatch,
    KnowledgeReviewNotAllowed,
)
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeCandidateSource,
    KnowledgeCandidateStatus,
    KnowledgeReview,
    KnowledgeReviewAction,
    is_knowledge_sensitive_key,
    redact_knowledge_sensitive_values,
)


def _candidate(**overrides) -> KnowledgeCandidate:
    data = {
        "fault_type": SecurityFaultType.CAMERA_BLACK_SCREEN,
        "candidate_label": "device_offline_or_network_unreachable",
        "title": "摄像头黑屏：设备离线",
        "summary": "设备离线导致平台无法获取视频流",
        "symptoms": ["平台预览黑屏", "设备状态离线"],
        "root_cause": "设备网络不可达或供电异常",
        "troubleshooting_steps": ["检查设备供电", "检查网络连通性"],
        "excluded_causes": ["平台拉流异常证据不足"],
        "source": KnowledgeCandidateSource.DIAGNOSIS_CONFIRMATION,
        "source_diagnosis_id": "diag-1",
        "source_conclusion_id": "conc-1",
        "source_evidence_ids": ["evd-1", "evd-2"],
    }
    data.update(overrides)
    return KnowledgeCandidate(**data)


def _review(
    knowledge_id: str,
    action: KnowledgeReviewAction,
    reviewer: str = "expert",
) -> KnowledgeReview:
    return KnowledgeReview(
        knowledge_id=knowledge_id,
        action=action,
        reviewer=reviewer,
        comment="人工审核通过",
    )


def test_candidate_can_be_created_with_traceable_source():
    candidate = _candidate()

    assert candidate.status is KnowledgeCandidateStatus.CANDIDATE
    assert candidate.fault_type is SecurityFaultType.CAMERA_BLACK_SCREEN
    assert candidate.source is KnowledgeCandidateSource.DIAGNOSIS_CONFIRMATION
    assert candidate.source_diagnosis_id == "diag-1"
    assert candidate.source_conclusion_id == "conc-1"
    assert candidate.source_evidence_ids == ["evd-1", "evd-2"]
    assert candidate.reviews == []


@pytest.mark.parametrize(
    "field_name",
    [
        "candidate_label",
        "title",
        "summary",
        "root_cause",
        "source_diagnosis_id",
        "source_conclusion_id",
    ],
)
def test_candidate_rejects_required_blank_fields(field_name):
    with pytest.raises(pydantic.ValidationError):
        _candidate(**{field_name: ""})


def test_candidate_requires_symptoms():
    with pytest.raises(pydantic.ValidationError):
        _candidate(symptoms=[])


def test_candidate_requires_troubleshooting_steps():
    with pytest.raises(pydantic.ValidationError):
        _candidate(troubleshooting_steps=[])


def test_candidate_requires_source_evidence_ids():
    with pytest.raises(pydantic.ValidationError):
        _candidate(source_evidence_ids=[])


def test_candidate_rejects_too_long_text_fields():
    with pytest.raises(pydantic.ValidationError):
        _candidate(summary="x" * 1001)


def test_candidate_rejects_too_long_list_item():
    with pytest.raises(pydantic.ValidationError, match="单项长度"):
        _candidate(symptoms=["x" * 501])


@pytest.mark.parametrize(
    "status",
    [
        KnowledgeCandidateStatus.CONFIRMED,
        KnowledgeCandidateStatus.REJECTED,
        KnowledgeCandidateStatus.RETIRED,
    ],
)
def test_candidate_cannot_start_as_non_candidate(status):
    with pytest.raises(KnowledgeReviewNotAllowed, match="初始状态只能是 candidate"):
        _candidate(status=status)


def test_confirm_review_produces_confirmed_knowledge():
    candidate = _candidate()
    review = _review(candidate.knowledge_id, KnowledgeReviewAction.CONFIRM)

    saved = candidate.apply_review(review)

    assert saved is review
    assert candidate.status is KnowledgeCandidateStatus.CONFIRMED
    assert candidate.reviews == [review]


def test_reject_review_produces_rejected_knowledge():
    candidate = _candidate()

    candidate.apply_review(_review(candidate.knowledge_id, KnowledgeReviewAction.REJECT))

    assert candidate.status is KnowledgeCandidateStatus.REJECTED


def test_retire_review_only_allowed_after_confirmed():
    candidate = _candidate()
    candidate.apply_review(_review(candidate.knowledge_id, KnowledgeReviewAction.CONFIRM))

    candidate.apply_review(_review(candidate.knowledge_id, KnowledgeReviewAction.RETIRE))

    assert candidate.status is KnowledgeCandidateStatus.RETIRED
    assert [item.action for item in candidate.reviews] == [
        KnowledgeReviewAction.CONFIRM,
        KnowledgeReviewAction.RETIRE,
    ]


def test_retire_candidate_without_confirm_is_rejected():
    candidate = _candidate()

    with pytest.raises(KnowledgeReviewNotAllowed, match="只有 confirmed 状态可以 retired"):
        candidate.apply_review(_review(candidate.knowledge_id, KnowledgeReviewAction.RETIRE))


def test_rejected_candidate_cannot_be_confirmed_again():
    candidate = _candidate()
    candidate.apply_review(_review(candidate.knowledge_id, KnowledgeReviewAction.REJECT))

    with pytest.raises(KnowledgeReviewNotAllowed, match="只有 candidate 状态可以确认"):
        candidate.apply_review(_review(candidate.knowledge_id, KnowledgeReviewAction.CONFIRM))


def test_retired_candidate_cannot_be_confirmed_again():
    candidate = _candidate()
    candidate.apply_review(_review(candidate.knowledge_id, KnowledgeReviewAction.CONFIRM))
    candidate.apply_review(_review(candidate.knowledge_id, KnowledgeReviewAction.RETIRE))

    with pytest.raises(KnowledgeReviewNotAllowed, match="只有 candidate 状态可以确认"):
        candidate.apply_review(_review(candidate.knowledge_id, KnowledgeReviewAction.CONFIRM))


def test_review_must_belong_to_candidate():
    candidate = _candidate()
    foreign_review = _review("other-knowledge", KnowledgeReviewAction.CONFIRM)

    with pytest.raises(EvidenceDiagnosisMismatch, match="不属于知识候选"):
        candidate.apply_review(foreign_review)


def test_review_records_reviewer_action_comment_and_time():
    review = _review("knw-1", KnowledgeReviewAction.CONFIRM, reviewer="teacher")

    assert review.knowledge_id == "knw-1"
    assert review.action is KnowledgeReviewAction.CONFIRM
    assert review.reviewer == "teacher"
    assert review.comment == "人工审核通过"
    assert review.reviewed_at is not None


def test_review_comment_is_redacted():
    review = KnowledgeReview(
        knowledge_id="knw-1",
        action=KnowledgeReviewAction.REJECT,
        reviewer="teacher",
        comment="password=plain-secret",
    )

    assert "plain-secret" not in review.comment
    assert REDACTED_VALUE in review.comment


def test_candidate_text_is_redacted():
    candidate = _candidate(
        summary="现场截图 http://example.local/snapshot.jpg 显示黑屏",
        root_cause="password=plain-secret 导致连接失败",
        symptoms=["Bearer token-value 被误写入描述"],
    )

    assert candidate.redacted is True
    assert "http://example.local/snapshot.jpg" not in candidate.summary
    assert "plain-secret" not in candidate.root_cause
    assert "token-value" not in candidate.symptoms[0]
    assert REDACTED_VALUE in str(candidate.model_dump())


def test_candidate_metadata_is_redacted_recursively():
    candidate = _candidate(
        metadata={
            "snapshot_url": "http://example.local/snapshot.jpg",
            "nested": {"card_no": "sample-card"},
            "items": [{"token": "sample-token"}],
        }
    )

    payload = str(candidate.metadata)

    assert candidate.redacted is True
    assert "http://example.local/snapshot.jpg" not in payload
    assert "sample-card" not in payload
    assert "sample-token" not in payload
    assert REDACTED_VALUE in payload


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "token",
        "secret",
        "card_no",
        "person_id",
        "license_plate",
        "snapshot_url",
    ],
)
def test_knowledge_sensitive_key_detection(key):
    assert is_knowledge_sensitive_key(key)


def test_redact_knowledge_sensitive_values_keeps_normal_values():
    payload, changed = redact_knowledge_sensitive_values(
        {"title": "摄像头黑屏", "normal": ["保留"]}
    )

    assert changed is False
    assert payload == {"title": "摄像头黑屏", "normal": ["保留"]}


def test_redact_knowledge_sensitive_values_redacts_nested_values():
    payload, changed = redact_knowledge_sensitive_values(
        {
            "summary": "访问 http://example.local/video.mp4",
            "nested": {"phone": "13800000000"},
        }
    )

    assert changed is True
    assert "http://example.local/video.mp4" not in str(payload)
    assert "13800000000" not in str(payload)
    assert REDACTED_VALUE in str(payload)


def test_knowledge_module_has_no_infrastructure_dependencies():
    source = (
        __import__("pathlib")
        .Path("src/security_diagnosis_harness/domain/knowledge.py")
        .read_text(encoding="utf-8")
    )

    forbidden = ("fastapi", "sqlalchemy", "alembic", "openai", "httpx", "requests")
    assert all(item not in source.lower() for item in forbidden)
