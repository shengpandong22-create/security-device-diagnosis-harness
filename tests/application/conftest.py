"""应用层测试夹具。"""

from __future__ import annotations

import pytest

from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.adapters.llm.fake import FakeLLM
from security_diagnosis_harness.agent.runner import ToolLoopBudget, ToolLoopRunner
from security_diagnosis_harness.application.diagnoses import SecurityDiagnosisApplicationService
from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository
from security_diagnosis_harness.bootstrap.container import (
    build_camera_black_screen_responder,
    build_registry,
)
from security_diagnosis_harness.domain.citation_policy import CitationPolicy
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.review import HumanReviewAction
from security_diagnosis_harness.ports.llm import (
    ConclusionDraft,
    FinishReason,
    LLMResponse,
    ToolCall,
)

from ..conftest import DEVICE_DATASET, DEVICE_ID, make_tool_context, write_device_dataset

DEMO_DEVICE_ID = DEVICE_ID


def black_screen_llm() -> FakeLLM:
    """能跑通摄像头黑屏闭环的脚本式 FakeLLM。"""
    return FakeLLM(responder=build_camera_black_screen_responder())


def failing_llm() -> FakeLLM:
    """每次都失败的 FakeLLM（无预设响应）。"""
    return FakeLLM()


def no_conclusion_llm() -> FakeLLM:
    """只调工具、不给结论的 FakeLLM。"""
    return FakeLLM([LLMResponse(finish_reason=FinishReason.TOOL_CALLS)])


def no_evidence_llm() -> FakeLLM:
    """直接给结论但不调工具，因此不会有任何 Evidence。"""
    return FakeLLM(
        [
            LLMResponse(
                final_conclusion=ConclusionDraft(
                    fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                    summary="没有证据支撑的猜测",
                    confidence="probable",
                    cited_evidence_ids=[],
                ),
                finish_reason=FinishReason.STOP,
            )
        ]
    )


def knowledge_only_llm() -> FakeLLM:
    """只查知识库，因此只有 knowledge_sop Evidence。"""
    return FakeLLM(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        call_id="c1",
                        tool_name="knowledge__search",
                        arguments={"query": "摄像头黑屏"},
                    )
                ],
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                final_conclusion=ConclusionDraft(
                    fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
                    summary="依据 SOP 的初步判断",
                    confidence="probable",
                    cited_evidence_ids=[],
                ),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )


def build_service(
    llm: FakeLLM,
    gateway: StaticDeviceGateway,
    *,
    budget: ToolLoopBudget | None = None,
) -> tuple[SecurityDiagnosisApplicationService, InMemoryDiagnosisRepository]:
    """构造一个应用服务及其仓储。"""
    repository = InMemoryDiagnosisRepository()
    registry = build_registry()
    runner = ToolLoopRunner(llm, registry, budget or ToolLoopBudget(max_rounds=3, max_tool_calls=5))
    service = SecurityDiagnosisApplicationService(
        repository=repository,
        runner=runner,
        registry=registry,
        gateway=gateway,
        citation_policy=CitationPolicy(),
    )
    return service, repository


@pytest.fixture
def app_service(static_gateway) -> SecurityDiagnosisApplicationService:
    service, _ = build_service(black_screen_llm(), static_gateway)
    return service


@pytest.fixture
def demo_service() -> SecurityDiagnosisApplicationService:
    """使用仓库样例数据的应用服务。"""
    from security_diagnosis_harness.bootstrap.container import DEFAULT_DEVICE_DATA_PATH

    gateway = StaticDeviceGateway(DEFAULT_DEVICE_DATA_PATH)
    service, _ = build_service(black_screen_llm(), gateway)
    return service


@pytest.fixture
def sample_gateway(tmp_path) -> StaticDeviceGateway:
    path = write_device_dataset(tmp_path / "devices.json")
    return StaticDeviceGateway(path)


def create_black_screen_case(service: SecurityDiagnosisApplicationService) -> str:
    case = service.create_diagnosis(
        device_id=DEMO_DEVICE_ID,
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        reporter="ops-zhang",
        description="3 号楼大厅摄像头预览黑屏",
    )
    return case.diagnosis_id


def confirm(service: SecurityDiagnosisApplicationService, diagnosis_id: str, **kwargs):
    return service.review_diagnosis(
        diagnosis_id=diagnosis_id,
        action=HumanReviewAction.CONFIRM,
        reviewer=kwargs.get("reviewer", "ops-li"),
        comment=kwargs.get("comment", "现场核实，同意结论"),
    )


__all__ = [
    "DEVICE_DATASET",
    "DEMO_DEVICE_ID",
    "build_service",
    "confirm",
    "create_black_screen_case",
    "make_tool_context",
]
