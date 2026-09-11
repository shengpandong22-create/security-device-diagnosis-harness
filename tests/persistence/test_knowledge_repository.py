"""Phase 6A：SQLite 知识仓储验收。

覆盖：状态治理（candidate / confirmed / rejected / retired）、fault_type 过滤、
评审与引用关系往返、重复与不存在的受控失败、读后本地修改不隐式落库、敏感文本不明文。
"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.adapters.knowledge.in_memory import KnowledgeNotFoundError
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeCandidateStatus,
    KnowledgeReview,
    KnowledgeReviewAction,
)
from tests.persistence._builders import build_knowledge_candidate


def test_save_and_get_round_trip(knowledge_repository):
    candidate = build_knowledge_candidate()
    knowledge_repository.save(candidate)
    loaded = knowledge_repository.get(candidate.knowledge_id)

    assert loaded.knowledge_id == candidate.knowledge_id
    assert loaded.title == candidate.title
    assert loaded.summary == candidate.summary
    assert loaded.root_cause == candidate.root_cause
    assert loaded.symptoms == candidate.symptoms
    assert loaded.troubleshooting_steps == candidate.troubleshooting_steps
    assert loaded.excluded_causes == candidate.excluded_causes


def test_fault_type_enum_is_restored(knowledge_repository):
    candidate = build_knowledge_candidate()
    knowledge_repository.save(candidate)

    assert (
        knowledge_repository.get(candidate.knowledge_id).fault_type
        is SecurityFaultType.CAMERA_BLACK_SCREEN
    )


def test_source_references_are_preserved(knowledge_repository):
    candidate = build_knowledge_candidate()
    knowledge_repository.save(candidate)
    loaded = knowledge_repository.get(candidate.knowledge_id)

    assert loaded.source_diagnosis_id == candidate.source_diagnosis_id
    assert loaded.source_conclusion_id == candidate.source_conclusion_id
    assert loaded.source_evidence_ids == candidate.source_evidence_ids


def test_knowledge_review_is_restored(knowledge_repository):
    candidate = build_knowledge_candidate(confirmed=True)
    knowledge_repository.save(candidate)
    loaded = knowledge_repository.get(candidate.knowledge_id)

    assert loaded.status is KnowledgeCandidateStatus.CONFIRMED
    assert len(loaded.reviews) == 1
    review = loaded.reviews[0]
    assert review.action is KnowledgeReviewAction.CONFIRM
    assert review.reviewer == "expert"


def test_candidate_is_saved_but_not_recalled(knowledge_repository):
    candidate = build_knowledge_candidate()
    knowledge_repository.save(candidate)

    assert knowledge_repository.get(candidate.knowledge_id).status is (
        KnowledgeCandidateStatus.CANDIDATE
    )
    assert (
        knowledge_repository.search_confirmed(
            "网络 黑屏", SecurityFaultType.CAMERA_BLACK_SCREEN
        )
        == []
    )


def test_confirmed_is_recalled(knowledge_repository):
    candidate = build_knowledge_candidate(confirmed=True)
    knowledge_repository.save(candidate)

    results = knowledge_repository.search_confirmed(
        "网络 黑屏", SecurityFaultType.CAMERA_BLACK_SCREEN
    )
    assert [item.knowledge_id for item in results] == [candidate.knowledge_id]


def test_rejected_is_not_recalled(knowledge_repository):
    candidate = build_knowledge_candidate()
    candidate.apply_review(
        KnowledgeReview(
            knowledge_id=candidate.knowledge_id,
            action=KnowledgeReviewAction.REJECT,
            reviewer="expert",
        )
    )
    knowledge_repository.save(candidate)

    assert (
        knowledge_repository.search_confirmed(
            "网络 黑屏", SecurityFaultType.CAMERA_BLACK_SCREEN
        )
        == []
    )


def test_retired_is_not_recalled(knowledge_repository):
    candidate = build_knowledge_candidate(confirmed=True)
    candidate.apply_review(
        KnowledgeReview(
            knowledge_id=candidate.knowledge_id,
            action=KnowledgeReviewAction.RETIRE,
            reviewer="expert",
        )
    )
    knowledge_repository.save(candidate)

    assert knowledge_repository.get(candidate.knowledge_id).status is (
        KnowledgeCandidateStatus.RETIRED
    )
    assert (
        knowledge_repository.search_confirmed(
            "网络 黑屏", SecurityFaultType.CAMERA_BLACK_SCREEN
        )
        == []
    )


def test_mismatched_fault_type_is_not_recalled(knowledge_repository):
    candidate = build_knowledge_candidate(confirmed=True)
    knowledge_repository.save(candidate)

    assert (
        knowledge_repository.search_confirmed(
            "网络 黑屏", SecurityFaultType.RECORDING_MISSING
        )
        == []
    )


def test_duplicate_save_is_rejected(knowledge_repository):
    candidate = build_knowledge_candidate()
    knowledge_repository.save(candidate)

    with pytest.raises(ValueError, match="已存在"):
        knowledge_repository.save(candidate)


def test_get_and_update_unknown_are_rejected(knowledge_repository):
    with pytest.raises(KnowledgeNotFoundError):
        knowledge_repository.get("missing")
    with pytest.raises(KnowledgeNotFoundError):
        knowledge_repository.update(build_knowledge_candidate("knw-missing"))


def test_local_mutation_does_not_implicitly_persist(knowledge_repository):
    candidate = build_knowledge_candidate()
    knowledge_repository.save(candidate)

    loaded = knowledge_repository.get(candidate.knowledge_id)
    loaded.title = "外部改坏了"

    assert knowledge_repository.get(candidate.knowledge_id).title == candidate.title


def test_list_all_returns_every_status(knowledge_repository):
    candidate = build_knowledge_candidate("knw-a")
    confirmed = build_knowledge_candidate("knw-b", confirmed=True)
    knowledge_repository.save(candidate)
    knowledge_repository.save(confirmed)

    ids = {item.knowledge_id for item in knowledge_repository.list_all()}
    assert ids == {"knw-a", "knw-b"}


def test_sensitive_text_is_not_stored_in_plaintext(engine, knowledge_repository):
    from sqlalchemy import select

    from security_diagnosis_harness.adapters.persistence.models import KnowledgeCandidateRow
    from security_diagnosis_harness.domain.device import REDACTED_VALUE

    candidate = KnowledgeCandidate(
        knowledge_id="knw-secret",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        candidate_label="device_offline",
        title="摄像头离线黑屏",
        summary="凭据 password=super-secret-password 出现在摘要中",
        symptoms=["画面无法预览"],
        root_cause="设备离线",
        troubleshooting_steps=["检查网络"],
        source_diagnosis_id="diag-1",
        source_conclusion_id="con-1",
        source_evidence_ids=["evd-1"],
    )
    knowledge_repository.save(candidate)

    with engine.connect() as connection:
        raw_summary = connection.execute(
            select(KnowledgeCandidateRow.summary)
        ).scalar_one()

    assert "super-secret-password" not in raw_summary
    assert REDACTED_VALUE in raw_summary


def test_repository_satisfies_knowledge_port(knowledge_repository):
    from security_diagnosis_harness.ports.knowledge_repository import KnowledgeRepository

    assert isinstance(knowledge_repository, KnowledgeRepository)
