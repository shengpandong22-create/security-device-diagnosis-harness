"""用于本地闭环和测试的内存知识仓储。"""

from __future__ import annotations

from copy import deepcopy
from threading import RLock

from security_diagnosis_harness.application.errors import (
    ConcurrentUpdateError,
    KnowledgeAlreadyExistsError,
    KnowledgeNotFoundError,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeCandidateStatus,
)
from security_diagnosis_harness.domain.knowledge_retrieval import (
    MIN_LEXICAL_OVERLAP,
    lexical_overlap_score,
)

# 兼容旧导入路径：`KnowledgeNotFoundError` 现在定义在 application.errors，
# 此处保留同名导出，历史调用者无需改动。
__all__ = ["InMemoryKnowledgeRepository", "KnowledgeNotFoundError"]

_ENTITY = "知识候选"


class InMemoryKnowledgeRepository:
    """通过深拷贝隔离调用方，并在检索边界强制状态治理。"""

    def __init__(self) -> None:
        self._items: dict[str, KnowledgeCandidate] = {}
        self._lock = RLock()

    def save(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        """新增知识候选；只接受全新聚合（`version == 0`），写入后版本为 1。"""
        if candidate.version != 0:
            raise ValueError(
                f"save() 只接受全新聚合（version=0），当前 {candidate.knowledge_id} "
                f"的 version={candidate.version}"
            )
        with self._lock:
            if candidate.knowledge_id in self._items:
                raise KnowledgeAlreadyExistsError(candidate.knowledge_id)
            persisted = deepcopy(candidate)
            persisted.version = 1
            self._items[candidate.knowledge_id] = persisted
            return deepcopy(persisted)

    def get(self, knowledge_id: str) -> KnowledgeCandidate:
        with self._lock:
            item = self._items.get(knowledge_id)
            if item is None:
                raise KnowledgeNotFoundError(knowledge_id)
            return deepcopy(item)

    def update(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        """CAS 更新：版本不一致时抛 `ConcurrentUpdateError`。"""
        if candidate.version <= 0:
            raise ValueError(
                f"update() 不接受未保存聚合（version>=1），当前 "
                f"{candidate.knowledge_id} 的 version={candidate.version}"
            )
        with self._lock:
            current = self._items.get(candidate.knowledge_id)
            if current is None:
                raise KnowledgeNotFoundError(candidate.knowledge_id)
            if current.version != candidate.version:
                raise ConcurrentUpdateError(
                    _ENTITY, candidate.knowledge_id, candidate.version
                )
            persisted = deepcopy(candidate)
            persisted.version = candidate.version + 1
            self._items[candidate.knowledge_id] = persisted
            return deepcopy(persisted)

    def _snapshot(self) -> list[KnowledgeCandidate]:
        """在锁内生成一致的深拷贝快照。

        快照之后的所有过滤 / 排序 / 打分都在锁外执行，避免长时间占锁。
        """
        with self._lock:
            return [deepcopy(item) for item in self._items.values()]

    def list_all(self) -> list[KnowledgeCandidate]:
        snapshot = self._snapshot()
        snapshot.sort(key=lambda item: (item.created_at, item.knowledge_id))
        return snapshot

    def search_confirmed(
        self,
        query: str,
        fault_type: SecurityFaultType,
        limit: int = 3,
    ) -> list[KnowledgeCandidate]:
        """按故障类型和词项匹配，只召回人工确认知识。

        先在锁内取快照，再在锁外做过滤与词法打分。
        """
        snapshot = self._snapshot()
        ranked: list[tuple[int, KnowledgeCandidate]] = []
        for item in snapshot:
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
