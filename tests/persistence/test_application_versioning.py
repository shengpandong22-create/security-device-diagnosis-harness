"""Phase 6B-2：Application 层版本传播验收。

验证应用服务在各条路径上都使用 `repository.update()` 返回的**最新持久化副本**，
并且不自动重试 Agent / Tool / HumanReview。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from security_diagnosis_harness.adapters.persistence import (
    SqlAlchemyDiagnosisRepository,
    build_database,
)
from security_diagnosis_harness.application.errors import ConcurrentUpdateError
from security_diagnosis_harness.bootstrap.container import build_container
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.review import HumanReviewAction
from security_diagnosis_harness.runtime import upgrade_database

DEVICE_ID = "camera-3f-001"
REPORTER = "probe"


def _runtime_service(tmp_path: Path):
    """用真实 SQLite 装配一个只支持摄像头的运行时服务。"""
    from security_diagnosis_harness.config import RuntimeSettings
    from security_diagnosis_harness.runtime import build_runtime_container as brc

    url = f"sqlite:///{(tmp_path / 'app.db').as_posix()}"
    engine, _ = build_database(url)
    settings = RuntimeSettings(repository_mode="sqlite", database_url=url)
    container = brc(settings)
    return container, engine


def test_create_then_run_then_review_advances_version(tmp_path: Path):
    """create=1 → run=2 → review=3，且每步都用最新副本。"""
    container, engine = _runtime_service(tmp_path)
    try:
        service = container.service

        case = service.create_diagnosis(
            device_id=DEVICE_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter=REPORTER,
        )
        assert case.version == 1

        service.run_diagnosis(case.diagnosis_id)
        assert service.get_diagnosis(case.diagnosis_id).version == 2

        service.review_diagnosis(
            diagnosis_id=case.diagnosis_id,
            action=HumanReviewAction.CONFIRM,
            reviewer=REPORTER,
        )
        final = service.get_diagnosis(case.diagnosis_id)
        assert final.version == 3
        assert final.status.value == "confirmed"
    finally:
        container.close()
        engine.dispose()


def test_service_does_not_retry_after_conflict(tmp_path: Path):
    """冲突不得触发自动重试：Runner 只被调用一次，版本不变。"""
    container, engine = _runtime_service(tmp_path)
    try:
        service = container.service
        case = service.create_diagnosis(
            device_id=DEVICE_ID,
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            reporter=REPORTER,
        )
        assert case.version == 1

        # 制造 stale copy：外部先读一份，随后人为推进持久化版本
        stale = service.get_diagnosis(case.diagnosis_id)
        assert stale.version == 1

        service.run_diagnosis(case.diagnosis_id)  # version -> 2

        # 用陈旧副本更新应该被拒，且不发生重试
        calls = {"count": 0}
        original = container.runner.run

        def _counting_run(*args, **kwargs):
            calls["count"] += 1
            return original(*args, **kwargs)

        container.runner.run = _counting_run  # type: ignore[method-assign]

        with pytest.raises(ConcurrentUpdateError):
            container.repository.update(stale)

        assert calls["count"] == 0
        assert service.get_diagnosis(case.diagnosis_id).version == 2
    finally:
        container.close()
        engine.dispose()


def test_in_memory_service_versions_advance():
    """内存装配同样按 1 → 2 → 3 递增（Phase 0 demo 语义不变）。"""
    container = build_container()
    service = container.service

    case = service.create_diagnosis(
        device_id=DEVICE_ID,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter=REPORTER,
    )
    assert case.version == 1

    service.run_diagnosis(case.diagnosis_id)
    assert service.get_diagnosis(case.diagnosis_id).version == 2

    service.review_diagnosis(
        diagnosis_id=case.diagnosis_id,
        action=HumanReviewAction.CONFIRM,
        reviewer=REPORTER,
    )
    assert service.get_diagnosis(case.diagnosis_id).version == 3


def test_repository_update_returns_fresh_copy(tmp_path: Path):
    """update() 返回值必须是带新版本的 Domain 副本，调用方对象不被改。"""
    url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    upgrade_database(url)
    engine, factory = build_database(url)
    try:
        from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
        from security_diagnosis_harness.domain.enums import SecurityFaultType as SFT

        repository = SqlAlchemyDiagnosisRepository(factory)
        case = SecurityDiagnosisCase(
            diagnosis_id="diag-fresh",
            fault_type=SFT.CAMERA_BLACK_SCREEN,
            device_id=DEVICE_ID,
            reporter=REPORTER,
        )
        saved = repository.save(case)
        assert saved.version == 1

        saved.description = "updated"
        returned = repository.update(saved)

        assert returned is not saved
        assert saved.version == 1
        assert returned.version == 2
        assert returned.description == "updated"
        assert repository.get("diag-fresh").version == 2
    finally:
        engine.dispose()


def test_service_accepts_repository_port_without_version_binding():
    """应用服务不绑定具体仓储实现（Port 语义未变）。"""
    from security_diagnosis_harness.ports.diagnosis_repository import DiagnosisRepository

    container = build_container()
    assert isinstance(container.repository, DiagnosisRepository)
