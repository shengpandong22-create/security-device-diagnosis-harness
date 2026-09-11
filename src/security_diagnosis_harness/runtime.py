"""正式本地运行装配（Phase 6B-1）。

职责：

- `upgrade_database(url)`：程序化执行 Alembic `upgrade head`（**不使用 create_all**）；
- `build_runtime_container(settings)`：按配置装配 SQLite 或内存仓储，
  并独占负责 Engine 的释放。

设计约束：

- 不修改 `bootstrap/container.py` 的评测装配（Phase 0～5 继续使用内存 + FakeLLM）；
- Engine 由 RuntimeContainer 独占，`close()` 必须 dispose 且可重复调用；
- 不把 Session 暴露给 Application / API；
- 迁移失败时容器构建失败，不返回可用 service。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from security_diagnosis_harness.adapters.audit_in_memory import InMemoryAuditRepository
from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.adapters.knowledge.in_memory import (
    InMemoryKnowledgeRepository,
)
from security_diagnosis_harness.adapters.llm.fake import FakeLLM
from security_diagnosis_harness.adapters.persistence.audit_repository import (
    SqlAlchemyAuditRepository,
)
from security_diagnosis_harness.adapters.persistence.database import (
    build_engine,
    build_session_factory,
    ensure_sqlite_directory,
)
from security_diagnosis_harness.adapters.persistence.diagnosis_repository import (
    SqlAlchemyDiagnosisRepository,
)
from security_diagnosis_harness.adapters.persistence.knowledge_repository import (
    SqlAlchemyKnowledgeRepository,
)
from security_diagnosis_harness.agent.runner import ToolLoopBudget, ToolLoopRunner
from security_diagnosis_harness.application.consistency import ConsistencyScanner
from security_diagnosis_harness.application.diagnoses import (
    SecurityDiagnosisApplicationService,
)
from security_diagnosis_harness.application.knowledge_candidates import (
    KnowledgeCandidateApplicationService,
)
from security_diagnosis_harness.application.knowledge_governance import (
    KnowledgeGovernanceApplicationService,
)
from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository
from security_diagnosis_harness.bootstrap.container import (
    DEFAULT_DEVICE_DATA_PATH,
    build_camera_black_screen_responder,
)
from security_diagnosis_harness.config import (
    RepositoryMode,
    RuntimeConfigurationError,
    RuntimeSettings,
    build_runtime_settings,
)
from security_diagnosis_harness.domain.citation_policy import CitationPolicy
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.ports.audit_repository import AuditRepository
from security_diagnosis_harness.ports.diagnosis_repository import DiagnosisRepository
from security_diagnosis_harness.ports.knowledge_repository import KnowledgeRepository
from security_diagnosis_harness.tools.device_channel import DeviceChannelTool
from security_diagnosis_harness.tools.device_stream import DeviceStreamTool
from security_diagnosis_harness.tools.platform_pull import PlatformPullStatusTool
from security_diagnosis_harness.tools.registry import ToolRegistry

_REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI_PATH = _REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = _REPO_ROOT / "migrations"


def _build_alembic_config(database_url: str) -> Config:
    """构造 Alembic Config：脚本路径基于源码目录，不依赖调用者 cwd。"""
    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # 通过 Config 注入 URL，不修改全局 alembic.ini 文件。
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def upgrade_database(database_url: str) -> None:
    """程序化执行 Alembic `upgrade head`。

    Args:
        database_url: 目标数据库 URL（本阶段只允许本地 SQLite）。

    Raises:
        RuntimeConfigurationError: URL 非法。
        Exception: 迁移失败时原样向上抛，由调用方决定容器构建失败。
    """
    if "://" not in database_url or not database_url.startswith("sqlite"):
        raise RuntimeConfigurationError("本阶段只允许本地 SQLite 数据库")

    ensure_sqlite_directory(database_url)
    config = _build_alembic_config(database_url)
    # 不使用 Base.metadata.create_all()，schema 完全由 Alembic 迁移管理。
    command.upgrade(config, "head")


# Phase 6B-1 尚未实现四故障域统一 Strategy Router，正式 Runtime 只装配
# 摄像头黑屏诊断能力（工具白名单 + 确定性 responder 都是摄像头专用）。
# 录像 / 门禁 / 报警的正式运行路由留给后续阶段，绝不能用宽泛 allowlist 假装支持。
SUPPORTED_RUNTIME_FAULT_TYPES: frozenset[SecurityFaultType] = frozenset(
    {SecurityFaultType.CAMERA_BLACK_SCREEN}
)


def supported_runtime_fault_types() -> frozenset[SecurityFaultType]:
    """正式 Runtime 当前支持的故障类型集合。"""
    return SUPPORTED_RUNTIME_FAULT_TYPES


@dataclass
class RuntimeContainer:
    """正式本地运行装配结果。

    与评测用 `bootstrap.Container` 分离：这里持有 Engine 生命周期的所有权。
    """

    settings: RuntimeSettings
    service: SecurityDiagnosisApplicationService
    repository: DiagnosisRepository
    engine: Engine | None
    session_factory: sessionmaker[Session] | None
    runner: ToolLoopRunner
    registry: ToolRegistry
    gateway: StaticDeviceGateway
    llm: FakeLLM
    citation_policy: CitationPolicy
    audit_repository: AuditRepository
    knowledge_repository: KnowledgeRepository
    knowledge_service: KnowledgeGovernanceApplicationService
    consistency_scanner: ConsistencyScanner
    _closed: bool = False

    # ------------------------------------------------------------------ 生命周期
    def close(self) -> None:
        """释放 Engine；可重复调用。memory 模式下安全无副作用。"""
        if self._closed:
            return
        self._closed = True
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
            self.session_factory = None

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> RuntimeContainer:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    # ------------------------------------------------------------------ health
    def database_ready(self) -> bool:
        """运行时仓储是否已完成初始化（不是硬编码）。"""
        if self._closed:
            return False
        if self.settings.repository_mode is RepositoryMode.MEMORY:
            return True
        return self.engine is not None and self.session_factory is not None


def build_runtime_container(
    settings: RuntimeSettings | None = None,
    *,
    device_data_path: str | Path | None = None,
) -> RuntimeContainer:
    """按配置装配正式运行环境。

    Args:
        settings: 运行配置；为 None 时读取环境变量与默认值。
        device_data_path: 设备样例数据路径（默认沿用 Phase 0 样例）。

    Raises:
        RuntimeConfigurationError: 配置非法。
        Exception: sqlite 模式迁移失败时向上抛，不返回容器。
    """
    resolved = settings or build_runtime_settings()

    data_path = Path(device_data_path) if device_data_path else DEFAULT_DEVICE_DATA_PATH
    gateway = StaticDeviceGateway(data_path)
    # 正式入口默认提供摄像头黑屏确定性 responder（含 Phase 1 只读工具），
    # 与 Phase 0/1 demo 使用同一套固定脚本，不调用真实模型。
    registry = build_camera_registry()
    llm = FakeLLM(responder=build_camera_black_screen_responder(include_camera_tools=True))
    runner = ToolLoopRunner(llm, registry, ToolLoopBudget(max_rounds=3, max_tool_calls=8))
    citation_policy = CitationPolicy()

    engine: Engine | None = None
    session_factory: sessionmaker[Session] | None = None
    repository: DiagnosisRepository
    audit_repository: AuditRepository
    knowledge_repository: KnowledgeRepository

    if resolved.repository_mode is RepositoryMode.SQLITE:
        try:
            if resolved.auto_migrate:
                upgrade_database(resolved.database_url)
            engine = build_engine(resolved.database_url, echo=resolved.database_echo)
            session_factory = build_session_factory(engine)
            repository = SqlAlchemyDiagnosisRepository(session_factory)
            audit_repository = SqlAlchemyAuditRepository(session_factory)
            knowledge_repository = SqlAlchemyKnowledgeRepository(session_factory)
        except Exception:
            # 迁移 / 建 Engine 失败时必须释放已创建资源，且不返回容器。
            if engine is not None:
                engine.dispose()
            raise
    else:
        repository = InMemoryDiagnosisRepository()
        audit_repository = InMemoryAuditRepository()
        knowledge_repository = InMemoryKnowledgeRepository()

    service = SecurityDiagnosisApplicationService(
        repository=repository,
        runner=runner,
        registry=registry,
        gateway=gateway,
        citation_policy=citation_policy,
        # 显式能力约束：正式 Runtime 只支持已装配的故障类型。
        supported_fault_types=SUPPORTED_RUNTIME_FAULT_TYPES,
        audit_repository=audit_repository,
    )
    knowledge_service = KnowledgeGovernanceApplicationService(
        KnowledgeCandidateApplicationService(service),
        knowledge_repository,
        audit_repository,
    )
    consistency_scanner = ConsistencyScanner(repository, knowledge_repository)
    return RuntimeContainer(
        settings=resolved,
        service=service,
        repository=repository,
        engine=engine,
        session_factory=session_factory,
        runner=runner,
        registry=registry,
        gateway=gateway,
        llm=llm,
        citation_policy=citation_policy,
        audit_repository=audit_repository,
        knowledge_repository=knowledge_repository,
        knowledge_service=knowledge_service,
        consistency_scanner=consistency_scanner,
    )


# 注意：这里刻意**不**提供 `build_runtime_service()` 之类的入口。
# 任何"创建 Engine 却只返回 service"的 builder 都会丢失资源 owner，
# 调用方无法 dispose Engine。正式入口必须使用 `build_runtime_container()`
# 并通过 `close()` / context manager 释放资源。


def build_camera_registry() -> ToolRegistry:
    """运行时工具注册表：Phase 0 基础工具 + Phase 1 摄像头只读工具。"""
    from security_diagnosis_harness.bootstrap.container import build_registry

    registry = build_registry()
    for tool_cls in (DeviceChannelTool, DeviceStreamTool, PlatformPullStatusTool):
        tool = tool_cls()
        if not registry.has(tool.name):
            registry.register(tool)
    return registry


__all__ = [
    "ALEMBIC_INI_PATH",
    "MIGRATIONS_DIR",
    "SUPPORTED_RUNTIME_FAULT_TYPES",
    "RuntimeContainer",
    "build_camera_registry",
    "build_runtime_container",
    "supported_runtime_fault_types",
    "upgrade_database",
]
