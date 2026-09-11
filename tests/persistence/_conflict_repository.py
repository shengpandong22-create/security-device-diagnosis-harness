"""可控冲突注入仓储装饰器（Phase 6B-2 收尾）。

用途：真实驱动 Application 层的 run_diagnosis / review_diagnosis，
在 Application 最终 `update(case)` 时，模拟"另一个请求抢先完成一次合法 CAS 更新"，
从而验证：

- Application 不会自动重试 Agent / ToolLoop；
- HumanReview 不会被自动重放；
- 竞争赢家的数据与版本被保留。
"""

from __future__ import annotations

from typing import Protocol

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase


class DiagnosisRepositoryLike(Protocol):
    def save(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase: ...
    def get(self, diagnosis_id: str) -> SecurityDiagnosisCase: ...
    def update(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase: ...
    def list(self) -> list[SecurityDiagnosisCase]: ...
    def exists(self, diagnosis_id: str) -> bool: ...


class ConflictInjectingDiagnosisRepository:
    """包装真实仓储，在第 N 次 `update` 前抢先做一次合法 CAS 更新。

    Args:
        inner: 被包装的真实仓储。
        conflict_on_update_index: 第几次 `update` 注入冲突（从 1 开始）。
        competitor_mutator: 赢家数据的修改逻辑，用于验证赢家被保留。
    """

    def __init__(
        self,
        inner: DiagnosisRepositoryLike,
        *,
        conflict_on_update_index: int = 1,
        competitor_mutator=None,
    ) -> None:
        self._inner = inner
        self._target_index = conflict_on_update_index
        self._competitor_mutator = competitor_mutator or _default_mutator
        self.update_calls: list[SecurityDiagnosisCase] = []
        self.conflict_injected = False
        self.winner: SecurityDiagnosisCase | None = None

    # ------------------------------------------------------------------ 读
    def get(self, diagnosis_id: str) -> SecurityDiagnosisCase:
        return self._inner.get(diagnosis_id)

    def list(self) -> list[SecurityDiagnosisCase]:
        return self._inner.list()

    def exists(self, diagnosis_id: str) -> bool:
        return self._inner.exists(diagnosis_id)

    def count(self) -> int:
        return self._inner.count()

    def save(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase:
        return self._inner.save(case)

    # ------------------------------------------------------------------ 写
    def update(self, case: SecurityDiagnosisCase) -> SecurityDiagnosisCase:
        self.update_calls.append(case)
        if len(self.update_calls) == self._target_index and not self.conflict_injected:
            # 模拟另一个请求：基于当前持久化状态做一次**合法** CAS 更新。
            competitor = self._inner.get(case.diagnosis_id)
            self._competitor_mutator(competitor)
            self.winner = self._inner.update(competitor)
            self.conflict_injected = True
        # 随后调用方的 update 基于陈旧 version → 必然冲突
        return self._inner.update(case)


def _default_mutator(case: SecurityDiagnosisCase) -> None:
    """默认赢家改动：修改 reporter 便于断言赢家数据被保留。"""
    case.reporter = "competitor-winner"
