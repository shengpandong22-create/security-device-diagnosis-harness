"""内存仓储的聚合 + 审计原子写入实现。"""

from __future__ import annotations

from copy import deepcopy
from threading import RLock

from security_diagnosis_harness.adapters.audit_in_memory import InMemoryAuditRepository
from security_diagnosis_harness.adapters.knowledge.in_memory import InMemoryKnowledgeRepository
from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository


class InMemoryAuditedWrite:
    """用共享临界区和失败回滚保证两个内存仓储同成同败。"""

    def __init__(
        self,
        diagnoses: InMemoryDiagnosisRepository,
        knowledge: InMemoryKnowledgeRepository,
        audit: InMemoryAuditRepository,
        lock: RLock,
    ) -> None:
        # 锁必须由装配方显式提供并与三个内存仓储共享：静默创建独立锁会让
        # 回滚快照与其他线程的直接写入交错，导致失败回滚覆盖他人已成功的写入。
        self._diagnoses = diagnoses
        self._knowledge = knowledge
        self._audit = audit
        self._lock = lock

    def save_diagnosis(self, case, event):
        return self._write(self._diagnoses, "_cases", "save", case, event)

    def update_diagnosis(self, case, event):
        return self._write(self._diagnoses, "_cases", "update", case, event)

    def save_knowledge(self, candidate, event):
        return self._write(self._knowledge, "_items", "save", candidate, event)

    def update_knowledge(self, candidate, event):
        return self._write(self._knowledge, "_items", "update", candidate, event)

    def _write(self, repository, state_name, operation, aggregate, event):
        with self._lock:
            state = deepcopy(getattr(repository, state_name))
            events = deepcopy(self._audit._events)
            try:
                saved = getattr(repository, operation)(aggregate)
                self._audit.append(event)
                return saved
            except Exception:
                setattr(repository, state_name, state)
                self._audit._events = events
                raise


__all__ = ["InMemoryAuditedWrite"]
