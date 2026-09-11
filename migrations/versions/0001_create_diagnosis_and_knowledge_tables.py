"""create diagnosis_cases and knowledge_candidates tables

Revision ID: 0001
Revises:
Create Date: 2026-09-11

Phase 6A 初始迁移：为诊断聚合与知识候选建立持久化表。
复杂子结构（evidence / conclusion / reviews / metadata）统一用 JSON 列存储。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnosis_cases",
        sa.Column("diagnosis_id", sa.String(length=64), primary_key=True),
        sa.Column("fault_type", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("reporter", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("conclusion", sa.JSON(), nullable=True),
        sa.Column("reviews", sa.JSON(), nullable=False),
    )
    op.create_index("ix_diagnosis_cases_fault_type", "diagnosis_cases", ["fault_type"])
    op.create_index("ix_diagnosis_cases_device_id", "diagnosis_cases", ["device_id"])
    op.create_index("ix_diagnosis_cases_status", "diagnosis_cases", ["status"])

    op.create_table(
        "knowledge_candidates",
        sa.Column("knowledge_id", sa.String(length=64), primary_key=True),
        sa.Column("fault_type", sa.String(length=64), nullable=False),
        sa.Column("candidate_label", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_diagnosis_id", sa.String(length=64), nullable=False),
        sa.Column("source_conclusion_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symptoms", sa.JSON(), nullable=False),
        sa.Column("troubleshooting_steps", sa.JSON(), nullable=False),
        sa.Column("excluded_causes", sa.JSON(), nullable=False),
        sa.Column("source_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("reviews", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("redacted", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
    )
    op.create_index("ix_knowledge_candidates_fault_type", "knowledge_candidates", ["fault_type"])
    op.create_index("ix_knowledge_candidates_status", "knowledge_candidates", ["status"])
    op.create_index(
        "ix_knowledge_candidates_source_diagnosis_id",
        "knowledge_candidates",
        ["source_diagnosis_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_candidates_source_diagnosis_id", table_name="knowledge_candidates")
    op.drop_index("ix_knowledge_candidates_status", table_name="knowledge_candidates")
    op.drop_index("ix_knowledge_candidates_fault_type", table_name="knowledge_candidates")
    op.drop_table("knowledge_candidates")

    op.drop_index("ix_diagnosis_cases_status", table_name="diagnosis_cases")
    op.drop_index("ix_diagnosis_cases_device_id", table_name="diagnosis_cases")
    op.drop_index("ix_diagnosis_cases_fault_type", table_name="diagnosis_cases")
    op.drop_table("diagnosis_cases")
