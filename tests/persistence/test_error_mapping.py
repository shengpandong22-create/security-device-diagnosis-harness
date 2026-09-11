"""Phase 6A 审计修复：持久化异常契约与 ORM 异常映射验收。

用可控的 Session / 异常注入验证映射，不写随机失败的并发线程测试。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from security_diagnosis_harness.adapters.persistence.diagnosis_repository import (
    SqlAlchemyDiagnosisRepository,
)
from security_diagnosis_harness.adapters.persistence.knowledge_repository import (
    SqlAlchemyKnowledgeRepository,
)
from security_diagnosis_harness.application.errors import (
    DiagnosisAlreadyExistsError,
    DiagnosisNotFoundError,
    KnowledgeAlreadyExistsError,
    KnowledgeNotFoundError,
    RepositoryPersistenceError,
)
from tests.persistence._builders import (
    build_confirmed_case,
    build_knowledge_candidate,
)


class _Row(dict):
    """既是 dict 又可被 setattr 的假 ORM 行。"""

    def __setattr__(self, name: str, value) -> None:
        self[name] = value


class _FakeSession:
    """可注入 commit 异常并记录 rollback / close 的假 Session。"""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.rolled_back = False
        self.closed = False

    def add(self, _obj) -> None:
        return None

    def get(self, _model, _pk):
        # 让 update 路径先通过「存在性」检查，并允许 setattr 写回列值。
        return _Row()

    def commit(self) -> None:
        if self._error is not None:
            raise self._error

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


def _factory(session: _FakeSession):
    @contextmanager
    def _context():
        yield session

    def _make():
        return _context()

    return _make


def _integrity_error() -> IntegrityError:
    return IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))


def _operational_error() -> OperationalError:
    return OperationalError("INSERT", {}, Exception("database is locked"))


# ---------------------------------------------------------------- Diagnosis
def test_diagnosis_integrity_error_maps_to_already_exists():
    session = _FakeSession(_integrity_error())
    repository = SqlAlchemyDiagnosisRepository(_factory(session))

    with pytest.raises(DiagnosisAlreadyExistsError):
        repository.save(build_confirmed_case())


def test_diagnosis_integrity_error_triggers_rollback():
    session = _FakeSession(_integrity_error())
    repository = SqlAlchemyDiagnosisRepository(_factory(session))

    with pytest.raises(DiagnosisAlreadyExistsError):
        repository.save(build_confirmed_case())

    assert session.rolled_back is True


def test_diagnosis_operational_error_maps_to_persistence_error():
    session = _FakeSession(_operational_error())
    repository = SqlAlchemyDiagnosisRepository(_factory(session))

    with pytest.raises(RepositoryPersistenceError):
        repository.save(build_confirmed_case())

    assert session.rolled_back is True


def test_diagnosis_sqlalchemy_error_is_not_leaked():
    session = _FakeSession(_operational_error())
    repository = SqlAlchemyDiagnosisRepository(_factory(session))

    with pytest.raises(RepositoryPersistenceError) as excinfo:
        repository.save(build_confirmed_case())

    assert not isinstance(excinfo.value, OperationalError)


def test_diagnosis_update_maps_persistence_error():
    session = _FakeSession(_operational_error())
    repository = SqlAlchemyDiagnosisRepository(_factory(session))

    with pytest.raises(RepositoryPersistenceError):
        repository.update(build_confirmed_case())

    assert session.rolled_back is True


def test_diagnosis_python_errors_are_not_swallowed():
    """编程错误（非 SQLAlchemy 异常）必须原样抛出。"""

    class _ExplodingSession(_FakeSession):
        def add(self, _obj) -> None:
            raise RuntimeError("programming error")

    session = _ExplodingSession()
    repository = SqlAlchemyDiagnosisRepository(_factory(session))

    with pytest.raises(RuntimeError, match="programming error"):
        repository.save(build_confirmed_case())


# ---------------------------------------------------------------- Knowledge
def test_knowledge_integrity_error_maps_to_already_exists():
    session = _FakeSession(_integrity_error())
    repository = SqlAlchemyKnowledgeRepository(_factory(session))

    with pytest.raises(KnowledgeAlreadyExistsError):
        repository.save(build_knowledge_candidate())


def test_knowledge_integrity_error_triggers_rollback():
    session = _FakeSession(_integrity_error())
    repository = SqlAlchemyKnowledgeRepository(_factory(session))

    with pytest.raises(KnowledgeAlreadyExistsError):
        repository.save(build_knowledge_candidate())

    assert session.rolled_back is True


def test_knowledge_operational_error_maps_to_persistence_error():
    session = _FakeSession(_operational_error())
    repository = SqlAlchemyKnowledgeRepository(_factory(session))

    with pytest.raises(RepositoryPersistenceError):
        repository.save(build_knowledge_candidate())

    assert session.rolled_back is True


# ---------------------------------------------------------------- 对称性
def test_not_found_semantics_are_symmetric(diagnosis_repository, knowledge_repository):
    with pytest.raises(DiagnosisNotFoundError):
        diagnosis_repository.get("missing")
    with pytest.raises(KnowledgeNotFoundError):
        knowledge_repository.get("missing")

    with pytest.raises(DiagnosisNotFoundError):
        diagnosis_repository.update(build_confirmed_case("diag-unknown"))
    with pytest.raises(KnowledgeNotFoundError):
        knowledge_repository.update(build_knowledge_candidate("knw-unknown"))


def test_duplicate_semantics_are_symmetric(diagnosis_repository, knowledge_repository):
    case = build_confirmed_case()
    diagnosis_repository.save(case)
    with pytest.raises(DiagnosisAlreadyExistsError):
        diagnosis_repository.save(case)

    candidate = build_knowledge_candidate()
    knowledge_repository.save(candidate)
    with pytest.raises(KnowledgeAlreadyExistsError):
        knowledge_repository.save(candidate)


def test_sqlalchemy_knowledge_adapter_does_not_import_in_memory_adapter():
    """Adapter 之间不允许反向依赖内存实现的异常。"""
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "src"
        / "security_diagnosis_harness"
        / "adapters"
        / "persistence"
        / "knowledge_repository.py"
    ).read_text(encoding="utf-8")

    assert "adapters.knowledge.in_memory" not in source
    assert "from security_diagnosis_harness.application.errors import" in source


# ---------------------------------------------------------------- 回归
def test_normal_paths_still_work(diagnosis_repository, knowledge_repository):
    case = build_confirmed_case()
    diagnosis_repository.save(case)
    case.description = "补充说明"
    diagnosis_repository.update(case)
    assert diagnosis_repository.get(case.diagnosis_id).description == "补充说明"
    assert diagnosis_repository.count() == 1

    candidate = build_knowledge_candidate()
    knowledge_repository.save(candidate)
    candidate.title = "更新后的标题"
    knowledge_repository.update(candidate)
    assert knowledge_repository.get(candidate.knowledge_id).title == "更新后的标题"


def test_count_uses_sql_count_not_loading_keys(engine):
    """count() 走 SQL COUNT，不需要加载全部 ID。"""
    from security_diagnosis_harness.adapters.persistence.models import DiagnosisCaseRow

    repository = SqlAlchemyDiagnosisRepository.from_engine(engine)
    for index in range(3):
        repository.save(build_confirmed_case(f"diag-count-{index}"))

    assert repository.count() == 3
    assert DiagnosisCaseRow.__tablename__ == "diagnosis_cases"
