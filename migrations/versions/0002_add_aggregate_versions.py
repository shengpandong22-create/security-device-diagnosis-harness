"""add aggregate version columns for optimistic locking

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11

Phase 6B-2：为诊断聚合与知识候选增加乐观锁版本列。

口径说明：

- 迁移默认值（`server_default="1"`）只用于让**已存在的历史行**取得合法初值；
- 新聚合的 Domain 初值仍为 `version=0`，由 Repository 首次 `save()` 写成 1；
- Repository 业务逻辑不依赖数据库默认值修正非法输入（`save(version != 0)` 受控拒绝）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("diagnosis_cases") as batch_op:
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )
    with op.batch_alter_table("knowledge_candidates") as batch_op:
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_candidates") as batch_op:
        batch_op.drop_column("version")
    with op.batch_alter_table("diagnosis_cases") as batch_op:
        batch_op.drop_column("version")
