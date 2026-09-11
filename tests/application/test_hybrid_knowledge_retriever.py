"""Phase 5C-3 混合检索与降级行为。"""

from security_diagnosis_harness.adapters.knowledge.in_memory import (
    InMemoryKnowledgeRepository,
)
from security_diagnosis_harness.application.hybrid_knowledge_retriever import (
    HybridKnowledgeRetriever,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeReview,
    KnowledgeReviewAction,
)


class SemanticEmbedding:
    dimension = 2

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0] if ("反应" in text or "响应" in text) else [0.0, 1.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0] if "控制器离线" in text else [0.0, 1.0]
            for text in texts
        ]


class FailingEmbedding(SemanticEmbedding):
    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("service unavailable")


def _confirmed(label: str, title: str, summary: str) -> KnowledgeCandidate:
    item = KnowledgeCandidate(
        fault_type=SecurityFaultType.ACCESS_CARD_FAILED,
        candidate_label=label,
        title=title,
        summary=summary,
        symptoms=[summary],
        root_cause=summary,
        troubleshooting_steps=["检查设备事实"],
        source_diagnosis_id=f"diag-{label}",
        source_conclusion_id=f"con-{label}",
        source_evidence_ids=[f"evd-{label}"],
    )
    item.apply_review(
        KnowledgeReview(
            knowledge_id=item.knowledge_id,
            action=KnowledgeReviewAction.CONFIRM,
            reviewer="expert",
        )
    )
    return item


def _repository() -> InMemoryKnowledgeRepository:
    repository = InMemoryKnowledgeRepository()
    repository.save(
        _confirmed("controller_offline", "控制器离线", "控制器离线导致刷卡无响应")
    )
    repository.save(_confirmed("permission_denied", "权限不足", "人员没有目标门权限"))
    return repository


def test_semantic_path_recalls_synonym_without_keyword_overlap():
    retriever = HybridKnowledgeRetriever(_repository(), SemanticEmbedding())

    results = retriever.search_confirmed(
        "读卡后毫无响应", SecurityFaultType.ACCESS_CARD_FAILED, 1
    )

    assert results[0].candidate_label == "controller_offline"
    assert retriever.last_diagnostics.mode == "hybrid"


def test_embedding_failure_falls_back_to_keyword_results():
    retriever = HybridKnowledgeRetriever(_repository(), FailingEmbedding())

    results = retriever.search_confirmed(
        "权限不足", SecurityFaultType.ACCESS_CARD_FAILED, 1
    )

    assert results[0].candidate_label == "permission_denied"
    assert retriever.last_diagnostics.semantic_fallback is True


def test_candidate_status_is_filtered_before_embedding():
    repository = _repository()
    candidate = _confirmed("unsafe", "不应召回", "控制器离线")
    candidate.status = "candidate"
    repository.save(candidate)
    retriever = HybridKnowledgeRetriever(repository, SemanticEmbedding())

    results = retriever.search_confirmed(
        "设备没反应", SecurityFaultType.ACCESS_CARD_FAILED, 10
    )

    assert all(item.candidate_label != "unsafe" for item in results)


def test_semantic_threshold_prevents_forced_irrelevant_match():
    retriever = HybridKnowledgeRetriever(
        _repository(), SemanticEmbedding(), min_semantic_score=1.1
    )

    results = retriever.search_confirmed(
        "完全无关的问题", SecurityFaultType.ACCESS_CARD_FAILED, 3
    )

    assert results == []
