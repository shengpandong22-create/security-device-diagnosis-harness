"""SQLite 知识仓储实现。

契约与 `InMemoryKnowledgeRepository` 保持一致：

- `save()`：新增；ID 重复时抛 `ValueError`（与内存实现同文案风格）；
- `get()` / `update()`：ID 不存在时抛 `KnowledgeNotFoundError`；
- `list_all()`：全部知识候选；
- `search_confirmed()`：只召回 `status == confirmed` 且 `fault_type` 匹配的候选。

检索打分复用 `domain.knowledge_retrieval` 的确定性词法规则，
不引入向量服务，也不访问网络。
"""

from __future__ import annotations

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from security_diagnosis_harness.adapters.knowledge.in_memory import KnowledgeNotFoundError
from security_diagnosis_harness.adapters.persistence.mapping import (
    columns_to_knowledge,
    knowledge_to_columns,
)
from security_diagnosis_harness.adapters.persistence.models import KnowledgeCandidateRow
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeCandidate,
    KnowledgeCandidateStatus,
)
from security_diagnosis_harness.domain.knowledge_retrieval import (
    MIN_LEXICAL_OVERLAP,
    lexical_overlap_score,
)


class SqlAlchemyKnowledgeRepository:
    """基于 SQLAlchemy 2.x 的知识仓储。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @classmethod
    def from_engine(cls, engine: Engine) -> SqlAlchemyKnowledgeRepository:
        return cls(sessionmaker(bind=engine, expire_on_commit=False, future=True))

    # ------------------------------------------------------------------ 写
    def save(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        with self._session_factory() as session:
            if session.get(KnowledgeCandidateRow, candidate.knowledge_id) is not None:
                raise ValueError(f"知识 {candidate.knowledge_id} 已存在")
            session.add(KnowledgeCandidateRow(**knowledge_to_columns(candidate)))
            session.commit()
        return self.get(candidate.knowledge_id)

    def update(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        with self._session_factory() as session:
            row = session.get(KnowledgeCandidateRow, candidate.knowledge_id)
            if row is None:
                raise KnowledgeNotFoundError(f"知识 {candidate.knowledge_id} 不存在")
            for key, value in knowledge_to_columns(candidate).items():
                setattr(row, key, value)
            session.commit()
        return self.get(candidate.knowledge_id)

    # ------------------------------------------------------------------ 读
    def get(self, knowledge_id: str) -> KnowledgeCandidate:
        with self._session_factory() as session:
            row = session.get(KnowledgeCandidateRow, knowledge_id)
            if row is None:
                raise KnowledgeNotFoundError(f"知识 {knowledge_id} 不存在")
            return columns_to_knowledge(row)

    def list_all(self) -> list[KnowledgeCandidate]:
        statement = select(KnowledgeCandidateRow).order_by(
            KnowledgeCandidateRow.created_at, KnowledgeCandidateRow.knowledge_id
        )
        with self._session_factory() as session:
            return [columns_to_knowledge(row) for row in session.scalars(statement)]

    def search_confirmed(
        self,
        query: str,
        fault_type: SecurityFaultType,
        limit: int = 3,
    ) -> list[KnowledgeCandidate]:
        """按故障类型与词法重叠召回已确认知识。"""
        statement = (
            select(KnowledgeCandidateRow)
            .where(KnowledgeCandidateRow.status == KnowledgeCandidateStatus.CONFIRMED.value)
            .where(KnowledgeCandidateRow.fault_type == fault_type.value)
        )
        with self._session_factory() as session:
            rows = list(session.scalars(statement))

        ranked: list[tuple[int, KnowledgeCandidate]] = []
        for row in rows:
            item = columns_to_knowledge(row)
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
        return [item for _, item in ranked[:limit]]


__all__ = ["SqlAlchemyKnowledgeRepository"]
