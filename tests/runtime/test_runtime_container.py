"""Phase 6B-1：正式运行装配 RuntimeContainer 验收。"""

from __future__ import annotations

from pathlib import Path

import pytest

from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository
from security_diagnosis_harness.bootstrap.container import (
    build_container,
    build_phase1_container,
    build_phase2_container,
    build_phase3_container,
    build_phase4_container,
)
from security_diagnosis_harness.config import RepositoryMode, RuntimeSettings
from security_diagnosis_harness.runtime import build_runtime_container


def _sqlite_settings(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings(
        repository_mode="sqlite",
        database_url=f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}",
        auto_migrate=True,
    )


# ---------------------------------------------------------------- 装配
def test_sqlite_mode_uses_sqlalchemy_repository(tmp_path: Path):
    from security_diagnosis_harness.adapters.persistence.diagnosis_repository import (
        SqlAlchemyDiagnosisRepository,
    )

    with build_runtime_container(_sqlite_settings(tmp_path)) as runtime:
        assert isinstance(runtime.repository, SqlAlchemyDiagnosisRepository)
        assert runtime.settings.repository_mode is RepositoryMode.SQLITE


def test_memory_mode_uses_in_memory_repository():
    settings = RuntimeSettings(repository_mode="memory")

    with build_runtime_container(settings) as runtime:
        assert isinstance(runtime.repository, InMemoryDiagnosisRepository)
        assert runtime.engine is None
        assert runtime.session_factory is None


def test_existing_containers_still_use_memory():
    for build in (
        build_container,
        build_phase1_container,
        build_phase2_container,
        build_phase3_container,
        build_phase4_container,
    ):
        container = build()
        assert isinstance(container.repository, InMemoryDiagnosisRepository)


def test_sqlite_engine_is_owned_and_disposed(tmp_path: Path):
    settings = _sqlite_settings(tmp_path)
    runtime = build_runtime_container(settings)
    engine = runtime.engine

    assert engine is not None
    runtime.close()

    # dispose 后再连接会重新建立连接池；用 pool 状态判断已释放。
    assert engine.pool.checkedout() == 0


def test_close_is_idempotent(tmp_path: Path):
    runtime = build_runtime_container(_sqlite_settings(tmp_path))

    runtime.close()
    runtime.close()
    runtime.close()


def test_memory_close_is_safe_and_side_effect_free():
    runtime = build_runtime_container(RuntimeSettings(repository_mode="memory"))

    runtime.close()
    runtime.close()
    assert runtime.engine is None


def test_context_manager_closes_engine(tmp_path: Path):
    settings = _sqlite_settings(tmp_path)
    with build_runtime_container(settings) as runtime:
        engine = runtime.engine
        assert engine is not None

    assert engine.pool.checkedout() == 0


def test_runtime_exposes_required_components(tmp_path: Path):
    with build_runtime_container(_sqlite_settings(tmp_path)) as runtime:
        assert runtime.service is not None
        assert runtime.runner is not None
        assert runtime.registry is not None
        assert runtime.gateway is not None
        assert runtime.llm is not None
        assert runtime.citation_policy is not None
        assert runtime.session_factory is not None


def test_runtime_does_not_call_external_model(tmp_path: Path):
    with build_runtime_container(_sqlite_settings(tmp_path)) as runtime:
        case = runtime.service.create_diagnosis(
            device_id="cam-offline-01",
            fault_type=__import__(
                "security_diagnosis_harness.domain.enums", fromlist=["SecurityFaultType"]
            ).SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter="tester",
        )
        runtime.service.run_diagnosis(case.diagnosis_id)

        assert runtime.llm.external_model_called is False


def test_containers_are_not_global_singletons(tmp_path: Path):
    first = build_runtime_container(_sqlite_settings(tmp_path))
    second = build_runtime_container(_sqlite_settings(tmp_path))

    try:
        assert first is not second
        assert first.service is not second.service
        assert first.repository is not second.repository
        assert first.engine is not second.engine
        assert first.session_factory is not second.session_factory
    finally:
        first.close()
        second.close()


def test_migration_failure_prevents_container(monkeypatch, tmp_path: Path):
    from security_diagnosis_harness import runtime as runtime_module

    def _boom(database_url: str) -> None:
        raise RuntimeError("migration exploded")

    monkeypatch.setattr(runtime_module, "upgrade_database", _boom)

    with pytest.raises(RuntimeError, match="migration exploded"):
        build_runtime_container(_sqlite_settings(tmp_path))
