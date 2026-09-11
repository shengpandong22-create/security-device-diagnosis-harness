"""Phase 5C-1 知识仓储与状态治理。"""

import pytest

from security_diagnosis_harness.adapters.knowledge.in_memory import (
    InMemoryKnowledgeRepository,
    KnowledgeNotFoundError,
)
from security_diagnosis_harness.application.errors import KnowledgeAlreadyExistsError
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeReview,
    KnowledgeReviewAction,
)


def _candidate(label: str = "device_offline") -> KnowledgeCandidate:
    return KnowledgeCandidate(
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        candidate_label=label,
        title="摄像头离线黑屏",
        summary="摄像头网络不可达导致预览黑屏",
        symptoms=["画面无法预览"],
        root_cause="设备离线或网络中断",
        troubleshooting_steps=["检查供电和网络"],
        source_diagnosis_id="diag-1",
        source_conclusion_id="con-1",
        source_evidence_ids=["evd-1"],
    )


def _confirm(candidate: KnowledgeCandidate) -> KnowledgeCandidate:
    candidate.apply_review(
        KnowledgeReview(
            knowledge_id=candidate.knowledge_id,
            action=KnowledgeReviewAction.CONFIRM,
            reviewer="expert",
        )
    )
    return candidate


def test_repository_round_trip_uses_deep_copies():
    repository = InMemoryKnowledgeRepository()
    candidate = repository.save(_candidate())
    candidate.title = "外部修改"

    assert repository.get(candidate.knowledge_id).title == "摄像头离线黑屏"


def test_repository_rejects_duplicate_id():
    repository = InMemoryKnowledgeRepository()
    candidate = _candidate()
    repository.save(candidate)

    with pytest.raises(KnowledgeAlreadyExistsError):
        repository.save(candidate)


def test_repository_get_and_update_unknown_fail():
    repository = InMemoryKnowledgeRepository()
    with pytest.raises(KnowledgeNotFoundError):
        repository.get("missing")
    with pytest.raises(KnowledgeNotFoundError):
        repository.update(_candidate())


@pytest.mark.parametrize("terminal_action", [None, KnowledgeReviewAction.REJECT])
def test_candidate_and_rejected_knowledge_are_not_recalled(terminal_action):
    repository = InMemoryKnowledgeRepository()
    candidate = _candidate()
    if terminal_action:
        candidate.apply_review(
            KnowledgeReview(
                knowledge_id=candidate.knowledge_id,
                action=terminal_action,
                reviewer="expert",
            )
        )
    repository.save(candidate)

    assert repository.search_confirmed(
        "网络 黑屏", SecurityFaultType.CAMERA_BLACK_SCREEN
    ) == []


def test_retired_knowledge_is_not_recalled():
    repository = InMemoryKnowledgeRepository()
    candidate = _confirm(_candidate())
    candidate.apply_review(
        KnowledgeReview(
            knowledge_id=candidate.knowledge_id,
            action=KnowledgeReviewAction.RETIRE,
            reviewer="expert",
        )
    )
    repository.save(candidate)

    assert repository.search_confirmed(
        "网络 黑屏", SecurityFaultType.CAMERA_BLACK_SCREEN
    ) == []


def test_only_matching_fault_type_confirmed_knowledge_is_recalled():
    repository = InMemoryKnowledgeRepository()
    confirmed = _confirm(_candidate())
    repository.save(confirmed)

    assert repository.search_confirmed(
        "网络 黑屏", SecurityFaultType.CAMERA_BLACK_SCREEN
    )[0].knowledge_id == confirmed.knowledge_id
    assert repository.search_confirmed(
        "网络 黑屏", SecurityFaultType.RECORDING_MISSING
    ) == []


def test_keyword_results_rank_more_matching_terms_first():
    repository = InMemoryKnowledgeRepository()
    weak = _confirm(_candidate("weak"))
    weak.title = "摄像头黑屏"
    strong = _confirm(_candidate("strong"))
    strong.knowledge_id = "knw-strong"
    repository.save(weak)
    repository.save(strong)

    results = repository.search_confirmed(
        "网络 黑屏", SecurityFaultType.CAMERA_BLACK_SCREEN
    )

    assert results[0].knowledge_id == strong.knowledge_id
