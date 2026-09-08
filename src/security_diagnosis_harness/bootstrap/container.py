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
from security_diagnosis_harness.tools.device_config import DeviceConfigSnapshotTool
from security_diagnosis_harness.tools.device_status import DeviceStatusTool
from security_diagnosis_harness.tools.knowledge_search import KnowledgeSearchTool
from security_diagnosis_harness.tools.registry import ToolRegistry

DEFAULT_DEVICE_DATA_PATH = (
    Path(__file__).resolve().parents[3] / "samples" / "devices" / "static_devices.sample.json"
)

PHASE0_TOOLS: tuple[str, ...] = (
    "device__query_status",
    "device__search_alarm_events",
    "device__read_config_snapshot",
    "knowledge__search",
)


def build_camera_black_screen_responder() -> Responder:
    """构造摄像头黑屏闭环脚本。

    第一轮请求四个只读工具；拿到工具结果后给出 probable 候选结论。
    结论的 `cited_evidence_ids` 故意留空，由应用服务按刚刚落地的
    Evidence 做最小引用修正，从而验证 CitationPolicy 不被绕过。
    """

    def responder(request: LLMRequest) -> LLMResponse:
        device_id = str(request.metadata.get("device_id", ""))
        has_tool_results = any(message.role is ChatRole.TOOL for message in request.messages)

        if not has_tool_results:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        call_id="c1",
                        tool_name="device__query_status",
                        arguments={"device_id": device_id},
                    ),
                    ToolCall(
                        call_id="c2",
                        tool_name="device__search_alarm_events",
                        arguments={"device_id": device_id, "keyword": "stream", "limit": 5},
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
                ],
                finish_reason=FinishReason.TOOL_CALLS,
            )

        return LLMResponse(
            final_conclusion=ConclusionDraft(
                fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                summary="摄像头黑屏更可能由码流发布失败或编码器异常导致",
                root_cause="STREAM_PUBLISH_FAILED",
                confidence="probable",
                cited_evidence_ids=[],
                next_steps=[
                    "检查编码器状态并确认是否超时",
                    "降低码率或分辨率后重试取流",
                    "确认平台侧拉流状态与录像计划",
                ],
            ),
            finish_reason=FinishReason.STOP,
        )

    return responder


def build_registry() -> ToolRegistry:
    """注册 Phase 0 四个 READ_ONLY 工具。"""
    registry = ToolRegistry()
    registry.register(DeviceStatusTool())
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
) -> Container:
    """装配一个完整的 Phase 0C 运行环境。"""
    data_path = Path(device_data_path) if device_data_path else DEFAULT_DEVICE_DATA_PATH
    gateway = StaticDeviceGateway(data_path)
    registry = build_registry()
    llm = FakeLLM(responder=build_camera_black_screen_responder())
    runner = ToolLoopRunner(llm, registry, budget or ToolLoopBudget(max_rounds=3, max_tool_calls=5))
    repository = InMemoryDiagnosisRepository()
    citation_policy = CitationPolicy()
    service = SecurityDiagnosisApplicationService(
        repository=repository,
        runner=runner,
        registry=registry,
        gateway=gateway,
        citation_policy=citation_policy,
        tool_allowlist=tool_allowlist if tool_allowlist is not None else list(PHASE0_TOOLS),
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
    """只拿应用服务的便捷入口。"""
    return build_container(device_data_path).service


__all__ = [
    "Container",
    "DEFAULT_DEVICE_DATA_PATH",
    "PHASE0_TOOLS",
    "build_camera_black_screen_responder",
    "build_container",
    "build_registry",
    "build_service",
]
