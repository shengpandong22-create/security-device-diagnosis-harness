"""SQLite 追加式审计仓储。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from security_diagnosis_harness.adapters.persistence.errors import translate_persistence_error
from security_diagnosis_harness.adapters.persistence.mapping import ensure_aware
from security_diagnosis_harness.adapters.persistence.models import AuditEventRow
from security_diagnosis_harness.application.errors import (
    AuditEventNotFoundError,
    RepositoryPersistenceError,
)
from security_diagnosis_harness.domain.audit import AuditEvent


class SqlAlchemyAuditRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def append(self, event: AuditEvent) -> AuditEvent:
        values = event.model_dump(mode="python")
        values["metadata_json"] = values.pop("metadata")
        row = AuditEventRow(**values)
        with self._session_factory() as session:
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise RepositoryPersistenceError("审计事件", "integrity") from exc
            except SQLAlchemyError as exc:
                session.rollback()
                raise translate_persistence_error("审计事件", exc) from exc
        return self.get(event.event_id)

    def get(self, event_id: str) -> AuditEvent:
        try:
            with self._session_factory() as session:
                row = session.get(AuditEventRow, event_id)
                if row is None:
                    raise AuditEventNotFoundError(event_id)
                return _to_domain(row)
        except SQLAlchemyError as exc:
            raise translate_persistence_error("审计事件", exc) from exc

    def list_for_entity(self, entity_id: str) -> list[AuditEvent]:
        statement = (
            select(AuditEventRow)
            .where(AuditEventRow.entity_id == entity_id)
            .order_by(AuditEventRow.occurred_at, AuditEventRow.event_id)
        )
        try:
            with self._session_factory() as session:
                return [_to_domain(row) for row in session.scalars(statement)]
        except SQLAlchemyError as exc:
            raise translate_persistence_error("审计事件", exc) from exc


def _to_domain(row: AuditEventRow) -> AuditEvent:
    values = {
        "event_id": row.event_id,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "action": row.action,
        "outcome": row.outcome,
        "actor": row.actor,
        "previous_state": row.previous_state,
        "current_state": row.current_state,
        "previous_version": row.previous_version,
        "current_version": row.current_version,
        "summary": row.summary,
        "metadata": row.metadata_json,
        "occurred_at": ensure_aware(row.occurred_at),
    }
    return AuditEvent.model_validate(values)
