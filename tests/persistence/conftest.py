"""Phase 6A 持久化测试夹具。

所有夹具都使用 pytest 的 `tmp_path`，保证：
- 不在仓库里落地数据库文件；
- 每个测试拿到独立的 SQLite 文件；
- 测试结束后连接被关闭，临时目录可被清理。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from security_diagnosis_harness.adapters.persistence import (  # noqa: E402
    Base,
    SqlAlchemyDiagnosisRepository,
    SqlAlchemyKnowledgeRepository,
    build_database,
)


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """指向临时目录的 SQLite URL。"""
    return f"sqlite:///{(tmp_path / 'phase6a.db').as_posix()}"


@pytest.fixture
def engine(database_url: str) -> Iterator:
    """建表后的 Engine；测试结束关闭并释放连接。"""
    engine, _ = build_database(database_url)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def diagnosis_repository(engine) -> SqlAlchemyDiagnosisRepository:
    return SqlAlchemyDiagnosisRepository.from_engine(engine)


@pytest.fixture
def knowledge_repository(engine) -> SqlAlchemyKnowledgeRepository:
    return SqlAlchemyKnowledgeRepository.from_engine(engine)
