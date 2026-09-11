"""追加式审计仓储 Port。"""

from typing import Protocol, runtime_checkable

from security_diagnosis_harness.domain.audit import AuditEvent


@runtime_checkable
class AuditRepository(Protocol):
    def append(self, event: AuditEvent) -> AuditEvent: ...
    def get(self, event_id: str) -> AuditEvent: ...
    def list_for_entity(self, entity_id: str) -> list[AuditEvent]: ...

