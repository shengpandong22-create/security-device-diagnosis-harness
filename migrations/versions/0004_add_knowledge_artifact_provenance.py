"""add source-specific knowledge provenance

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_candidates") as batch_op:
        batch_op.alter_column("source_diagnosis_id", nullable=True)
        batch_op.alter_column("source_conclusion_id", nullable=True)
        batch_op.add_column(sa.Column("source_artifact_id", sa.String(128), nullable=True))
        batch_op.add_column(
            sa.Column("source_artifact_sha256", sa.String(64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_candidates") as batch_op:
        batch_op.drop_column("source_artifact_sha256")
        batch_op.drop_column("source_artifact_id")
        batch_op.alter_column("source_conclusion_id", nullable=False)
        batch_op.alter_column("source_diagnosis_id", nullable=False)

