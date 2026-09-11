"""Alembic 迁移环境（Phase 6A）。

URL 解析优先级：命令行 `-x db_url=...` > 环境变量 > `alembic.ini` 默认。
不依赖开发机绝对路径；自动测试通过 `-x db_url=` 指向临时数据库。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import Engine, engine_from_config, pool

# 让迁移脚本在未安装包时也能导入 src 布局。
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from security_diagnosis_harness.adapters.persistence.database import (  # noqa: E402
    DATABASE_URL_ENV_VAR,
    DEFAULT_DATABASE_URL,
    ensure_sqlite_directory,
)
from security_diagnosis_harness.adapters.persistence.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    """按 -x db_url > 环境变量 > 配置默认 解析数据库 URL。"""
    x_arguments = context.get_x_argument(as_dictionary=True)
    if x_arguments.get("db_url"):
        return x_arguments["db_url"]
    from_env = os.environ.get(DATABASE_URL_ENV_VAR, "").strip()
    if from_env:
        return from_env
    configured = config.get_main_option("sqlalchemy.url", "")
    return configured or DEFAULT_DATABASE_URL


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不建立连接。"""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_engine(connectable: Engine) -> None:
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：使用同步 Engine 执行迁移。

    Engine 必须显式 dispose（成功与失败路径都要），不能依赖垃圾回收：
    否则 SQLite 文件句柄会一直被占用，迁移后无法删除数据库文件。
    """
    url = _resolve_url()
    ensure_sqlite_directory(url)
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    try:
        _run_with_engine(connectable)
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
