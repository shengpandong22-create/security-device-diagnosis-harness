"""Phase 6B-1：正式运行装配 RuntimeContainer 验收。"""

from __future__ import annotations

from pathlib import Path

import pytest

from security_diagnosis_harness.application.knowledge_seeds import (
    load_manual_knowledge_seeds,
)
from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository
from security_diagnosis_harness.bootstrap.container import (
    build_container,
    build_phase1_container,
    build_phase2_container,
    build_phase3_container,
    build_phase4_container,
)
from security_diagnosis_harness.config import RepositoryMode, RuntimeSettings
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.knowledge import (
    KnowledgeReviewAction,
)
from security_diagnosis_harness.runtime import (
    FORMAL_RUNTIME_TOOL_ALLOWLIST,
    build_runtime_container,
)
from security_diagnosis_harness.tools.contracts import ToolExecutionContext, ToolPermission


def _sqlite_settings(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings(
        repository_mode="sqlite",
        database_url=f"sqlite:///{(tmp_path / 'runtime.db').as_posix()}",
        auto_migrate=True,
    )


# ---------------------------------------------------------------- 装配
def test_sqlite_mode_uses_sqlalchemy_repository(tmp_path: Path):
    with build_runtime_container(_sqlite_settings(tmp_path)) as runtime:
        assert runtime.settings.repository_mode is RepositoryMode.SQLITE
        assert runtime.database_ready() is True
        assert not hasattr(runtime.repository, "save")
        assert not hasattr(runtime.repository, "update")


def test_memory_mode_uses_in_memory_repository():
    settings = RuntimeSettings(repository_mode="memory")

    with build_runtime_container(settings) as runtime:
        assert runtime.database_ready() is True
        assert not hasattr(runtime, "engine")
        assert not hasattr(runtime, "session_factory")


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
    assert runtime.database_ready() is True
    runtime.close()

    assert runtime.database_ready() is False


def test_close_is_idempotent(tmp_path: Path):
    runtime = build_runtime_container(_sqlite_settings(tmp_path))

    runtime.close()
    runtime.close()
    runtime.close()


def test_memory_close_is_safe_and_side_effect_free():
    runtime = build_runtime_container(RuntimeSettings(repository_mode="memory"))

    runtime.close()
    runtime.close()
    assert runtime.closed is True


def test_context_manager_closes_engine(tmp_path: Path):
    settings = _sqlite_settings(tmp_path)
    with build_runtime_container(settings) as runtime:
        assert runtime.database_ready() is True

    assert runtime.database_ready() is False


def test_runtime_exposes_required_components(tmp_path: Path):
    with build_runtime_container(_sqlite_settings(tmp_path)) as runtime:
        assert runtime.service is not None
        assert runtime.runner is not None
        assert runtime.registry is not None
        assert runtime.gateway is not None
        assert runtime.llm is not None
        assert runtime.citation_policy is not None
        assert not hasattr(runtime, "session_factory")
        assert not hasattr(runtime, "engine")
        assert not hasattr(runtime, "audited_write")


def test_formal_runtime_passes_explicit_minimal_tool_allowlist():
    with build_runtime_container(RuntimeSettings(repository_mode="memory")) as runtime:
        assert set(FORMAL_RUNTIME_TOOL_ALLOWLIST) <= set(runtime.registry.names())
        assert not hasattr(runtime.service, "_tool_allowlist")


def test_formal_runtime_knowledge_tool_reads_confirmed_repository_only():
    with build_runtime_container(RuntimeSettings(repository_mode="memory")) as runtime:
        seed_path = (
            Path(__file__).resolve().parents[2]
            / "samples"
            / "knowledge"
            / "default_sops.manual-seeds.json"
        )
        seed = load_manual_knowledge_seeds(seed_path)[0]
        candidate = runtime.knowledge_service.import_manual_seed(seed, "seed-operator")
        runtime.knowledge_service.review(
            candidate.knowledge_id,
            KnowledgeReviewAction.CONFIRM,
            reviewer="runtime-reviewer",
        )
        context = ToolExecutionContext(
            diagnosis_id="diag-current",
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            permissions=frozenset({ToolPermission.KNOWLEDGE_READ}),
        )

        result = runtime.registry.execute(
            "knowledge__search", {"query": seed.title, "limit": 3}, context
        )

        assert result.ok is True
        assert [item["sop_id"] for item in result.evidence_drafts[0].payload["sops"]] == [
            candidate.knowledge_id
        ]
        assert all(
            item["sop_id"] != seed.artifact_id
            for item in result.evidence_drafts[0].payload["sops"]
        )


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
        assert first.knowledge_repository is not second.knowledge_repository
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


# ---------------------------------------------------------------- 授权策略与生命周期
def test_default_authorization_is_minimal_and_finite():
    from datetime import UTC, datetime, timedelta

    from security_diagnosis_harness.device_authorization import (
        READ_ONLY_OPERATIONS,
        DeviceReadOperation,
    )

    with build_runtime_container(RuntimeSettings(repository_mode="memory")) as runtime:
        manifest = runtime.authorization.manifest
        assert manifest is not None
        # 明确标记为本地静态样例，不冒充真实设备授权。
        assert manifest.manifest_id == "local-static-sample-read-only"
        assert manifest.environment_alias == "local-static-sample"
        # 操作集是工具 allowlist 与资产能力的最小交集，是真子集（非全部只读操作）。
        assert manifest.allowed_operations < READ_ONLY_OPERATIONS
        assert manifest.allowed_operations == frozenset(
            {
                DeviceReadOperation.QUERY_STATUS,
                DeviceReadOperation.QUERY_CHANNEL_SNAPSHOT,
                DeviceReadOperation.QUERY_STREAM_SNAPSHOT,
                DeviceReadOperation.QUERY_PLATFORM_PULL_STATUS,
                DeviceReadOperation.SEARCH_ALARM_EVENTS,
                DeviceReadOperation.READ_CONFIG_SNAPSHOT,
            }
        )
        # 时间窗有限，不再是 datetime.min/max 的"近似无限"窗口。
        assert manifest.valid_from > datetime.min.replace(tzinfo=UTC)
        assert manifest.valid_until < datetime.max.replace(tzinfo=UTC)
        assert manifest.valid_until - manifest.valid_from <= timedelta(hours=24)


def test_explicit_device_adapter_requires_explicit_authorization():
    from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
    from security_diagnosis_harness.bootstrap.container import DEFAULT_DEVICE_DATA_PATH
    from security_diagnosis_harness.config import RuntimeConfigurationError

    adapter = StaticDeviceGateway(DEFAULT_DEVICE_DATA_PATH)
    with pytest.raises(RuntimeConfigurationError):
        build_runtime_container(
            RuntimeSettings(repository_mode="memory"), device_adapter=adapter
        )


def test_close_closes_authorization_session():
    runtime = build_runtime_container(RuntimeSettings(repository_mode="memory"))

    assert runtime.authorization.closed is False
    runtime.close()
    assert runtime.authorization.closed is True
    # close 幂等，不改变终态。
    runtime.close()
    assert runtime.authorization.closed is True


def _replacement_manifest():
    from datetime import UTC, datetime, timedelta

    from security_diagnosis_harness.device_authorization import (
        AuthorizationManifest,
        DeviceReadOperation,
    )

    start = datetime.now(UTC)
    return AuthorizationManifest(
        manifest_id="rotation-attempt-manifest",
        environment_alias="local-static-sample",
        asset_scope_aliases=frozenset({"camera-3f-001"}),
        credential_ref="runtime:configured",
        valid_from=start,
        valid_until=start + timedelta(hours=1),
        allowed_operations=frozenset({DeviceReadOperation.QUERY_STATUS}),
        max_total_calls=10,
        max_calls_per_operation={DeviceReadOperation.QUERY_STATUS: 10},
    )


def test_runtime_authorization_view_cannot_rotate_or_reset_budget():
    """公开只读快照无法替换 manifest、重置预算或撤销会话。"""
    from security_diagnosis_harness.device_authorization import DeviceAuthorizationSession

    runtime = build_runtime_container(RuntimeSettings(repository_mode="memory"))
    try:
        view = runtime.authorization
        # 不是原始会话，也没有任何可变更入口。
        assert not isinstance(view, DeviceAuthorizationSession)
        for forbidden in ("rotate", "authorize", "close"):
            assert not hasattr(view, forbidden), forbidden
        # 内部状态即使绕过 __getattr__ 也不可达。
        with pytest.raises(AttributeError):
            _ = view._session
        with pytest.raises(AttributeError):
            object.__getattribute__(view, "_session")

        # 网关内部仍共享同一个强制预算载体：真实调用后预算被消费。
        runtime.gateway.query_status("camera-3f-001")
        consumed = view.budget_state.total_calls_consumed
        batch_index = view.batch_index
        assert consumed == 1

        # 公开调用方无法 rotate：批次与预算都不被重置。
        with pytest.raises(AttributeError):
            view.rotate(_replacement_manifest())
        assert view.batch_index == batch_index
        assert view.budget_state.total_calls_consumed == consumed
        assert runtime.authorization.manifest.manifest_id == "local-static-sample-read-only"
    finally:
        runtime.close()


def test_runtime_authorization_view_cannot_bypass_after_close():
    """容器关闭后，关闭前取得的旧视图也不能用来重置预算。"""
    runtime = build_runtime_container(RuntimeSettings(repository_mode="memory"))
    view = runtime.authorization
    runtime.close()

    assert view.closed is True
    assert not hasattr(view, "rotate")
    assert not hasattr(view, "authorize")
    assert not hasattr(view, "close")
    with pytest.raises(AttributeError):
        view.rotate(_replacement_manifest())
    with pytest.raises(AttributeError):
        view.authorize(asset_alias="camera-3f-001", operation="query_status")


# ---------------------------------------------------------------- 关闭后生命周期
def test_sqlite_closed_container_rejects_all_entry_points(tmp_path: Path):
    from security_diagnosis_harness.runtime import RuntimeClosedError

    with build_runtime_container(_sqlite_settings(tmp_path)) as runtime:
        pass

    with pytest.raises(RuntimeClosedError):
        _ = runtime.service
    with pytest.raises(RuntimeClosedError):
        _ = runtime.repository
    with pytest.raises(RuntimeClosedError):
        _ = runtime.gateway


def test_memory_closed_container_rejects_entry_points():
    from security_diagnosis_harness.runtime import RuntimeClosedError

    runtime = build_runtime_container(RuntimeSettings(repository_mode="memory"))
    runtime.close()

    with pytest.raises(RuntimeClosedError):
        _ = runtime.service
    with pytest.raises(RuntimeClosedError):
        runtime.ensure_open()


def test_held_runtime_entry_points_are_revoked_after_close():
    from security_diagnosis_harness.runtime import RuntimeClosedError

    runtime = build_runtime_container(RuntimeSettings(repository_mode="memory"))
    repository = runtime.repository
    audit_repository = runtime.audit_repository
    knowledge_repository = runtime.knowledge_repository
    knowledge_service = runtime.knowledge_service
    runner = runtime.runner
    registry = runtime.registry
    gateway = runtime.gateway
    calls = (
        repository.list,
        audit_repository.list_all,
        knowledge_repository.list_all,
        knowledge_service.generate_and_save,
        runner.run,
        registry.names,
        gateway.query_status,
    )
    runtime.close()

    for call in calls:
        with pytest.raises(RuntimeClosedError):
            call()


def test_runtime_registry_view_never_exposes_adapters():
    from security_diagnosis_harness.runtime import DEFAULT_RUNTIME_ADAPTER_KEY

    runtime = build_runtime_container(RuntimeSettings(repository_mode="memory"))
    view = runtime.adapter_registry
    assert view.ready_adapter_keys() == (DEFAULT_RUNTIME_ADAPTER_KEY,)
    assert not hasattr(view, "get")
    assert not hasattr(view, "get_ready")
    with pytest.raises(AttributeError):
        _ = runtime.repository._target
    with pytest.raises(AttributeError):
        _ = runtime.repository.__dict__
    with pytest.raises(AttributeError):
        object.__getattribute__(runtime.repository, "_target")
    assert not hasattr(runtime.registry, "get")
    assert not hasattr(runtime.knowledge_repository, "save")
    assert not hasattr(runtime.audit_repository, "append")
    assert not hasattr(runtime.runner, "_registry")
    assert not hasattr(runtime.service, "_repository")
    runtime.close()


def test_sqlite_session_factory_is_not_exposed(tmp_path: Path):
    runtime = build_runtime_container(_sqlite_settings(tmp_path))
    try:
        assert not hasattr(runtime, "session_factory")
        assert not hasattr(runtime, "engine")
    finally:
        runtime.close()


def test_formal_runtime_requires_audit_assembly(monkeypatch):
    from security_diagnosis_harness import runtime as runtime_module
    from security_diagnosis_harness.config import RuntimeConfigurationError

    monkeypatch.setattr(runtime_module, "InMemoryAuditRepository", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeConfigurationError, match="AuditRepository"):
        build_runtime_container(RuntimeSettings(repository_mode="memory"))
