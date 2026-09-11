"""Phase 6B-2：乐观锁（stale copy 拒绝）验收。

先写失败测试：当前实现在两个 Adapter 上都会发生 lost update。
本文件刻意不用 `id(a) != id(b)` 判断实例差异（对象销毁后 ID 可能被复用），
而是保留引用并用 `is not`。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from security_diagnosis_harness.adapters.persistence import (
    SqlAlchemyDiagnosisRepository,
    SqlAlchemyKnowledgeRepository,
    build_database,
)
from security_diagnosis_harness.application.errors import (
    ConcurrentUpdateError,
    DiagnosisAlreadyExistsError,
    DiagnosisNotFoundError,
    KnowledgeNotFoundError,
)
from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.runtime import upgrade_database
from tests.persistence._builders import build_knowledge_candidate


def _new_case(case_id: str = "diag-lock-1") -> SecurityDiagnosisCase:
    return SecurityDiagnosisCase(
        diagnosis_id=case_id,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        device_id="cam-1",
        reporter="probe",
    )


@pytest.fixture
def in_memory_repository() -> InMemoryDiagnosisRepository:
    return InMemoryDiagnosisRepository()


@pytest.fixture
def sqlite_repository(tmp_path: Path) -> SqlAlchemyDiagnosisRepository:
    """用真实 Alembic 迁移建立 schema（不用 create_all 冒充迁移）。"""
    url = f"sqlite:///{(tmp_path / 'lock.db').as_posix()}"
    upgrade_database(url)
    engine, factory = build_database(url)
    try:
        yield SqlAlchemyDiagnosisRepository(factory)
    finally:
        engine.dispose()


@pytest.fixture
def sqlite_dual_repositories(tmp_path: Path):
    """两个独立 SessionFactory + Repository，连接同一个文件型 SQLite。"""
    url = f"sqlite:///{(tmp_path / 'dual.db').as_posix()}"
    upgrade_database(url)
    engine_a, _ = build_database(url)
    factory_a = sessionmaker(bind=engine_a, expire_on_commit=False, future=True)
    engine_b, _ = build_database(url)
    factory_b = sessionmaker(bind=engine_b, expire_on_commit=False, future=True)
    try:
        yield (
            SqlAlchemyDiagnosisRepository(factory_a),
            SqlAlchemyDiagnosisRepository(factory_b),
        )
    finally:
        engine_a.dispose()
        engine_b.dispose()


# ---------------------------------------------------------------- 领域版本字段
def test_new_aggregate_starts_at_version_zero():
    assert _new_case().version == 0
    assert build_knowledge_candidate().version == 0


def test_version_defaults_are_not_api_controllable():
    """创建请求不得暴露 version 输入。"""
    from security_diagnosis_harness.api.schemas import CreateDiagnosisRequest

    assert "version" not in CreateDiagnosisRequest.model_fields


# ---------------------------------------------------------------- save 语义
@pytest.mark.parametrize("repository_name", ["in_memory", "sqlite"])
def test_first_save_persists_version_one(repository_name, request):
    repository = request.getfixturevalue(f"{repository_name}_repository")
    saved = repository.save(_new_case())

    assert saved.version == 1
    assert repository.get(saved.diagnosis_id).version == 1


@pytest.mark.parametrize("repository_name", ["in_memory", "sqlite"])
def test_save_rejects_non_zero_version(repository_name, request):
    """save() 只能接受全新聚合（version=0）。"""
    repository = request.getfixturevalue(f"{repository_name}_repository")
    case = _new_case()
    case.version = 3

    with pytest.raises(ValueError, match="version"):
        repository.save(case)


@pytest.mark.parametrize("repository_name", ["in_memory", "sqlite"])
def test_update_rejects_unsaved_aggregate(repository_name, request):
    repository = request.getfixturevalue(f"{repository_name}_repository")
    case = _new_case()

    with pytest.raises(ValueError, match="version"):
        repository.update(case)


# ---------------------------------------------------------------- 核心 CAS
@pytest.mark.parametrize("repository_name", ["in_memory", "sqlite"])
def test_stale_copy_cannot_overwrite_newer_state(repository_name, request):
    """真实并发冲突：两个独立副本基于同一 version，后者必须被拒。"""
    repository = request.getfixturevalue(f"{repository_name}_repository")

    saved = repository.save(_new_case())
    assert saved.version == 1

    copy_a = repository.get(saved.diagnosis_id)
    copy_b = repository.get(saved.diagnosis_id)

    assert copy_a is not copy_b
    assert copy_a.version == copy_b.version == 1

    copy_a.description = "winner"
    winner = repository.update(copy_a)
    assert winner.version == 2

    copy_b.description = "loser"
    with pytest.raises(ConcurrentUpdateError):
        repository.update(copy_b)

    final = repository.get(saved.diagnosis_id)
    assert final.version == 2
    assert final.description == "winner"
    assert final.description != "loser"


@pytest.mark.parametrize("repository_name", ["in_memory", "sqlite"])
def test_successful_update_preserves_winner_data(repository_name, request):
    repository = request.getfixturevalue(f"{repository_name}_repository")
    saved = repository.save(_new_case())

    copy_a = repository.get(saved.diagnosis_id)
    copy_b = repository.get(saved.diagnosis_id)

    copy_a.reporter = "winner-reporter"
    repository.update(copy_a)

    copy_b.reporter = "loser-reporter"
    with pytest.raises(ConcurrentUpdateError):
        repository.update(copy_b)

    final = repository.get(saved.diagnosis_id)
    assert final.reporter == "winner-reporter"


@pytest.mark.parametrize("repository_name", ["in_memory", "sqlite"])
def test_version_increments_on_each_successful_update(repository_name, request):
    repository = request.getfixturevalue(f"{repository_name}_repository")
    saved = repository.save(_new_case())

    for expected in (2, 3, 4):
        current = repository.get(saved.diagnosis_id)
        current.description = f"round-{expected}"
        updated = repository.update(current)
        assert updated.version == expected


@pytest.mark.parametrize("repository_name", ["in_memory", "sqlite"])
def test_conflict_leaves_aggregate_consistent(repository_name, request):
    """冲突后聚合不能停留在半更新状态。"""
    repository = request.getfixturevalue(f"{repository_name}_repository")
    saved = repository.save(_new_case())

    # 两个**独立**副本基于同一 version；A 先成功，B 成为 stale copy。
    copy_a = repository.get(saved.diagnosis_id)
    copy_b = repository.get(saved.diagnosis_id)
    assert copy_a is not copy_b
    assert copy_a.version == copy_b.version == 1

    copy_a.description = "winner"
    repository.update(copy_a)

    copy_b.description = "loser"
    before = repository.get(saved.diagnosis_id)
    with pytest.raises(ConcurrentUpdateError):
        repository.update(copy_b)

    after = repository.get(saved.diagnosis_id)
    assert after.model_dump() == before.model_dump()
    assert after.description == "winner"


@pytest.mark.parametrize("repository_name", ["in_memory", "sqlite"])
def test_update_unknown_id_raises_not_found(repository_name, request):
    repository = request.getfixturevalue(f"{repository_name}_repository")
    # 用一个已"保存过"的版本号去更新一个不存在的 ID。
    missing = _new_case("diag-missing").model_copy(update={"version": 1})

    with pytest.raises(DiagnosisNotFoundError):
        repository.update(missing)


@pytest.mark.parametrize("repository_name", ["in_memory", "sqlite"])
def test_repository_does_not_mutate_caller_object(repository_name, request):
    repository = request.getfixturevalue(f"{repository_name}_repository")
    case = _new_case()

    returned = repository.save(case)

    assert case.version == 0
    assert returned.version == 1
    assert case is not returned


# ---------------------------------------------------------------- 双 Repository
def test_two_sqlite_repositories_share_one_file(sqlite_dual_repositories):
    repository_a, repository_b = sqlite_dual_repositories
    assert repository_a is not repository_b

    saved = repository_a.save(_new_case())
    assert repository_b.get(saved.diagnosis_id).version == 1

    copy_a = repository_a.get(saved.diagnosis_id)
    copy_b = repository_b.get(saved.diagnosis_id)
    assert copy_a is not copy_b

    copy_a.description = "winner"
    assert repository_a.update(copy_a).version == 2

    copy_b.description = "loser"
    with pytest.raises(ConcurrentUpdateError):
        repository_b.update(copy_b)

    final = repository_b.get(saved.diagnosis_id)
    assert final.version == 2
    assert final.description == "winner"


def test_duplicate_save_still_rejected_with_version(sqlite_repository):
    case = _new_case()
    sqlite_repository.save(case)

    with pytest.raises(DiagnosisAlreadyExistsError):
        sqlite_repository.save(_new_case())


# ---------------------------------------------------------------- Knowledge
def test_knowledge_stale_copy_is_rejected(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'k.db').as_posix()}"
    upgrade_database(url)
    engine, factory = build_database(url)
    try:
        repository = SqlAlchemyKnowledgeRepository(factory)
        saved = repository.save(build_knowledge_candidate())
        assert saved.version == 1

        copy_a = repository.get(saved.knowledge_id)
        copy_b = repository.get(saved.knowledge_id)
        assert copy_a is not copy_b

        copy_a.title = "winner-title"
        assert repository.update(copy_a).version == 2

        copy_b.title = "loser-title"
        with pytest.raises(ConcurrentUpdateError):
            repository.update(copy_b)

        final = repository.get(saved.knowledge_id)
        assert final.title == "winner-title"
        assert final.version == 2
    finally:
        engine.dispose()


def test_knowledge_save_rejects_non_zero_version(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'k2.db').as_posix()}"
    upgrade_database(url)
    engine, factory = build_database(url)
    try:
        repository = SqlAlchemyKnowledgeRepository(factory)
        candidate = build_knowledge_candidate()
        candidate.version = 5
        with pytest.raises(ValueError, match="version"):
            repository.save(candidate)
    finally:
        engine.dispose()


def test_knowledge_update_unknown_raises_not_found(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'k3.db').as_posix()}"
    upgrade_database(url)
    engine, factory = build_database(url)
    try:
        repository = SqlAlchemyKnowledgeRepository(factory)
        missing = build_knowledge_candidate("knw-missing").model_copy(
            update={"version": 1}
        )
        with pytest.raises(KnowledgeNotFoundError):
            repository.update(missing)
    finally:
        engine.dispose()
