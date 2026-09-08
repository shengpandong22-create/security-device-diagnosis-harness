"""领域异常。"""

from __future__ import annotations


class DomainError(Exception):
    """所有领域规则的基类。"""


class InvalidStatusTransition(DomainError):
    """非法的诊断状态跳转。"""


class EvidenceDiagnosisMismatch(DomainError):
    """Evidence 不属于当前诊断。"""


class UnknownEvidenceReference(DomainError):
    """结论引用了当前诊断中不存在的 Evidence。"""


class ReviewNotAllowed(DomainError):
    """当前状态下不允许该人工审核动作。"""


class CitationPolicyViolation(DomainError):
    """结论引用不满足 Citation Policy。"""
