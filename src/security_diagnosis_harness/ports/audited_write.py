"""业务聚合与审计事件的原子写入边界。"""

from typing import Protocol, runtime_checkable

from security_diagnosis_harness.domain.audit import AuditEvent
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.knowledge import KnowledgeCandidate


@runtime_checkable
class AuditedWrite(Protocol):
    """保证业务聚合与对应审计事件同成同败。"""

    def save_diagnosis(
        self, case: SecurityDiagnosisCase, event: AuditEvent
    ) -> SecurityDiagnosisCase: ...

    def update_diagnosis(
        self, case: SecurityDiagnosisCase, event: AuditEvent
    ) -> SecurityDiagnosisCase: ...

    def save_knowledge(
        self, candidate: KnowledgeCandidate, event: AuditEvent
    ) -> KnowledgeCandidate: ...

    def update_knowledge(
        self, candidate: KnowledgeCandidate, event: AuditEvent
    ) -> KnowledgeCandidate: ...


__all__ = ["AuditedWrite"]
