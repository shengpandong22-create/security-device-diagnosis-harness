"""Phase 6A：Alembic 迁移验收。

所有用例都针对临时 SQLite 数据库，不污染仓库、不依赖开发机绝对路径。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from security_diagnosis_harness.adapters.persistence.models import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

EXPECTED_TABLES = {"diagnosis_cases", "knowledge_candidates"}


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    # 通过 -x db_url 传入临时数据库，迁移脚本不依赖 ini 里的默认路径。
    config.cmd_opts = type("_Opts", (), {"x": [f"db_url={database_url}"]})()
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return config


@pytest.fixture
def migration_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"


def _table_names(database_url: str) -> set[str]:
    engine = create_engine(database_url, future=True)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_head_creates_both_tables(migration_url: str):
    command.upgrade(_alembic_config(migration_url), "head")

    tables = _table_names(migration_url)
    assert tables >= EXPECTED_TABLES
    assert "alembic_version" in tables


def test_downgrade_base_removes_business_tables(migration_url: str):
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    tables = _table_names(migration_url)
    assert not (EXPECTED_TABLES & tables)


def test_second_upgrade_after_downgrade_succeeds(migration_url: str):
    config = _alembic_config(migration_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    assert _table_names(migration_url) >= EXPECTED_TABLES


def test_migration_matches_orm_metadata(migration_url: str):
    """迁移建出的表结构必须与 ORM metadata 一致。"""
    command.upgrade(_alembic_config(migration_url), "head")

    engine = create_engine(migration_url, future=True)
    try:
        inspector = inspect(engine)
        migrated = {
            table: {column["name"] for column in inspector.get_columns(table)}
            for table in EXPECTED_TABLES
        }
    finally:
        engine.dispose()

    for table_name, columns in migrated.items():
        orm_columns = {column.name for column in Base.metadata.tables[table_name].columns}
        assert columns == orm_columns, f"{table_name} 列不一致: {columns ^ orm_columns}"


def test_temporary_database_can_be_removed(migration_url: str):
    """迁移后 Engine 全部关闭，临时文件可被删除（无连接泄漏）。"""
    command.upgrade(_alembic_config(migration_url), "head")

    db_path = Path(migration_url.replace("sqlite:///", ""))
    assert db_path.exists()
    db_path.unlink()
    assert not db_path.exists()
