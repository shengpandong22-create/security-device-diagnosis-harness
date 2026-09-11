"""诊断仓储 Port。

Phase 6A 把原本只在 application 层的 `InMemoryDiagnosisRepository`
抽象成稳定契约，供内存实现与 SQLite 实现共同遵守。

契约要点：

- 所有读操作返回**独立**的 Domain 对象（深拷贝语义），调用方本地修改数据库；
- `save()` 遇到重复 ID 必须受控失败；
- `update()` 遇到不存在 ID 必须受控失败；
- `get()` 遇到不存在 ID 抛统一 `DiagnosisNotFoundError`。
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
