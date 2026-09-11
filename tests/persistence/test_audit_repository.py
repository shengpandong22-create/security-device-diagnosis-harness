"""Phase 6C-1 追加式审计仓储与应用接入验收。"""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from security_diagnosis_harness.adapters.audit_in_memory import InMemoryAuditRepository
from security_diagnosis_harness.adapters.persistence import (
    SqlAlchemyAuditRepository,
    build_database,
)
from security_diagnosis_harness.application.errors import (
    AuditEventNotFoundError,
    RepositoryPersistenceError,
)
from security_diagnosis_harness.config import RuntimeSettings
from security_diagnosis_harness.domain.audit import AuditEntityType, AuditEvent
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import KnowledgeReviewAction
from security_diagnosis_harness.domain.review import HumanReviewAction
from security_diagnosis_harness.ports.audit_repository import AuditRepository
from security_diagnosis_harness.runtime import build_runtime_container, upgrade_database


@pytest.fixture(params=["memory", "sqlite"])
def audit_repository(request, tmp_path: Path):
    if request.param == "memory":
        yield InMemoryAuditRepository()
        return
    url = f"sqlite:///{(tmp_path / 'audit.db').as_posix()}"
    upgrade_database(url)
    engine, factory = build_database(url)
    try:
        yield SqlAlchemyAuditRepository(factory)
    finally:
        engine.dispose()


def _event(entity_id: str = "diag-1") -> AuditEvent:
    return AuditEvent(
        entity_type=AuditEntityType.DIAGNOSIS,
        entity_id=entity_id,
        action="diagnosis.created",
        actor="tester",
        current_state="created",
        current_version=1,
    )


def test_repository_satisfies_port(audit_repository):
    assert isinstance(audit_repository, AuditRepository)


def test_append_get_and_list_round_trip(audit_repository):
    saved = audit_repository.append(_event())
    assert audit_repository.get(saved.event_id) == saved
    assert audit_repository.list_for_entity("diag-1") == [saved]
    assert audit_repository.get(saved.event_id).occurred_at.tzinfo is not None


def test_unknown_event_is_controlled(audit_repository):
    with pytest.raises(AuditEventNotFoundError):
        audit_repository.get("missing")


def test_duplicate_event_cannot_overwrite(audit_repository):
    event = _event()
    audit_repository.append(event)
    with pytest.raises((ValueError, RepositoryPersistenceError)) as excinfo:
        audit_repository.append(event)
    assert type(excinfo.value).__name__ != "AssertionError"


def test_event_redacts_summary_and_metadata():
    event = AuditEvent(
        entity_type=AuditEntityType.DIAGNOSIS,
        entity_id="diag-1",
        action="diagnosis.created",
        summary="password=plain-secret",
        metadata={"access_token": "plain-token"},
    )
    dumped = event.model_dump_json()
    assert "plain-secret" not in dumped
    assert "plain-token" not in dumped
    assert "***REDACTED***" in dumped


def test_event_redacts_actor_credentials():
    event = _event().model_copy(update={"actor": "token=plain-actor-token"})
    validated = AuditEvent.model_validate(event.model_dump())
    assert "plain-actor-token" not in validated.actor


def test_event_is_immutable():
    event = _event()
    with pytest.raises(ValidationError):
        event.action = "changed"  # type: ignore[misc]


def test_runtime_records_create_run_and_review(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}"
    settings = RuntimeSettings(repository_mode="sqlite", database_url=url)
    with build_runtime_container(settings) as container:
        case = container.service.create_diagnosis(
            device_id="camera-3f-001",
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="operator-a",
        )
        container.service.run_diagnosis(case.diagnosis_id)
        container.service.review_diagnosis(
            case.diagnosis_id, HumanReviewAction.CONFIRM, "reviewer-a"
        )
        events = container.audit_repository.list_for_entity(case.diagnosis_id)

    assert [item.action for item in events] == [
        "diagnosis.created",
        "diagnosis.run",
        "diagnosis.review.confirm",
    ]
    assert [(item.previous_version, item.current_version) for item in events] == [
        (None, 1),
        (1, 2),
        (2, 3),
    ]
    assert events[-1].actor == "reviewer-a"


def test_audit_repository_has_no_mutation_api(audit_repository):
    assert not hasattr(audit_repository, "update")
    assert not hasattr(audit_repository, "delete")


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_sqlite_rejects_raw_audit_mutation(verb: str, tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'append-only.db').as_posix()}"
    upgrade_database(url)
    engine, factory = build_database(url)
    repository = SqlAlchemyAuditRepository(factory)
    event = repository.append(_event())
    statement = (
        "UPDATE audit_events SET action='changed' WHERE event_id=:id"
        if verb == "UPDATE"
        else "DELETE FROM audit_events WHERE event_id=:id"
    )
    try:
        with pytest.raises(IntegrityError, match="append-only"):
            with engine.begin() as connection:
                connection.execute(text(statement), {"id": event.event_id})
        assert repository.get(event.event_id).action == "diagnosis.created"
    finally:
        engine.dispose()


def test_runtime_records_knowledge_generation_and_review(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'knowledge-audit.db').as_posix()}"
    settings = RuntimeSettings(repository_mode="sqlite", database_url=url)
    with build_runtime_container(settings) as container:
        case = container.service.create_diagnosis(
            device_id="camera-3f-001",
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="operator-a",
        )
        container.service.run_diagnosis(case.diagnosis_id)
        container.service.review_diagnosis(
            case.diagnosis_id, HumanReviewAction.CONFIRM, "reviewer-a"
        )
        candidate = container.knowledge_service.generate_and_save(
            case.diagnosis_id, "knowledge-curator"
        )
        confirmed = container.knowledge_service.review(
            candidate.knowledge_id,
            KnowledgeReviewAction.CONFIRM,
            "knowledge-reviewer",
        )
        events = container.audit_repository.list_for_entity(candidate.knowledge_id)

    assert confirmed.version == 2
    assert [event.action for event in events] == [
        "knowledge.generated",
        "knowledge.review.confirm",
    ]
    assert [(event.previous_version, event.current_version) for event in events] == [
        (None, 1),
        (1, 2),
    ]


def test_audit_failure_is_explicit_after_business_write():
    class FailingAuditRepository(InMemoryAuditRepository):
        def append(self, event):
            raise RepositoryPersistenceError("审计事件", "injected")

    container = build_runtime_container(RuntimeSettings(repository_mode="memory"))
    container.service._audit_repository = FailingAuditRepository()
    try:
        with pytest.raises(RepositoryPersistenceError):
            container.service.create_diagnosis(
                device_id="camera-3f-001",
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                reporter="operator-a",
            )
        assert container.repository.count() == 1
    finally:
        container.close()
