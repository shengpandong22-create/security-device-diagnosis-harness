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

    assert version == "0001"
