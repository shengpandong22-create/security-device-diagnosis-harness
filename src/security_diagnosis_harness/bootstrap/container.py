"""最小 Container：Phase 0C 全部使用本地/假实现。

- DeviceGateway: StaticDeviceGateway，只读 `samples/devices/static_devices.sample.json`；
- LLM: FakeLLM（脚本模式），不发网络请求、不读 .env；
- 仓储: 内存 dict；
- 工具: 四个 READ_ONLY 只读工具。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.adapters.llm.fake import FakeLLM, Responder
from security_diagnosis_harness.agent.runner import ToolLoopBudget, ToolLoopRunner
from security_diagnosis_harness.application.diagnoses import (
    SecurityDiagnosisApplicationService,
)
from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository
from security_diagnosis_harness.domain.citation_policy import CitationPolicy
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.ports.llm import (
    ChatRole,
    ConclusionDraft,
    FinishReason,
    LLMRequest,
    LLMResponse,
    ToolCall,
)
from security_diagnosis_harness.tools.device_alarm_events import DeviceAlarmEventsTool
from security_diagnosis_harness.tools.device_channel import DeviceChannelTool
from security_diagnosis_harness.tools.device_config import DeviceConfigSnapshotTool
from security_diagnosis_harness.tools.device_status import DeviceStatusTool
from security_diagnosis_harness.tools.device_stream import DeviceStreamTool
from security_diagnosis_harness.tools.knowledge_search import KnowledgeSearchTool
from security_diagnosis_harness.tools.platform_pull import PlatformPullStatusTool
from security_diagnosis_harness.tools.registry import ToolRegistry

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples" / "devices"
DEFAULT_DEVICE_DATA_PATH = SAMPLES_DIR / "static_devices.sample.json"
CAMERA_CASES_DATA_PATH = SAMPLES_DIR / "camera_black_screen_cases.json"

PHASE0_TOOLS: tuple[str, ...] = (
    "device__query_status",
    "device__search_alarm_events",
    "device__read_config_snapshot",
    "knowledge__search",
)

PHASE1_TOOLS: tuple[str, ...] = (
    "device__query_status",
    "device__query_channel",
    "device__query_stream",
    "platform__query_pull_status",
    "device__search_alarm_events",
    "device__read_config_snapshot",
    "knowledge__search",
)


def _make_responder(include_camera_tools: bool) -> Responder:
    """构造摄像头黑屏闭环脚本。

    第一轮请求只读工具；拿到工具结果后给出 probable 候选结论。
    结论的 `cited_evidence_ids` 故意留空，由应用服务按刚刚落地的
    Evidence 做最小引用修正，从而验证 CitationPolicy 不被绕过。

    `include_camera_tools=True` 时会额外请求通道/码流/平台拉流三个 Phase 1 工具。
    """

    def responder(request: LLMRequest) -> LLMResponse:
        device_id = str(request.metadata.get("device_id", ""))
        has_tool_results = any(message.role is ChatRole.TOOL for message in request.messages)

        if not has_tool_results:
            calls = [
                ToolCall(
                    call_id="c1",
                    tool_name="device__query_status",
                    arguments={"device_id": device_id},
                ),
                ToolCall(
                    call_id="c2",
                    tool_name="device__search_alarm_events",
                    arguments={"device_id": device_id, "limit": 10},
                ),
                ToolCall(
                    call_id="c3",
                    tool_name="device__read_config_snapshot",
                    arguments={"device_id": device_id},
                ),
                ToolCall(
                    call_id="c4",
                    tool_name="knowledge__search",
                    arguments={"query": "摄像头黑屏", "limit": 3},
                ),
            ]
            if include_camera_tools:
                calls += [
                    ToolCall(
                        call_id="c5",
                        tool_name="device__query_channel",
                        arguments={"device_id": device_id},
                    ),
                    ToolCall(
                        call_id="c6",
                        tool_name="device__query_stream",
                        arguments={"device_id": device_id, "stream_kind": "main"},
                    ),
                    ToolCall(
                        call_id="c7",
                        tool_name="platform__query_pull_status",
                        arguments={"device_id": device_id},
                    ),
                ]
            return LLMResponse(tool_calls=calls, finish_reason=FinishReason.TOOL_CALLS)

        return LLMResponse(
            final_conclusion=ConclusionDraft(
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                summary="摄像头黑屏需要结合设备状态、通道、码流与平台拉流事实定位根因",
                root_cause=None,
                confidence="probable",
                cited_evidence_ids=[],
                next_steps=[
                    "按候选根因对应的排查顺序逐项验证",
                    "确认设备侧与平台侧事实是否一致",
                    "无法定位时补充现场信息后重新运行诊断",
                ],
            ),
            finish_reason=FinishReason.STOP,
        )

    return responder


def build_camera_black_screen_responder(include_camera_tools: bool = False) -> Responder:
    """构造摄像头黑屏闭环脚本。

    默认只使用 Phase 0 四个工具（兼容 Phase 0 demo 的旧样例数据）；
    `include_camera_tools=True` 时追加通道/码流/平台拉流三个 Phase 1 工具。
    """
    return _make_responder(include_camera_tools)


def build_registry() -> ToolRegistry:
    """注册全部 READ_ONLY 工具（Phase 0 四个 + Phase 1 三个）。"""
    registry = ToolRegistry()
    registry.register(DeviceStatusTool())
    registry.register(DeviceChannelTool())
    registry.register(DeviceStreamTool())
    registry.register(PlatformPullStatusTool())
    registry.register(DeviceAlarmEventsTool())
    registry.register(DeviceConfigSnapshotTool())
    registry.register(KnowledgeSearchTool())
    return registry


@dataclass
class Container:
    """一次装配结果。"""

    service: SecurityDiagnosisApplicationService
    repository: InMemoryDiagnosisRepository
    runner: ToolLoopRunner
    registry: ToolRegistry
    gateway: StaticDeviceGateway
    llm: FakeLLM
    citation_policy: CitationPolicy

    @property
    def external_model_called(self) -> bool:
        return self.llm.external_model_called


def build_container(
    device_data_path: str | Path | None = None,
    *,
    budget: ToolLoopBudget | None = None,
    tool_allowlist: list[str] | None = None,
    include_camera_tools: bool = False,
) -> Container:
    """装配一个完整的运行环境。

    默认（Phase 0）：旧样例数据 + 四个 Phase 0 工具，保证 Phase 0 demo 不受影响。
    `include_camera_tools=True`：追加通道/码流/平台拉流三个 Phase 1 只读工具。
    """
    data_path = Path(device_data_path) if device_data_path else DEFAULT_DEVICE_DATA_PATH
    gateway = StaticDeviceGateway(data_path)
    registry = build_registry()
    llm = FakeLLM(responder=build_camera_black_screen_responder(include_camera_tools))
    default_tools = PHASE1_TOOLS if include_camera_tools else PHASE0_TOOLS
    default_budget = (
        ToolLoopBudget(max_rounds=3, max_tool_calls=8)
        if include_camera_tools
        else ToolLoopBudget(max_rounds=3, max_tool_calls=5)
    )
    runner = ToolLoopRunner(llm, registry, budget or default_budget)
    repository = InMemoryDiagnosisRepository()
    citation_policy = CitationPolicy()
    service = SecurityDiagnosisApplicationService(
        repository=repository,
        runner=runner,
        registry=registry,
        gateway=gateway,
        citation_policy=citation_policy,
        tool_allowlist=tool_allowlist if tool_allowlist is not None else list(default_tools),
    )
    return Container(
        service=service,
        repository=repository,
        runner=runner,
        registry=registry,
        gateway=gateway,
        llm=llm,
        citation_policy=citation_policy,
    )


def build_service(
    device_data_path: str | Path | None = None,
) -> SecurityDiagnosisApplicationService:
    """只拿应用服务的便捷入口（Phase 0 默认配置）。"""
    return build_container(device_data_path).service


def build_phase1_container(
    device_data_path: str | Path | None = None,
) -> Container:
    """装配 Phase 1 摄像头黑屏评测环境。"""
    return build_container(
        device_data_path or CAMERA_CASES_DATA_PATH,
        include_camera_tools=True,
        tool_allowlist=list(PHASE1_TOOLS),
    )


__all__ = [
    "CAMERA_CASES_DATA_PATH",
    "Container",
    "DEFAULT_DEVICE_DATA_PATH",
    "PHASE0_TOOLS",
    "PHASE1_TOOLS",
    "build_camera_black_screen_responder",
    "build_container",
    "build_phase1_container",
    "build_registry",
    "build_service",
]
