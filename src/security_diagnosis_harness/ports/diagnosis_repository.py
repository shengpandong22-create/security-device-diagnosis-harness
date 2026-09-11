"""诊断仓储 Port。

Phase 6A 把原本只在 application 层的 `InMemoryDiagnosisRepository`
抽象成稳定契约，供内存实现与 SQLite 实现共同遵守。

契约要点：

- 所有读操作返回**独立**的 Domain 对象（深拷贝语义），
  调用方本地修改**不会**隐式修改数据库，必须显式调用 `update()`；
- `save()` 遇到重复 ID 必须抛 `DiagnosisAlreadyExistsError`；
- `update()` 遇到不存在 ID 必须抛 `DiagnosisNotFoundError`；
- `get()` 遇到不存在 ID 抛统一 `DiagnosisNotFoundError`；
- 无法归类的持久化写入失败统一抛 `RepositoryPersistenceError`；
- 实现不得把底层 ORM 异常（`IntegrityError` / `OperationalError` 等）
  作为公开契约泄漏给调用方。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase


@runtime_checkable
class DiagnosisRepository(Protocol):
    """诊断聚合仓储契约。"""

    def save(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase: ...

    def get(self, diagnosis_id: str) -> SecurityDiagnosisCase: ...

    def update(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase: ...

    def list(self) -> list[SecurityDiagnosisCase]: ...

    def exists(self, diagnosis_id: str) -> bool: ...


__all__ = ["DiagnosisRepository"]
