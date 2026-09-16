"""正式本地运行装配（Phase 6B-1 / 9C-2B）。

职责：

- `upgrade_database(url)`：程序化执行 Alembic `upgrade head`（**不使用 create_all**）；
- `build_runtime_container(settings)`：按配置装配 SQLite 或内存仓储，
  并独占负责 Engine 的释放；
- 默认设备平面走 `RoutedDeviceGateway`：资产目录 + Adapter Registry +
  内部 StaticDeviceGateway Adapter，`supported_fault_types` 由
  `resolve_fault_support()` 按实际装配结果推导，不再由手工常量决定。

设计约束：

- 不修改 `bootstrap/container.py` 的评测装配（Phase 0～5 继续使用内存 + FakeLLM）；
- Engine 由 RuntimeContainer 独占，`close()` 必须 dispose 且可重复调用；
- 不把 Session 暴露给 Application / API；
- 迁移失败时容器构建失败，不返回可用 service；
- 不访问网络、真实设备、真实 LLM、BGE 或 `.env`。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import NamedTuple

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from security_diagnosis_harness.adapters.audit_in_memory import InMemoryAuditRepository
from security_diagnosis_harness.adapters.audited_write_in_memory import InMemoryAuditedWrite
from security_diagnosis_harness.adapters.device_assets import InMemoryDeviceAssetCatalog
from security_diagnosis_harness.adapters.device_gateway.registry import (
    InMemoryDeviceAdapterRegistry,
)
from security_diagnosis_harness.adapters.device_gateway.routed import RoutedDeviceGateway
from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.adapters.knowledge.in_memory import (
    InMemoryKnowledgeRepository,
)
from security_diagnosis_harness.adapters.llm.fake import FakeLLM
from security_diagnosis_harness.adapters.persistence.audit_repository import (
    SqlAlchemyAuditRepository,
)
from security_diagnosis_harness.adapters.persistence.audited_write import (
    SqlAlchemyAuditedWrite,
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
from security_diagnosis_harness.application.runtime_capabilities import (
    RuntimeCapabilitySupport,
    resolve_fault_support,
)
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
from security_diagnosis_harness.domain.device_integration import (
    DeviceAsset,
    DeviceCapability,
)
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.ports.audit_repository import AuditRepository
from security_diagnosis_harness.ports.audited_write import AuditedWrite
from security_diagnosis_harness.ports.device_gateway import DeviceGateway
from security_diagnosis_harness.ports.diagnosis_repository import DiagnosisRepository
from security_diagnosis_harness.ports.knowledge_repository import KnowledgeRepository
from security_diagnosis_harness.tools.device_channel import DeviceChannelTool
from security_diagnosis_harness.tools.device_stream import DeviceStreamTool
from security_diagnosis_harness.tools.platform_pull import PlatformPullStatusTool
from security_diagnosis_harness.tools.registry import ToolRegistry, supports_fault_type

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


# Phase 9C-2B：正式 Runtime 的 supported_fault_types 由实际装配结果推导
# （资产 + Adapter readiness + 固定自检集合 + 资产能力 + 工具覆盖），
# 不再由手工常量直接决定。默认仍只支持摄像头黑屏——这是推导结果，
# 不是扩大后的 allowlist；本阶段不开放其它三域。
#
# 兼容导出：旧常量仅供既有调用方读取，**不是** Service 的真实输入；
# Service 输入一律来自 `resolve_fault_support()` 的推导结果。
SUPPORTED_RUNTIME_FAULT_TYPES: frozenset[SecurityFaultType] = frozenset(
    {SecurityFaultType.CAMERA_BLACK_SCREEN}
)


# ---------------------------------------------------------------- 能力需求矩阵
# "故障域所需 capability" 与 "故障域所需工具名" 的固定需求矩阵。
# 矩阵是**需求**，不是 supported 结果；supported 必须由
# `resolve_fault_support()` 按实际装配推导。
class _FaultDomainNeeds(NamedTuple):
    """单个故障域完成诊断所需的设备能力与只读工具名。"""

    capabilities: frozenset[DeviceCapability]
    tool_names: frozenset[str]


_FAULT_DOMAIN_REQUIREMENTS: Mapping[SecurityFaultType, _FaultDomainNeeds] = MappingProxyType(
    {
        SecurityFaultType.CAMERA_BLACK_SCREEN: _FaultDomainNeeds(
            capabilities=frozenset(
                {
                    DeviceCapability.STATUS,
                    DeviceCapability.CHANNEL,
                    DeviceCapability.STREAM,
                }
            ),
            tool_names=frozenset(
                {
                    "device__query_status",
                    "device__query_channel",
                    "device__query_stream",
                    "platform__query_pull_status",
                }
            ),
        ),
        SecurityFaultType.RECORDING_MISSING: _FaultDomainNeeds(
            capabilities=frozenset({DeviceCapability.RECORDING}),
            tool_names=frozenset(
                {
                    "recording__query_plan",
                    "storage__query_status",
                    "recording__check_playback",
                }
            ),
        ),
        SecurityFaultType.ACCESS_CARD_FAILED: _FaultDomainNeeds(
            capabilities=frozenset({DeviceCapability.ACCESS}),
            tool_names=frozenset(
                {
                    "access__query_controller",
                    "access__query_door",
                    "access__query_credential",
                    "access__query_policy",
                    "access__search_events",
                }
            ),
        ),
        SecurityFaultType.ALARM_FALSE_POSITIVE: _FaultDomainNeeds(
            capabilities=frozenset({DeviceCapability.ALARM_DIAGNOSIS}),
            tool_names=frozenset(
                {
                    "alarm__query_rule",
                    "alarm__query_signal",
                    "alarm__query_environment",
                    "alarm__query_verification",
                    "alarm__query_correlation",
                }
            ),
        ),
    }
)


# ---------------------------------------------------------------- 默认设备平面
# 默认 Adapter key：非敏感内部别名，仅用于 AssetCatalog ↔ Registry 的路由关联。
DEFAULT_RUNTIME_ADAPTER_KEY = "runtime-static-adapter"


# 默认资产只含非敏感字段；device_id 与既有默认样例
# `samples/devices/static_devices.sample.json` 保持一致。
# capabilities 覆盖摄像头闭环所需的全部只读能力（含 Phase 0 通用告警/配置事实）。
def build_default_runtime_asset() -> DeviceAsset:
    """构造正式 Runtime 的默认非敏感设备资产。"""
    return DeviceAsset(
        device_id="camera-3f-001",
        device_type="camera",
        adapter_key=DEFAULT_RUNTIME_ADAPTER_KEY,
        capabilities=frozenset(
            {
                DeviceCapability.STATUS,
                DeviceCapability.CHANNEL,
                DeviceCapability.STREAM,
                DeviceCapability.ALARM,
                DeviceCapability.CONFIG,
            }
        ),
        enabled=True,
    )


def _tool_supported_fault_types(registry: ToolRegistry) -> frozenset[SecurityFaultType]:
    """从 ToolRegistry 实际注册工具计算 tool-supported fault types。

    某域的**所有**必要工具均存在、且每个工具本身都允许该 fault type，
    才算工具覆盖；不得仅看一个工具。
    """
    supported: set[SecurityFaultType] = set()
    for fault_type, needs in _FAULT_DOMAIN_REQUIREMENTS.items():
        covered = all(
            registry.has(tool_name) and supports_fault_type(registry.get(tool_name), fault_type)
            for tool_name in needs.tool_names
        )
        if covered:
            supported.add(fault_type)
    return frozenset(supported)


def derive_runtime_capability_support(
    *,
    assets: Iterable[DeviceAsset],
    ready_adapter_keys: Iterable[str],
    self_check_passed_adapter_keys: Iterable[str],
    registry: ToolRegistry,
) -> RuntimeCapabilitySupport:
    """按固定需求矩阵与实际装配推导各故障域的支持判定。

    纯装配推导：只读取入参与 ToolRegistry 的注册状态，不访问
    环境、网络、数据库或真实设备；不修改 Router 内部状态。
    """
    return resolve_fault_support(
        assets=assets,
        ready_adapter_keys=ready_adapter_keys,
        required_capabilities_by_fault_type={
            fault_type: needs.capabilities
            for fault_type, needs in _FAULT_DOMAIN_REQUIREMENTS.items()
        },
        tool_supported_fault_types=_tool_supported_fault_types(registry),
        self_check_passed_adapter_keys=self_check_passed_adapter_keys,
    )


def supported_runtime_fault_types() -> frozenset[SecurityFaultType]:
    """正式 Runtime 当前支持的故障类型集合（由默认装配推导）。

    兼容说明：旧契约返回 frozenset，这里对 resolver 输出做一次 frozenset
    转换，既有调用方的类型预期保持不变。
    """
    return frozenset(
        derive_runtime_capability_support(
            assets=(build_default_runtime_asset(),),
            ready_adapter_keys=(DEFAULT_RUNTIME_ADAPTER_KEY,),
            self_check_passed_adapter_keys=(DEFAULT_RUNTIME_ADAPTER_KEY,),
            registry=build_camera_registry(),
        ).supported_fault_types
    )


@dataclass
class RuntimeContainer:
    """正式本地运行装配结果。

    与评测用 `bootstrap.Container` 分离：这里持有 Engine 生命周期的所有权。
    `capability_support` / `asset_catalog` / `adapter_registry` 以只读属性暴露。
    """

    settings: RuntimeSettings
    service: SecurityDiagnosisApplicationService
    repository: DiagnosisRepository
    engine: Engine | None
    session_factory: sessionmaker[Session] | None
    runner: ToolLoopRunner
    registry: ToolRegistry
    # Phase 9C-2B：契约是 DeviceGateway；默认实例是 RoutedDeviceGateway，
    # StaticDeviceGateway 只作为 Router 内部 Adapter，不再直接传给 Service。
    gateway: DeviceGateway
    llm: FakeLLM
    citation_policy: CitationPolicy
    audit_repository: AuditRepository
    knowledge_repository: KnowledgeRepository
    audited_write: AuditedWrite
    knowledge_service: KnowledgeGovernanceApplicationService
    consistency_scanner: ConsistencyScanner
    _asset_catalog: InMemoryDeviceAssetCatalog
    _adapter_registry: InMemoryDeviceAdapterRegistry
    _capability_support: RuntimeCapabilitySupport
    _closed: bool = False

    # ------------------------------------------------------ 只读能力装配视图
    @property
    def asset_catalog(self) -> InMemoryDeviceAssetCatalog:
        """默认设备资产目录（只读访问）。"""
        return self._asset_catalog

    @property
    def adapter_registry(self) -> InMemoryDeviceAdapterRegistry:
        """Adapter Registry（只读访问；ready 状态由装配阶段决定）。"""
        return self._adapter_registry

    @property
    def capability_support(self) -> RuntimeCapabilitySupport:
        """`resolve_fault_support()` 的推导结果（只读、不可变）。"""
        return self._capability_support

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
    assets: Iterable[DeviceAsset] | None = None,
    adapter_ready: bool = True,
    self_check_passed_adapter_keys: Iterable[str] | None = None,
    registry: ToolRegistry | None = None,
    device_adapter: DeviceGateway | None = None,
) -> RuntimeContainer:
    """按配置装配正式运行环境。

    Args:
        settings: 运行配置；为 None 时读取环境变量与默认值。
        device_data_path: 设备样例数据路径（默认沿用 Phase 0 样例）。
        assets: 注入设备资产目录内容；缺省时使用默认非敏感摄像头资产
            （device_id 与既有默认样例一致）。
        adapter_ready: 默认 Adapter 是否标记 ready。
        self_check_passed_adapter_keys: 固定自检通过集合；缺省时由装配参数
            显式构造（adapter ready 即默认 key 通过，否则为空），
            Router 不自行猜测自检结果。
        registry: 注入 ToolRegistry；缺省时使用摄像头运行时注册表。
            supported fault types 一律按该注册表实际注册工具推导。
        device_adapter: Phase 9C-3 显式注入的 DeviceGateway Adapter（测试 /
            影子运行用）；缺省时使用内部 StaticDeviceGateway，默认行为完全
            不变。Adapter 注册在 DEFAULT_RUNTIME_ADAPTER_KEY 下，readiness
            由 `adapter_ready` 决定；不形成全局单例、不访问网络。

    Raises:
        RuntimeConfigurationError: 配置非法。
        Exception: sqlite 模式迁移失败时向上抛，不返回容器。
    """
    resolved = settings or build_runtime_settings()

    data_path = Path(device_data_path) if device_data_path else DEFAULT_DEVICE_DATA_PATH
    # 正式入口默认提供摄像头黑屏确定性 responder（含 Phase 1 只读工具），
    # 与 Phase 0/1 demo 使用同一套固定脚本，不调用真实模型。
    resolved_registry = registry if registry is not None else build_camera_registry()
    llm = FakeLLM(responder=build_camera_black_screen_responder(include_camera_tools=True))
    runner = ToolLoopRunner(llm, resolved_registry, ToolLoopBudget(max_rounds=3, max_tool_calls=8))
    citation_policy = CitationPolicy()

    # ------------------------------------------------ 设备平面：Catalog + Registry
    resolved_assets = tuple(assets) if assets is not None else (build_default_runtime_asset(),)
    # Phase 9C-3：允许显式注入受控 Adapter（如 SimulatorDeviceGateway）；
    # 缺省时仍使用内部 StaticDeviceGateway，默认装配行为完全不变。
    runtime_adapter = (
        device_adapter if device_adapter is not None else StaticDeviceGateway(data_path)
    )
    asset_catalog = InMemoryDeviceAssetCatalog(resolved_assets)
    adapter_registry = InMemoryDeviceAdapterRegistry()
    # Adapter 注册为 Router 内部 Adapter；ready 状态由装配参数决定。
    adapter_registry.register(
        DEFAULT_RUNTIME_ADAPTER_KEY,
        runtime_adapter,
        ready=adapter_ready,
    )
    gateway = RoutedDeviceGateway(asset_catalog, adapter_registry)

    # ------------------------------------------------ 能力推导（9C-2A resolver）
    if self_check_passed_adapter_keys is not None:
        resolved_self_check_keys = frozenset(self_check_passed_adapter_keys)
    else:
        resolved_self_check_keys = (
            frozenset({DEFAULT_RUNTIME_ADAPTER_KEY}) if adapter_ready else frozenset()
        )
    capability_support = derive_runtime_capability_support(
        assets=resolved_assets,
        ready_adapter_keys=adapter_registry.ready_adapter_keys(),
        self_check_passed_adapter_keys=resolved_self_check_keys,
        registry=resolved_registry,
    )
    # Service 的 supported 集合只能来自 resolver 推导结果。
    derived_supported_fault_types = frozenset(capability_support.supported_fault_types)

    engine: Engine | None = None
    session_factory: sessionmaker[Session] | None = None
    repository: DiagnosisRepository
    audit_repository: AuditRepository
    knowledge_repository: KnowledgeRepository
    audited_write: AuditedWrite

    if resolved.repository_mode is RepositoryMode.SQLITE:
        try:
            if resolved.auto_migrate:
                upgrade_database(resolved.database_url)
            engine = build_engine(resolved.database_url, echo=resolved.database_echo)
            session_factory = build_session_factory(engine)
            repository = SqlAlchemyDiagnosisRepository(session_factory)
            audit_repository = SqlAlchemyAuditRepository(session_factory)
            knowledge_repository = SqlAlchemyKnowledgeRepository(session_factory)
            audited_write = SqlAlchemyAuditedWrite(session_factory)
        except Exception:
            # 迁移 / 建 Engine 失败时必须释放已创建资源，且不返回容器。
            if engine is not None:
                engine.dispose()
            raise
    else:
        transaction_lock = RLock()
        repository = InMemoryDiagnosisRepository(transaction_lock)
        audit_repository = InMemoryAuditRepository(transaction_lock)
        knowledge_repository = InMemoryKnowledgeRepository(transaction_lock)
        audited_write = InMemoryAuditedWrite(
            repository, knowledge_repository, audit_repository, transaction_lock
        )

    service = SecurityDiagnosisApplicationService(
        repository=repository,
        runner=runner,
        registry=resolved_registry,
        gateway=gateway,
        citation_policy=citation_policy,
        # 显式能力约束：由实际装配推导，不再是手工常量。
        supported_fault_types=derived_supported_fault_types,
        audit_repository=audit_repository,
        audited_write=audited_write,
    )
    knowledge_service = KnowledgeGovernanceApplicationService(
        KnowledgeCandidateApplicationService(service),
        knowledge_repository,
        audit_repository,
        audited_write,
    )
    consistency_scanner = ConsistencyScanner(
        repository, knowledge_repository, audit_repository
    )
    return RuntimeContainer(
        settings=resolved,
        service=service,
        repository=repository,
        engine=engine,
        session_factory=session_factory,
        runner=runner,
        registry=resolved_registry,
        gateway=gateway,
        llm=llm,
        citation_policy=citation_policy,
        audit_repository=audit_repository,
        knowledge_repository=knowledge_repository,
        audited_write=audited_write,
        knowledge_service=knowledge_service,
        consistency_scanner=consistency_scanner,
        _asset_catalog=asset_catalog,
        _adapter_registry=adapter_registry,
        _capability_support=capability_support,
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
    "DEFAULT_RUNTIME_ADAPTER_KEY",
    "MIGRATIONS_DIR",
    "SUPPORTED_RUNTIME_FAULT_TYPES",
    "RuntimeContainer",
    "build_camera_registry",
    "build_default_runtime_asset",
    "build_runtime_container",
    "derive_runtime_capability_support",
    "supported_runtime_fault_types",
    "upgrade_database",
]
