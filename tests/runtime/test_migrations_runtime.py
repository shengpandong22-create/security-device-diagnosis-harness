"""Phase 6B-1：程序化 Alembic 迁移验收。

证明迁移走 Alembic 而非 `Base.metadata.create_all()`，
不依赖调用者 cwd，且失败会阻止 RuntimeContainer 构建。
"""

from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from security_diagnosis_harness.adapters.persistence.database import (
    build_engine,
)
from security_diagnosis_harness.config import RuntimeConfigurationError
from security_diagnosis_harness.runtime import upgrade_database

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {"diagnosis_cases", "knowledge_candidates"}


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"


def _tables(url: str) -> set[str]:
    engine = create_engine(url, future=True)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_creates_tables_via_alembic(tmp_path: Path):
    url = _url(tmp_path)
    upgrade_database(url)

    tables = _tables(url)
    assert tables >= EXPECTED_TABLES
    # Alembic 版本表的存在证明走的是迁移而不是 create_all。
    assert "alembic_version" in tables


def test_upgrade_is_idempotent(tmp_path: Path):
    url = _url(tmp_path)

    upgrade_database(url)
    upgrade_database(url)

    assert _tables(url) >= EXPECTED_TABLES


def test_upgrade_does_not_require_project_cwd(tmp_path: Path):
    """从任意 cwd 调用迁移都必须成功（不依赖调用者当前目录）。"""
    url = _url(tmp_path)
    foreign_cwd = tmp_path / "elsewhere"
    foreign_cwd.mkdir()

    code = (
        "import sys;"
        f"sys.path.insert(0, r'{REPO_ROOT / 'src'}');"
        "from security_diagnosis_harness.runtime import upgrade_database;"
        f"upgrade_database(r'{url}')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=foreign_cwd,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert _tables(url) >= EXPECTED_TABLES


def test_upgrade_rejects_non_sqlite_url():
    with pytest.raises(RuntimeConfigurationError):
        upgrade_database("postgresql://user:pass@localhost/db")


def test_upgrade_without_create_all(monkeypatch, tmp_path: Path):
    """调用 upgrade_database 不得触发 Base.metadata.create_all()。"""
    from sqlalchemy import MetaData

    called = {"count": 0}
    original = MetaData.create_all

    def _spy(self, *args, **kwargs):
        called["count"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(MetaData, "create_all", _spy)

    upgrade_database(_url(tmp_path))

    assert called["count"] == 0


def test_migration_failure_propagates(monkeypatch, tmp_path: Path):
    """迁移内部抛错时必须向上传播，不被吞掉。"""
    from alembic import command

    from security_diagnosis_harness import runtime as runtime_module

    def _boom(config, revision):  # noqa: ANN001
        raise RuntimeError("alembic upgrade failed")

    monkeypatch.setattr(command, "upgrade", _boom)

    with pytest.raises(RuntimeError, match="alembic upgrade failed"):
        runtime_module.upgrade_database(_url(tmp_path))


def test_no_alembic_path_separator_deprecation_warning(tmp_path: Path):
    """`version_path_separator` 必须使用新写法，不产生弃用警告。"""
    url = _url(tmp_path)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        upgrade_database(url)

    messages = " ".join(str(item.message) for item in caught)
    assert "path_separator" not in messages


def test_engine_without_tables_fails_controlled(tmp_path: Path):
    """auto_migrate=false 时不迁移，首次 Repository 操作必须受控失败。"""
    from security_diagnosis_harness.adapters.persistence.diagnosis_repository import (
        SqlAlchemyDiagnosisRepository,
    )
    from security_diagnosis_harness.application.errors import RepositoryPersistenceError

    url = _url(tmp_path)
    engine = build_engine(url)
    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    try:
        from tests.persistence._builders import build_confirmed_case

        with pytest.raises(RepositoryPersistenceError):
            repository.save(build_confirmed_case("diag-no-table"))
    finally:
        engine.dispose()

    # 未偷偷 create_all
    assert EXPECTED_TABLES.isdisjoint(_tables(url))


def test_alembic_version_recorded(tmp_path: Path):
    url = _url(tmp_path)
    upgrade_database(url)

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()

    assert version == "0002"


def test_version_columns_exist_after_migration(tmp_path: Path):
    """Phase 6B-2：两张表都应具备 version 列。"""
    url = _url(tmp_path)
    upgrade_database(url)

    engine = create_engine(url, future=True)
    try:
        for table in ("diagnosis_cases", "knowledge_candidates"):
            columns = {
                column["name"] for column in inspect(engine).get_columns(table)
            }
            assert "version" in columns, f"{table} 缺少 version 列"
    finally:
        engine.dispose()


def _alembic_config(url: str):
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _columns_of(url: str, table: str) -> set[str]:
    engine = create_engine(url, future=True)
    try:
        return {column["name"] for column in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def _insert_legacy_rows(url: str) -> None:
    """按 **0001 schema**（无 version 列）插入历史数据。"""
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO diagnosis_cases ("
                    " diagnosis_id, fault_type, device_id, reporter, description,"
                    " status, created_at, updated_at, evidence, conclusion, reviews"
                    ") VALUES ("
                    " 'diag-legacy', 'camera_black_screen', 'cam-1', 'legacy-reporter',"
                    " 'legacy description', 'created',"
                    " '2026-01-01 00:00:00', '2026-01-01 00:00:00',"
                    " '[]', NULL, '[]'"
                    ")"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO knowledge_candidates ("
                    " knowledge_id, fault_type, candidate_label, title, summary,"
                    " root_cause, status, source_diagnosis_id, source_conclusion_id,"
                    " created_at, updated_at, symptoms, troubleshooting_steps,"
                    " excluded_causes, source_evidence_ids, reviews, source,"
                    " redacted, metadata"
                    ") VALUES ("
                    " 'knw-legacy', 'camera_black_screen', 'legacy_label',"
                    " 'legacy title', 'legacy summary', 'legacy root cause',"
                    " 'confirmed', 'diag-legacy', 'con-legacy',"
                    " '2026-01-01 00:00:00', '2026-01-01 00:00:00',"
                    " '[\"s\"]', '[\"step\"]', '[]', '[\"evd-1\"]', '[]',"
                    " 'manual_seed', 0, '{}'"
                    ")"
                )
            )
    finally:
        engine.dispose()


def _row_count(url: str, table: str) -> int:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            return int(
                connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
            )
    finally:
        engine.dispose()


def test_legacy_rows_upgrade_0001_to_0002_preserves_data(tmp_path: Path):
    """真实历史迁移：0001 建表 → 插历史数据 → 升级 0002 → 降级 0001 → 再升级 0002。"""
    url = _url(tmp_path)
    config = _alembic_config(url)

    # 1) 明确只升级到 0001（不是 head）
    command.upgrade(config, "0001")
    assert _columns_of(url, "diagnosis_cases").isdisjoint({"version"})
    assert _columns_of(url, "knowledge_candidates").isdisjoint({"version"})

    # 2) 插入符合 0001 schema 的历史数据
    _insert_legacy_rows(url)
    assert _row_count(url, "diagnosis_cases") == 1
    assert _row_count(url, "knowledge_candidates") == 1

    # 3) 升级到 0002
    command.upgrade(config, "0002")

    for table in ("diagnosis_cases", "knowledge_candidates"):
        assert "version" in _columns_of(url, table), f"{table} 缺少 version 列"

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            case_row = connection.execute(
                text(
                    "SELECT device_id, reporter, description, version"
                    " FROM diagnosis_cases WHERE diagnosis_id = 'diag-legacy'"
                )
            ).one()
            knowledge_row = connection.execute(
                text(
                    "SELECT title, summary, status, version"
                    " FROM knowledge_candidates WHERE knowledge_id = 'knw-legacy'"
                )
            ).one()
            version_num = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
    finally:
        engine.dispose()

    # 原字段保持不变 + version = 1
    assert case_row.device_id == "cam-1"
    assert case_row.reporter == "legacy-reporter"
    assert case_row.description == "legacy description"
    assert case_row.version == 1

    assert knowledge_row.title == "legacy title"
    assert knowledge_row.summary == "legacy summary"
    assert knowledge_row.status == "confirmed"
    assert knowledge_row.version == 1

    assert version_num == "0002"

    # 4) downgrade 0001：数据保留，version 列移除
    command.downgrade(config, "0001")

    assert "version" not in _columns_of(url, "diagnosis_cases")
    assert "version" not in _columns_of(url, "knowledge_candidates")
    assert _row_count(url, "diagnosis_cases") == 1
    assert _row_count(url, "knowledge_candidates") == 1

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            survived = connection.execute(
                text(
                    "SELECT description FROM diagnosis_cases"
                    " WHERE diagnosis_id = 'diag-legacy'"
                )
            ).scalar()
    finally:
        engine.dispose()
    assert survived == "legacy description"

    # 5) 再升级 0002：数据仍在，version 恢复为 1
    command.upgrade(config, "0002")

    assert _row_count(url, "diagnosis_cases") == 1
    assert _row_count(url, "knowledge_candidates") == 1

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            restored = connection.execute(
                text(
                    "SELECT version FROM diagnosis_cases"
                    " WHERE diagnosis_id = 'diag-legacy'"
                )
            ).scalar()
            restored_knowledge = connection.execute(
                text(
                    "SELECT version FROM knowledge_candidates"
                    " WHERE knowledge_id = 'knw-legacy'"
                )
            ).scalar()
    finally:
        engine.dispose()

    assert restored == 1
    assert restored_knowledge == 1


def test_downgrade_to_base_removes_version_columns(tmp_path: Path):
    """downgrade 到 base 后 version 列应被移除。"""
    url = _url(tmp_path)
    upgrade_database(url)

    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "base")

    assert EXPECTED_TABLES.isdisjoint(_tables(url))
