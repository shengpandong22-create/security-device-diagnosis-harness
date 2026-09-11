"""Phase 6A：持久化边界验收。

这些用例守护的是"架构约束"，而不是某个函数的行为：

- Domain 与 API Schema 不得出现 SQLAlchemy / Alembic；
- 应用服务只依赖仓储 Port；
- 内存仓储与 SQLite 仓储行为一致；
- 数据库 URL 解析不依赖开发机绝对路径。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "security_diagnosis_harness"
FORBIDDEN_IMPORTS = ("sqlalchemy", "alembic")


def _python_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*.py") if path.is_file())


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize("package", ["domain", "api", "ports"])
def test_domain_api_ports_do_not_import_orm(package: str):
    offenders: dict[str, set[str]] = {}
    for path in _python_files(SRC_ROOT / package):
        hits = _imports(path) & set(FORBIDDEN_IMPORTS)
        if hits:
            offenders[str(path.relative_to(SRC_ROOT))] = hits
    assert offenders == {}


def test_application_service_depends_on_port_not_orm():
    source = (SRC_ROOT / "application" / "diagnoses.py").read_text(encoding="utf-8")
    assert "DiagnosisRepository" in source
    assert "sqlalchemy" not in source.lower()
    assert "SqlAlchemyDiagnosisRepository" not in source


def test_database_url_resolution_priority(monkeypatch):
    from security_diagnosis_harness.adapters.persistence.database import (
        DATABASE_URL_ENV_VAR,
        DEFAULT_DATABASE_URL,
        resolve_database_url,
    )

    monkeypatch.delenv(DATABASE_URL_ENV_VAR, raising=False)
    assert resolve_database_url("sqlite:///explicit.db") == "sqlite:///explicit.db"
    assert resolve_database_url() == DEFAULT_DATABASE_URL

    monkeypatch.setenv(DATABASE_URL_ENV_VAR, "sqlite:///from-env.db")
    assert resolve_database_url() == "sqlite:///from-env.db"
    assert resolve_database_url("sqlite:///explicit.db") == "sqlite:///explicit.db"


def test_default_url_is_relative_not_absolute_machine_path():
    from security_diagnosis_harness.adapters.persistence.database import DEFAULT_DATABASE_URL

    assert DEFAULT_DATABASE_URL == "sqlite:///./data/security-diagnosis.db"
    assert ":" not in DEFAULT_DATABASE_URL.split("///", 1)[1].lstrip("./")


def test_in_memory_and_sqlite_repositories_behave_the_same(engine):
    from security_diagnosis_harness.adapters.persistence import SqlAlchemyDiagnosisRepository
    from security_diagnosis_harness.application.errors import (
        DiagnosisAlreadyExistsError,
        DiagnosisNotFoundError,
    )
    from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository
    from tests.persistence._builders import build_confirmed_case

    for repository in (
        InMemoryDiagnosisRepository(),
        SqlAlchemyDiagnosisRepository.from_engine(engine),
    ):
        case = build_confirmed_case()
        repository.save(case)
        assert repository.exists(case.diagnosis_id) is True
        assert repository.get(case.diagnosis_id).status is case.status

        with pytest.raises(DiagnosisAlreadyExistsError):
            repository.save(case)
        with pytest.raises(DiagnosisNotFoundError):
            repository.get("missing")
        with pytest.raises(DiagnosisNotFoundError):
            repository.update(build_confirmed_case("diag-unknown"))


def test_gitignore_covers_database_artifacts():
    patterns = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    stripped = {line.strip() for line in patterns}
    for expected in ("*.db", "*.sqlite", "*.sqlite3"):
        assert expected in stripped
