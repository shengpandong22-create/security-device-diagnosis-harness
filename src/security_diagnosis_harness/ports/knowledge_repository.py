"""知识仓储 Port。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import KnowledgeCandidate


@runtime_checkable
class KnowledgeRepository(Protocol):
    """保存完整生命周期，但诊断检索只暴露 confirmed knowledge。"""

    def save(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate: ...

    def get(self, knowledge_id: str) -> KnowledgeCandidate: ...

    def update(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate: ...

    def list_all(self) -> list[KnowledgeCandidate]: ...

    def search_confirmed(
        self,
        query: str,
        fault_type: SecurityFaultType,
        limit: int = 3,
    ) -> list[KnowledgeCandidate]: ...


class KnowledgeRetriever(Protocol):
    """供 knowledge__search 使用的稳定检索契约。"""

    def search_confirmed(
        self,
        query: str,
        fault_type: SecurityFaultType,
        limit: int = 3,
    ) -> list[KnowledgeCandidate]: ...
