"""内存诊断仓储。

Phase 0C 不接数据库：只用内存 dict 保存 Case，读写都做深拷贝，
避免外部代码拿到内部对象后绕过应用服务直接改状态。

Phase 6B-2 起与 SQLite 实现共享同一套乐观锁语义：

- 全新聚合必须 `version == 0`，`save()` 落库后版本为 1；
- `update()` 使用 CAS：只有传入副本的版本与当前持久化版本一致才允许写入，
  写入后版本 +1；版本不一致抛 `ConcurrentUpdateError`；
- 用 `RLock` 保护「比较版本 + 写入」这一临界区（不是悲观锁：临界区内没有
  业务处理，也不覆盖 LLM / Tool 执行过程）。
"""

from __future__ import annotations

from threading import RLock

from security_diagnosis_harness.application.errors import (
    ConcurrentUpdateError,
    DiagnosisAlreadyExistsError,
    DiagnosisNotFoundError,
)
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase

_ENTITY = "诊断"


class InMemoryDiagnosisRepository:
    """进程内诊断仓储（乐观锁语义与 SQLite 实现一致）。"""

    def __init__(self) -> None:
        self._cases: dict[str, SecurityDiagnosisCase] = {}
        self._lock = RLock()

    # ------------------------------------------------------------------ 写
    def save(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase:
        """新增一条诊断；ID 重复时受控失败（不做隐式覆盖）。

        只接受全新聚合（`version == 0`），落库后版本为 1。
        """
        if case.version != 0:
            raise ValueError(
                f"save() 只接受全新聚合（version=0），当前 {case.diagnosis_id} "
                f"的 version={case.version}"
            )
        with self._lock:
            if case.diagnosis_id in self._cases:
                raise DiagnosisAlreadyExistsError(case.diagnosis_id)
            persisted = case.model_copy(deep=True)
            persisted.version = 1
            self._cases[case.diagnosis_id] = persisted
            return persisted.model_copy(deep=True)

    def update(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase:
        """CAS 更新：版本不一致时抛 `ConcurrentUpdateError`。"""
        if case.version <= 0:
            raise ValueError(
                f"update() 不接受未保存聚合（version>=1），当前 "
                f"{case.diagnosis_id} 的 version={case.version}"
            )
        with self._lock:
            current = self._cases.get(case.diagnosis_id)
            if current is None:
                raise DiagnosisNotFoundError(case.diagnosis_id)
            if current.version != case.version:
                raise ConcurrentUpdateError(_ENTITY, case.diagnosis_id, case.version)

            persisted = case.model_copy(deep=True)
            persisted.version = case.version + 1
            self._cases[case.diagnosis_id] = persisted
            return persisted.model_copy(deep=True)

    # ------------------------------------------------------------------ 读
    def get(self, diagnosis_id: str) -> SecurityDiagnosisCase:
        """按 ID 读取诊断，不存在时抛 `DiagnosisNotFoundError`。"""
        with self._lock:
            case = self._cases.get(diagnosis_id)
            if case is None:
                raise DiagnosisNotFoundError(diagnosis_id)
            return case.model_copy(deep=True)

    def list(self) -> list[SecurityDiagnosisCase]:
        """列出全部诊断，按创建顺序。"""
        with self._lock:
            return [case.model_copy(deep=True) for case in self._cases.values()]

    def exists(self, diagnosis_id: str) -> bool:
        with self._lock:
            return diagnosis_id in self._cases

    def count(self) -> int:
        with self._lock:
            return len(self._cases)
