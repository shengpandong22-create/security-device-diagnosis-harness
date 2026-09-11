"""confirmed knowledge 的关键词 + 语义混合检索。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeCandidateStatus,
    redact_knowledge_text,
)
from security_diagnosis_harness.domain.knowledge_retrieval import (
    MIN_LEXICAL_OVERLAP,
    lexical_overlap_score,
)
from security_diagnosis_harness.ports.embedding import EmbeddingPort
from security_diagnosis_harness.ports.knowledge_repository import KnowledgeRepository


@dataclass(frozen=True)
class RetrievalDiagnostics:
    mode: str
    semantic_fallback: bool = False


class HybridKnowledgeRetriever:
    """以 RRF 融合词法与向量排名，向量失败时确定性降级。"""

    def __init__(
        self,
        repository: KnowledgeRepository,
        embedding: EmbeddingPort,
        *,
        rrf_k: int = 60,
        min_semantic_score: float = 0.35,
    ) -> None:
        self._repository = repository
        self._embedding = embedding
        self._rrf_k = rrf_k
        self._min_semantic_score = min_semantic_score
        self._document_vector_cache: dict[str, list[float]] = {}
        self.last_diagnostics = RetrievalDiagnostics(mode="not_run")

    def search_confirmed(
        self,
        query: str,
        fault_type: SecurityFaultType,
        limit: int = 3,
    ) -> list[KnowledgeCandidate]:
        safe_query, _ = redact_knowledge_text(query)
        candidates = [
            item
            for item in self._repository.list_all()
            if item.status is KnowledgeCandidateStatus.CONFIRMED
            and item.fault_type is fault_type
        ]
        if not candidates:
            self.last_diagnostics = RetrievalDiagnostics(mode="hybrid")
            return []

        keyword_rank = _keyword_rank(safe_query, candidates)
        try:
            semantic_rank = self._semantic_rank(safe_query, candidates)
        except Exception:
            self.last_diagnostics = RetrievalDiagnostics(
                mode="keyword", semantic_fallback=True
            )
            return keyword_rank[:limit]

        scores: dict[str, float] = {}
        by_id = {item.knowledge_id: item for item in candidates}
        # 设备错误码和明确领域词应略高于语义近似，因此词法路权重稍高。
        for weight, ranking in ((1.2, keyword_rank), (1.0, semantic_rank)):
            for rank, item in enumerate(ranking, start=1):
                scores[item.knowledge_id] = scores.get(item.knowledge_id, 0.0) + weight / (
                    self._rrf_k + rank
                )
        ordered_ids = sorted(scores, key=lambda item_id: (-scores[item_id], item_id))
        self.last_diagnostics = RetrievalDiagnostics(mode="hybrid")
        return [by_id[item_id] for item_id in ordered_ids[:limit]]

    def _semantic_rank(
        self,
        query: str,
        candidates: list[KnowledgeCandidate],
    ) -> list[KnowledgeCandidate]:
        query_vector = self._embedding.embed_query(query)
        vectors = _candidate_vectors(
            candidates, self._embedding, self._document_vector_cache
        )
        if len(vectors) != len(candidates):
            raise RuntimeError("embedding result count mismatch")
        scored = [
            (_cosine_similarity(query_vector, vector), item)
            for item, vector in zip(candidates, vectors, strict=True)
        ]
        scored = [pair for pair in scored if pair[0] >= self._min_semantic_score]
        scored.sort(key=lambda pair: (-pair[0], pair[1].knowledge_id))
        return [item for _, item in scored]


class SemanticKnowledgeRetriever:
    """纯向量检索基线，用于与 Keyword / Hybrid 做同变量评测。"""

    def __init__(
        self,
        repository: KnowledgeRepository,
        embedding: EmbeddingPort,
        *,
        min_semantic_score: float = 0.35,
    ) -> None:
        self._repository = repository
        self._embedding = embedding
        self._min_semantic_score = min_semantic_score
        self._document_vector_cache: dict[str, list[float]] = {}

    def search_confirmed(
        self,
        query: str,
        fault_type: SecurityFaultType,
        limit: int = 3,
    ) -> list[KnowledgeCandidate]:
        safe_query, _ = redact_knowledge_text(query)
        candidates = [
            item
            for item in self._repository.list_all()
            if item.status is KnowledgeCandidateStatus.CONFIRMED
            and item.fault_type is fault_type
        ]
        query_vector = self._embedding.embed_query(safe_query)
        vectors = _candidate_vectors(
            candidates, self._embedding, self._document_vector_cache
        )
        if len(vectors) != len(candidates):
            raise RuntimeError("embedding result count mismatch")
        scored = [
            (_cosine_similarity(query_vector, vector), item)
            for item, vector in zip(candidates, vectors, strict=True)
        ]
        scored = [pair for pair in scored if pair[0] >= self._min_semantic_score]
        scored.sort(key=lambda pair: (-pair[0], pair[1].knowledge_id))
        return [item for _, item in scored[:limit]]


def _candidate_text(candidate: KnowledgeCandidate) -> str:
    return " ".join(
        [
            candidate.candidate_label,
            candidate.title,
            candidate.summary,
            candidate.root_cause,
            *candidate.symptoms,
            *candidate.troubleshooting_steps,
        ]
    )


def _candidate_vectors(
    candidates: list[KnowledgeCandidate],
    embedding: EmbeddingPort,
    cache: dict[str, list[float]],
) -> list[list[float]]:
    """缓存稳定知识的文档向量，知识更新时间变化时自动失效。"""
    keys = [f"{item.knowledge_id}:{item.updated_at.isoformat()}" for item in candidates]
    missing = [
        (key, item)
        for key, item in zip(keys, candidates, strict=True)
        if key not in cache
    ]
    if missing:
        vectors = embedding.embed_documents(
            [_candidate_text(item) for _, item in missing]
        )
        if len(vectors) != len(missing):
            raise RuntimeError("embedding result count mismatch")
        for (key, _), vector in zip(missing, vectors, strict=True):
            cache[key] = vector
    return [cache[key] for key in keys]


def _keyword_rank(
    query: str,
    candidates: list[KnowledgeCandidate],
) -> list[KnowledgeCandidate]:
    scored: list[tuple[int, KnowledgeCandidate]] = []
    for item in candidates:
        haystack = _candidate_text(item).lower()
        score = lexical_overlap_score(query, haystack)
        if not query.strip() or score >= MIN_LEXICAL_OVERLAP:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1].knowledge_id))
    return [item for _, item in scored]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise RuntimeError("embedding dimension mismatch")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
