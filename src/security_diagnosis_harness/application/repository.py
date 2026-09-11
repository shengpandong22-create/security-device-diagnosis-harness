"""内存诊断仓储。

Phase 0C 不接数据库：只用内存 dict 保存 Case，读写都做深拷贝，
避免外部代码拿到内部对象后绕过应用服务直接改状态。

Phase 6A 起本实现与 `ports.diagnosis_repository.DiagnosisRepository`
契约对齐（新增/更新分离、重复 ID 受控失败），行为与 SQLite 实现保持一致。
"""

from __future__ import annotations

from security_diagnosis_harness.application.errors import (
    DiagnosisAlreadyExistsError,
    DiagnosisNotFoundError,
)
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase


class InMemoryDiagnosisRepository:
    """进程内诊断仓储。"""

    def __init__(self) -> None:
        self._cases: dict[str, SecurityDiagnosisCase] = {}

    def save(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase:
        """新增一条诊断；ID 重复时受控失败（不做隐式覆盖）。"""
        if case.diagnosis_id in self._cases:
            raise DiagnosisAlreadyExistsError(case.diagnosis_id)
        self._cases[case.diagnosis_id] = case.model_copy(deep=True)
        return self.get(case.diagnosis_id)

    def update(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase:
        """更新一条已存在诊断，不存在则报错。"""
        if case.diagnosis_id not in self._cases:
            raise DiagnosisNotFoundError(case.diagnosis_id)
        self._cases[case.diagnosis_id] = case.model_copy(deep=True)
        return self.get(case.diagnosis_id)

    def get(self, diagnosis_id: str) -> SecurityDiagnosisCase:
        """按 ID 读取诊断，不存在时抛 `DiagnosisNotFoundError`。"""
        try:
            case = self._cases[diagnosis_id]
        except KeyError as exc:
            raise DiagnosisNotFoundError(diagnosis_id) from exc
        return case.model_copy(deep=True)

    def list(self) -> list[SecurityDiagnosisCase]:
        """列出全部诊断，按创建顺序。"""
        return [case.model_copy(deep=True) for case in self._cases.values()]

    def exists(self, diagnosis_id: str) -> bool:
        return diagnosis_id in self._cases

    def count(self) -> int:
        return len(self._cases)
