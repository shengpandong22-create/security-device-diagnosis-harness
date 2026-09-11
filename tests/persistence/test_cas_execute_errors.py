"""Phase 6B-2 收尾：CAS execute 阶段异常映射验收。

当前缺陷：`session.execute(UPDATE...)` 位于 try/except 之外，
若 execute 阶段抛 `OperationalError`，底层 ORM 异常会直接泄漏。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from security_diagnosis_harness.adapters.persistence.diagnosis_repository import (
    SqlAlchemyDiagnosisRepository,
)
from security_diagnosis_harness.adapters.persistence.knowledge_repository import (
    SqlAlchemyKnowledgeRepository,
)
from security_diagnosis_harness.application.errors import (
    ConcurrentUpdateError,
    DiagnosisNotFoundError,
    KnowledgeNotFoundError,
    RepositoryPersistenceError,
)
from tests.persistence._builders import (
    build_confirmed_case,
    build_knowledge_candidate,
)


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeSession:
    """可分别注入 execute / commit 异常的假 Session。"""

    def __init__(
        self,
        *,
        row_exists: bool = True,
        rowcount: int = 1,
        execute_error: Exception | None = None,
        commit_error: Exception | None = None,
        get_error: Exception | None = None,
    ) -> None:
        self._row_exists = row_exists
        self._rowcount = rowcount
        self._execute_error = execute_error
        self._commit_error = commit_error
        self._get_error = get_error
        self.rolled_back = False
        self.closed = False

    def add(self, _obj) -> None:
        return None

    def get(self, _model, _pk):
        if self._get_error is not None:
            raise self._get_error
        return object() if self._row_exists else None

    def execute(self, _statement, _parameters=None) -> _Result:
        if self._execute_error is not None:
            raise self._execute_error
        return _Result(self._rowcount)

    def commit(self) -> None:
        if self._commit_error is not None:
            raise self._commit_error

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


def _operational() -> OperationalError:
    return OperationalError("UPDATE", {}, Exception("database is locked"))


def _integrity() -> IntegrityError:
    return IntegrityError("UPDATE", {}, Exception("constraint failed"))


# ---------------------------------------------------------------- Diagnosis
def test_diagnosis_execute_operational_error_maps_to_persistence_error():
    session = _FakeSession(execute_error=_operational())
    repository = SqlAlchemyDiagnosisRepository(_factory(session))
    persisted = build_confirmed_case().model_copy(update={"version": 1})

    with pytest.raises(RepositoryPersistenceError):
        repository.update(persisted)


def test_diagnosis_execute_error_calls_rollback():
    session = _FakeSession(execute_error=_operational())
    repository = SqlAlchemyDiagnosisRepository(_factory(session))
    persisted = build_confirmed_case().model_copy(update={"version": 1})

    with pytest.raises(RepositoryPersistenceError):
        repository.update(persisted)

    assert session.rolled_back is True


def test_diagnosis_execute_integrity_error_maps_to_persistence_error():
    session = _FakeSession(execute_error=_integrity())
    repository = SqlAlchemyDiagnosisRepository(_factory(session))
    persisted = build_confirmed_case().model_copy(update={"version": 1})

    with pytest.raises(RepositoryPersistenceError):
        repository.update(persisted)


def test_diagnosis_execute_error_does_not_leak_orm_exception():
    session = _FakeSession(execute_error=_operational())
    repository = SqlAlchemyDiagnosisRepository(_factory(session))
    persisted = build_confirmed_case().model_copy(update={"version": 1})

    with pytest.raises(RepositoryPersistenceError) as excinfo:
        repository.update(persisted)

    for leaked in (OperationalError, SQLAlchemyError, IntegrityError):
        assert not isinstance(excinfo.value, leaked)


# ---------------------------------------------------------------- Knowledge
def test_knowledge_execute_operational_error_maps_to_persistence_error():
    session = _FakeSession(execute_error=_operational())
    repository = SqlAlchemyKnowledgeRepository(_factory(session))
    persisted = build_knowledge_candidate().model_copy(update={"version": 1})

    with pytest.raises(RepositoryPersistenceError):
        repository.update(persisted)


def test_knowledge_execute_error_calls_rollback():
    session = _FakeSession(execute_error=_operational())
    repository = SqlAlchemyKnowledgeRepository(_factory(session))
    persisted = build_knowledge_candidate().model_copy(update={"version": 1})

    with pytest.raises(RepositoryPersistenceError):
        repository.update(persisted)

    assert session.rolled_back is True


def test_knowledge_execute_error_does_not_leak_orm_exception():
    session = _FakeSession(execute_error=_operational())
    repository = SqlAlchemyKnowledgeRepository(_factory(session))
    persisted = build_knowledge_candidate().model_copy(update={"version": 1})

    with pytest.raises(RepositoryPersistenceError) as excinfo:
        repository.update(persisted)

    assert not isinstance(excinfo.value, OperationalError)


# ------------------------------------------------------- 冲突分类查询异常
@pytest.mark.parametrize(
    ("repository_type", "aggregate"),
    [
        (SqlAlchemyDiagnosisRepository, build_confirmed_case),
        (SqlAlchemyKnowledgeRepository, build_knowledge_candidate),
    ],
)
def test_save_classification_lookup_error_is_mapped(repository_type, aggregate):
    """INSERT 冲突后的分类查询失败时，ORM 异常不能从仓储边界泄漏。"""
    session = _FakeSession(commit_error=_integrity(), get_error=_operational())
    repository = repository_type(_factory(session))

    with pytest.raises(RepositoryPersistenceError) as excinfo:
        repository.save(aggregate())

    assert session.rolled_back is True
    assert not isinstance(excinfo.value, SQLAlchemyError)


@pytest.mark.parametrize(
    ("repository_type", "aggregate"),
    [
        (SqlAlchemyDiagnosisRepository, build_confirmed_case),
        (SqlAlchemyKnowledgeRepository, build_knowledge_candidate),
    ],
)
def test_cas_classification_lookup_error_is_mapped(repository_type, aggregate):
    """CAS 未命中后的分类查询失败时，必须返回统一持久化错误。"""
    session = _FakeSession(rowcount=0, get_error=_operational())
    repository = repository_type(_factory(session))
    persisted = aggregate().model_copy(update={"version": 1})

    with pytest.raises(RepositoryPersistenceError) as excinfo:
        repository.update(persisted)

    assert session.rolled_back is True
    assert not isinstance(excinfo.value, SQLAlchemyError)


# ---------------------------------------------------------------- 分类不回退
def test_diagnosis_rowcount_zero_not_found_is_not_persistence_error():
    """rowcount=0 + ID 不存在 → NotFound，不能被包装成持久化错误。"""
    session = _FakeSession(rowcount=0, row_exists=False)
    repository = SqlAlchemyDiagnosisRepository(_factory(session))
    persisted = build_confirmed_case().model_copy(update={"version": 1})

    with pytest.raises(DiagnosisNotFoundError):
        repository.update(persisted)

    assert not isinstance(session, RepositoryPersistenceError)


def test_diagnosis_rowcount_zero_conflict_is_not_persistence_error():
    """rowcount=0 + ID 存在 → ConcurrentUpdateError，不能被包装成持久化错误。"""
    session = _FakeSession(rowcount=0, row_exists=True)
    repository = SqlAlchemyDiagnosisRepository(_factory(session))
    persisted = build_confirmed_case().model_copy(update={"version": 1})

    with pytest.raises(ConcurrentUpdateError):
        repository.update(persisted)

    assert session.rolled_back is True


def test_knowledge_rowcount_zero_not_found_is_not_persistence_error():
    session = _FakeSession(rowcount=0, row_exists=False)
    repository = SqlAlchemyKnowledgeRepository(_factory(session))
    persisted = build_knowledge_candidate().model_copy(update={"version": 1})

    with pytest.raises(KnowledgeNotFoundError):
        repository.update(persisted)


def test_knowledge_rowcount_zero_conflict_is_not_persistence_error():
    session = _FakeSession(rowcount=0, row_exists=True)
    repository = SqlAlchemyKnowledgeRepository(_factory(session))
    persisted = build_knowledge_candidate().model_copy(update={"version": 1})

    with pytest.raises(ConcurrentUpdateError):
        repository.update(persisted)

    assert session.rolled_back is True


# ---------------------------------------------------------------- 编程错误
def test_diagnosis_python_error_is_not_swallowed():
    """非 SQLAlchemy 的编程错误必须原样抛出。"""

    class _ExplodingSession(_FakeSession):
        def execute(self, _statement, _parameters=None) -> _Result:
            raise RuntimeError("programming error")

    session = _ExplodingSession()
    repository = SqlAlchemyDiagnosisRepository(_factory(session))
    persisted = build_confirmed_case().model_copy(update={"version": 1})

    with pytest.raises(RuntimeError, match="programming error"):
        repository.update(persisted)


def test_knowledge_python_error_is_not_swallowed():
    class _ExplodingSession(_FakeSession):
        def execute(self, _statement, _parameters=None) -> _Result:
            raise RuntimeError("programming error")

    session = _ExplodingSession()
    repository = SqlAlchemyKnowledgeRepository(_factory(session))
    persisted = build_knowledge_candidate().model_copy(update={"version": 1})

    with pytest.raises(RuntimeError, match="programming error"):
        repository.update(persisted)
