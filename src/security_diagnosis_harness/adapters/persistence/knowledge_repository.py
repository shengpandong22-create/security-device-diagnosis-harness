"""SQLite 知识仓储实现。

契约与 `InMemoryKnowledgeRepository` 完全对称：

- `save()`：新增；ID 重复时抛 `KnowledgeAlreadyExistsError`；
- `get()` / `update()`：ID 不存在时抛 `KnowledgeNotFoundError`；
- `list_all()`：全部知识候选；
- `search_confirmed()`：只召回 `status == confirmed` 且 `fault_type` 匹配的候选。

异常不依赖底层 SQLAlchemy：`IntegrityError` 等统一映射为应用层受控错误。
本模块不 import 内存 Adapter，异常统一来自 `application.errors`。

检索打分复用 `domain.knowledge_retrieval` 的确定性词法规则，
不引入向量服务，也不访问网络。
"""

from __future__ import annotations

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from security_diagnosis_harness.adapters.persistence.mapping import (
    columns_to_knowledge,
    knowledge_to_columns,
)
from security_diagnosis_harness.adapters.persistence.models import KnowledgeCandidateRow
from security_diagnosis_harness.application.errors import (
    KnowledgeAlreadyExistsError,
    KnowledgeNotFoundError,
    RepositoryPersistenceError,
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

_ENTITY = "知识候选"


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
            session.add(KnowledgeCandidateRow(**knowledge_to_columns(candidate)))
            try:
                session.commit()
            except IntegrityError as exc:
                # 主键冲突（常见于并发或重复 ID）→ 受控的 AlreadyExists。
                session.rollback()
                raise KnowledgeAlreadyExistsError(candidate.knowledge_id) from exc
            except SQLAlchemyError as exc:
                session.rollback()
                raise RepositoryPersistenceError(_ENTITY, type(exc).__name__) from exc
        return self.get(candidate.knowledge_id)

    def update(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        with self._session_factory() as session:
            row = session.get(KnowledgeCandidateRow, candidate.knowledge_id)
            if row is None:
                raise KnowledgeNotFoundError(candidate.knowledge_id)
            for key, value in knowledge_to_columns(candidate).items():
                setattr(row, key, value)
            try:
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                raise RepositoryPersistenceError(_ENTITY, type(exc).__name__) from exc
        return self.get(candidate.knowledge_id)

    # ------------------------------------------------------------------ 读
    def get(self, knowledge_id: str) -> KnowledgeCandidate:
        with self._session_factory() as session:
            row = session.get(KnowledgeCandidateRow, knowledge_id)
            if row is None:
                raise KnowledgeNotFoundError(knowledge_id)
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
