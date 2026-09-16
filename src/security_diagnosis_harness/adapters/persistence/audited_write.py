"""SQLite 聚合与审计事件的单事务写入实现。"""

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from security_diagnosis_harness.adapters.persistence.errors import translate_persistence_error
from security_diagnosis_harness.adapters.persistence.mapping import (
    case_to_columns,
    columns_to_case,
    columns_to_knowledge,
    knowledge_to_columns,
)
from security_diagnosis_harness.adapters.persistence.models import (
    AuditEventRow,
    DiagnosisCaseRow,
    KnowledgeCandidateRow,
)
from security_diagnosis_harness.application.errors import (
    ConcurrentUpdateError,
    DiagnosisAlreadyExistsError,
    DiagnosisNotFoundError,
    KnowledgeAlreadyExistsError,
    KnowledgeNotFoundError,
    RepositoryPersistenceError,
)
from security_diagnosis_harness.domain.audit import AuditEvent
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.knowledge import KnowledgeCandidate


def _audit_row(event: AuditEvent) -> AuditEventRow:
    values = event.model_dump(mode="python")
    values["metadata_json"] = values.pop("metadata")
    return AuditEventRow(**values)


class SqlAlchemyAuditedWrite:
    """在一个 SQLAlchemy 事务中写聚合和追加式审计事件。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_diagnosis(
        self, case: SecurityDiagnosisCase, event: AuditEvent
    ) -> SecurityDiagnosisCase:
        if case.version != 0:
            raise ValueError("save_diagnosis() 只接受 version=0 的聚合")
        columns = case_to_columns(case)
        columns["version"] = 1
        with self._session_factory() as session:
            session.add(DiagnosisCaseRow(**columns))
            session.add(_audit_row(event))
            self._commit_insert(session, DiagnosisCaseRow, case.diagnosis_id, "诊断")
            row = session.get(DiagnosisCaseRow, case.diagnosis_id)
            assert row is not None
            return columns_to_case(row)

    def update_diagnosis(
        self, case: SecurityDiagnosisCase, event: AuditEvent
    ) -> SecurityDiagnosisCase:
        with self._session_factory() as session:
            self._cas_update(
                session,
                DiagnosisCaseRow,
                DiagnosisCaseRow.diagnosis_id,
                case.diagnosis_id,
                case.version,
                case_to_columns(case),
                "诊断",
            )
            session.add(_audit_row(event))
            self._commit(session, "诊断")
            row = session.get(DiagnosisCaseRow, case.diagnosis_id)
            assert row is not None
            return columns_to_case(row)

    def save_knowledge(
        self, candidate: KnowledgeCandidate, event: AuditEvent
    ) -> KnowledgeCandidate:
        if candidate.version != 0:
            raise ValueError("save_knowledge() 只接受 version=0 的聚合")
        columns = knowledge_to_columns(candidate)
        columns["version"] = 1
        with self._session_factory() as session:
            session.add(KnowledgeCandidateRow(**columns))
            session.add(_audit_row(event))
            self._commit_insert(session, KnowledgeCandidateRow, candidate.knowledge_id, "知识候选")
            row = session.get(KnowledgeCandidateRow, candidate.knowledge_id)
            assert row is not None
            return columns_to_knowledge(row)

    def update_knowledge(
        self, candidate: KnowledgeCandidate, event: AuditEvent
    ) -> KnowledgeCandidate:
        with self._session_factory() as session:
            self._cas_update(
                session,
                KnowledgeCandidateRow,
                KnowledgeCandidateRow.knowledge_id,
                candidate.knowledge_id,
                candidate.version,
                knowledge_to_columns(candidate),
                "知识候选",
            )
            session.add(_audit_row(event))
            self._commit(session, "知识候选")
            row = session.get(KnowledgeCandidateRow, candidate.knowledge_id)
            assert row is not None
            return columns_to_knowledge(row)

    @staticmethod
    def _cas_update(session, row_type, id_column, entity_id, version, columns, entity):
        if version <= 0:
            raise ValueError("原子 update 只接受 version>=1 的聚合")
        columns = dict(columns)
        columns.pop(id_column.key, None)
        columns["version"] = version + 1
        try:
            result = session.execute(
                update(row_type)
                .where(id_column == entity_id, row_type.version == version)
                .values(**columns)
            )
        except SQLAlchemyError as exc:
            session.rollback()
            raise translate_persistence_error(entity, exc) from exc
        if result.rowcount == 1:
            return
        session.rollback()
        try:
            exists = session.get(row_type, entity_id) is not None
        except SQLAlchemyError as exc:
            raise translate_persistence_error(entity, exc) from exc
        if not exists:
            if row_type is DiagnosisCaseRow:
                raise DiagnosisNotFoundError(entity_id)
            raise KnowledgeNotFoundError(entity_id)
        raise ConcurrentUpdateError(entity, entity_id, version)

    @staticmethod
    def _commit(session: Session, entity: str) -> None:
        try:
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise translate_persistence_error(entity, exc) from exc

    @staticmethod
    def _commit_insert(session, row_type, entity_id, entity):
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            try:
                exists = session.get(row_type, entity_id) is not None
            except SQLAlchemyError as lookup_exc:
                raise translate_persistence_error(entity, lookup_exc) from lookup_exc
            if exists:
                if row_type is DiagnosisCaseRow:
                    raise DiagnosisAlreadyExistsError(entity_id) from exc
                raise KnowledgeAlreadyExistsError(entity_id) from exc
            raise RepositoryPersistenceError(entity, "integrity") from exc
        except SQLAlchemyError as exc:
            session.rollback()
            raise translate_persistence_error(entity, exc) from exc


__all__ = ["SqlAlchemyAuditedWrite"]
