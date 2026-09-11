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

from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.adapters.llm.fake import FakeLLM
from security_diagnosis_harness.adapters.persistence.database import (
    build_engine,
    build_session_factory,
    ensure_sqlite_directory,
)
from security_diagnosis_harness.adapters.persistence.diagnosis_repository import (
    SqlAlchemyDiagnosisRepository,
)
from security_diagnosis_harness.agent.runner import ToolLoopBudget, ToolLoopRunner
from security_diagnosis_harness.application.diagnoses import (
    SecurityDiagnosisApplicationService,
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
from security_diagnosis_harness.ports.diagnosis_repository import DiagnosisRepository
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

    if resolved.repository_mode is RepositoryMode.SQLITE:
        try:
            if resolved.auto_migrate:
                upgrade_database(resolved.database_url)
            engine = build_engine(resolved.database_url, echo=resolved.database_echo)
            session_factory = build_session_factory(engine)
            repository = SqlAlchemyDiagnosisRepository(session_factory)
        except Exception:
            # 迁移 / 建 Engine 失败时必须释放已创建资源，且不返回容器。
            if engine is not None:
                engine.dispose()
            raise
    else:
        repository = InMemoryDiagnosisRepository()

    service = SecurityDiagnosisApplicationService(
        repository=repository,
        runner=runner,
        registry=registry,
        gateway=gateway,
        citation_policy=citation_policy,
    )
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
    )


def build_runtime_service(
    settings: RuntimeSettings | None = None,
) -> SecurityDiagnosisApplicationService:
    """仅返回服务（调用方负责通过 RuntimeContainer 管理 Engine 生命周期）。

    用法提示：直接调用本函数会失去 `close()` 能力，正式入口请使用
    `build_runtime_container()`。
    """
    return build_runtime_container(settings).service


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
    "RuntimeContainer",
    "build_camera_registry",
    "build_runtime_container",
    "build_runtime_service",
    "upgrade_database",
]
