"""进程内追加式审计仓储。"""

from copy import deepcopy
from threading import RLock

from security_diagnosis_harness.application.errors import AuditEventNotFoundError
from security_diagnosis_harness.domain.audit import AuditEvent


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self._events: dict[str, AuditEvent] = {}
        self._lock = RLock()

    def append(self, event: AuditEvent) -> AuditEvent:
        with self._lock:
            if event.event_id in self._events:
                raise ValueError(f"审计事件已存在: {event.event_id}")
            self._events[event.event_id] = deepcopy(event)
            return deepcopy(event)

    def get(self, event_id: str) -> AuditEvent:
        with self._lock:
            try:
                return deepcopy(self._events[event_id])
            except KeyError as exc:
                raise AuditEventNotFoundError(event_id) from exc

    def list_for_entity(self, entity_id: str) -> list[AuditEvent]:
        with self._lock:
            events = [
                deepcopy(item)
                for item in self._events.values()
                if item.entity_id == entity_id
            ]
        return sorted(events, key=lambda item: (item.occurred_at, item.event_id))
