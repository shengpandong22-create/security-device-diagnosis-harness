"""SQLAlchemy ORM 模型。

边界约定：

- ORM 模型只存在于 adapters 层，禁止泄漏到 Domain 与 API Schema；
- 聚合内部结构（Evidence / Conclusion / Review / KnowledgeReview）以 JSON 列存储，
  读回时由 Repository 重新构造真正的 Domain 对象；
- 脱敏由 Domain 模型自己保证（`model_validator`），本层不重复实现一套规则。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


class DiagnosisCaseRow(Base):
    """一条诊断用例的持久化行。

    - 标量字段（状态机 + 枚举）独立成列，便于查询与审计；
    - 聚合子结构（evidence / conclusion / reviews）存 JSON，保证 ID 引用关系不丢。
    """

    __tablename__ = "diagnosis_cases"

    diagnosis_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fault_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    reporter: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 乐观锁列：server_default 只为兼容历史行；业务逻辑必须显式写入版本。
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1"), default=1
    )

    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    conclusion: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reviews: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


class KnowledgeCandidateRow(Base):
    """一条知识候选的持久化行。"""

    __tablename__ = "knowledge_candidates"

    knowledge_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fault_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_label: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_diagnosis_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_conclusion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1"), default=1
    )

    symptoms: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    troubleshooting_steps: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    excluded_causes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    reviews: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    redacted: Mapped[bool] = mapped_column(nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )


__all__ = ["Base", "DiagnosisCaseRow", "KnowledgeCandidateRow"]
