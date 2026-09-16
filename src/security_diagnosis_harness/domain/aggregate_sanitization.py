"""聚合写入前的安全重建。

内存与 SQLite 仓储必须共享这一入口，避免不同装配产生不同的脱敏和
领域不变量语义。函数只依赖 Domain，不知道任何仓储或 ORM。
"""

from __future__ import annotations

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.knowledge import KnowledgeCandidate


def sanitize_case(case: SecurityDiagnosisCase) -> SecurityDiagnosisCase:
    """返回经完整 Domain 校验重新构造的诊断安全副本。"""
    return SecurityDiagnosisCase.model_validate(case.model_dump(mode="python"))


def sanitize_knowledge(candidate: KnowledgeCandidate) -> KnowledgeCandidate:
    """返回经完整 Domain 校验重新构造的知识安全副本。"""
    return KnowledgeCandidate.model_validate(candidate.model_dump(mode="python"))


__all__ = ["sanitize_case", "sanitize_knowledge"]
