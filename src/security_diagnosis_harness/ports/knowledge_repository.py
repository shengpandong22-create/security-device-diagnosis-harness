"""知识仓储 Port。

契约要点：

- 所有读操作返回**独立**的 Domain 对象（深拷贝语义），
  调用方本地修改**不会**隐式修改数据库，必须显式调用 `update()`；
- `save()` 遇到重复 ID 必须抛 `KnowledgeAlreadyExistsError`；
- `get()` / `update()` 遇到不存在 ID 必须抛 `KnowledgeNotFoundError`；
- 无法归类的持久化写入失败统一抛 `RepositoryPersistenceError`；
- 实现不得把底层 ORM 异常作为公开契约泄漏给调用方。

异常统一来自 `application.errors`，Adapter 之间不互相 import 异常。
"""

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
