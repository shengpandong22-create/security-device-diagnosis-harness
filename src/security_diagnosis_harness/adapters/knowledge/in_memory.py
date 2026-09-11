"""用于本地闭环和测试的内存知识仓储。"""

from __future__ import annotations

from copy import deepcopy

from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeCandidateStatus,
)
from security_diagnosis_harness.domain.knowledge_retrieval import (
    MIN_LEXICAL_OVERLAP,
    lexical_overlap_score,
)


class KnowledgeNotFoundError(KeyError):
    """知识候选不存在。"""


class InMemoryKnowledgeRepository:
    """通过深拷贝隔离调用方，并在检索边界强制状态治理。"""

    def __init__(self) -> None:
        self._items: dict[str, KnowledgeCandidate] = {}

    def save(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        if candidate.knowledge_id in self._items:
            raise ValueError(f"知识 {candidate.knowledge_id} 已存在")
        self._items[candidate.knowledge_id] = deepcopy(candidate)
        return deepcopy(candidate)

    def get(self, knowledge_id: str) -> KnowledgeCandidate:
        try:
            return deepcopy(self._items[knowledge_id])
        except KeyError as exc:
            raise KnowledgeNotFoundError(f"知识 {knowledge_id} 不存在") from exc

    def update(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        if candidate.knowledge_id not in self._items:
            raise KnowledgeNotFoundError(f"知识 {candidate.knowledge_id} 不存在")
        self._items[candidate.knowledge_id] = deepcopy(candidate)
        return deepcopy(candidate)

    def list_all(self) -> list[KnowledgeCandidate]:
        return [deepcopy(item) for item in self._items.values()]

    def search_confirmed(
        self,
        query: str,
        fault_type: SecurityFaultType,
        limit: int = 3,
    ) -> list[KnowledgeCandidate]:
        """按故障类型和词项匹配，只召回人工确认知识。"""
        ranked: list[tuple[int, KnowledgeCandidate]] = []
        for item in self._items.values():
            if item.status is not KnowledgeCandidateStatus.CONFIRMED:
                continue
            if item.fault_type is not fault_type:
                continue
            haystack = " ".join(
                [
                    item.candidate_label,
                    item.title,
                    item.summary,
                    item.root_cause,
                    *item.symptoms,
                    *item.troubleshooting_steps,
                ]
            ).lower()
            score = lexical_overlap_score(query, haystack)
            if query.strip() and score < MIN_LEXICAL_OVERLAP:
                continue
            ranked.append((score, item))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].knowledge_id))
        return [deepcopy(item) for _, item in ranked[:limit]]
