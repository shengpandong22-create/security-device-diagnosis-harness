"""最小 ToolLoopRunner 验收。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from security_diagnosis_harness.adapters.llm.fake import FakeLLM
from security_diagnosis_harness.agent.runner import ToolLoopBudget, ToolLoopRunner
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceType
from security_diagnosis_harness.ports.llm import (
    ConclusionDraft,
    FinishReason,
    LLMResponse,
    ToolCall,
)
from security_diagnosis_harness.tools.contracts import ToolExecutionContext, ToolExecutionResult
from security_diagnosis_harness.tools.registry import ToolRegistry

from ..conftest import DEVICE_ID, make_case, make_tool_context


class RecordingRegistry(ToolRegistry):
    """记录所有工具调用的 Registry，用于验证调用没有绕过 Registry。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        self.calls.append((tool_name, dict(arguments)))
        return super().execute(tool_name, arguments, context)


def _tool_call_response() -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCall(
                call_id="c1",
                tool_name="device__query_status",
                arguments={"device_id": DEVICE_ID},
            )
        ],
        finish_reason=FinishReason.TOOL_CALLS,
    )


def _final_response() -> LLMResponse:
    return LLMResponse(
        final_conclusion=ConclusionDraft(
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            summary="摄像头黑屏更可能由码流发布失败或编码器异常导致",
            root_cause="STREAM_PUBLISH_FAILED",
            confidence="probable",
            cited_evidence_ids=[],
            next_steps=["检查编码器", "确认平台拉流"],
        ),
        finish_reason=FinishReason.STOP,
    )


@pytest.fixture
def context(static_gateway):
    return make_tool_context("diag_phase0a", gateway=static_gateway)


def test_runner_calls_tool_then_returns_final_conclusion(tool_registry, context):
    llm = FakeLLM([_tool_call_response(), _final_response()])
    runner = ToolLoopRunner(llm, tool_registry)

    result = runner.run(make_case(), context)

    assert result.ok is True
    assert result.error is None
    assert result.rounds == 2
    assert result.tool_calls == 1
    assert result.final_conclusion is not None
    assert result.final_conclusion.root_cause == "STREAM_PUBLISH_FAILED"
    assert llm.call_count == 2


def test_runner_collects_evidence_drafts(tool_registry, context):
    llm = FakeLLM(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        call_id="c1",
                        tool_name="device__query_status",
                        arguments={"device_id": DEVICE_ID},
                    ),
                    ToolCall(
                        call_id="c2",
                        tool_name="device__search_alarm_events",
                        arguments={"device_id": DEVICE_ID, "keyword": "stream"},
                    ),
                ],
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            _final_response(),
        ]
    )
    runner = ToolLoopRunner(llm, tool_registry)

    result = runner.run(make_case(), context)

    assert result.ok is True
    assert [draft.evidence_type for draft in result.evidence_drafts] == [
        EvidenceType.DEVICE_STATUS,
        EvidenceType.DEVICE_ALARM,
    ]


def test_tool_calls_go_through_registry(static_gateway, context):
    registry = RecordingRegistry()
    from security_diagnosis_harness.tools.device_status import DeviceStatusTool

    registry.register(DeviceStatusTool())
    llm = FakeLLM([_tool_call_response(), _final_response()])
    runner = ToolLoopRunner(llm, registry)

    runner.run(make_case(), context)

    assert registry.calls == [("device__query_status", {"device_id": DEVICE_ID})]


def test_tool_outside_allowlist_is_rejected_by_registry(tool_registry, context):
    llm = FakeLLM([_tool_call_response(), _final_response()])
    runner = ToolLoopRunner(llm, tool_registry)

    result = runner.run(
        make_case(),
        context,
        tool_allowlist=["device__search_alarm_events"],
    )

    assert len(result.tool_results) == 1
    rejected = result.tool_results[0]
    assert rejected.ok is False
    assert "白名单" in (rejected.error or "")
    assert rejected.evidence_drafts == []
    # 白名单外的工具没有产生任何证据，模型仍拿到失败观察后继续。
    assert result.evidence_drafts == []
    assert result.final_conclusion is not None


def test_exceeding_max_tool_calls_returns_controlled_failure(tool_registry, context):
    llm = FakeLLM([_tool_call_response(), _tool_call_response(), _final_response()])
    runner = ToolLoopRunner(llm, tool_registry, ToolLoopBudget(max_rounds=3, max_tool_calls=1))

    result = runner.run(make_case(), context)

    assert result.ok is False
    assert "max_tool_calls" in (result.error or "")
    assert result.tool_calls == 1


def test_exceeding_max_rounds_returns_controlled_failure(tool_registry, context):
    llm = FakeLLM([_tool_call_response() for _ in range(5)])
    runner = ToolLoopRunner(llm, tool_registry, ToolLoopBudget(max_rounds=2, max_tool_calls=10))

    result = runner.run(make_case(), context)

    assert result.ok is False
    assert "max_rounds" in (result.error or "")
    assert result.rounds == 2


def test_model_error_returns_controlled_failure(tool_registry, context):
    llm = FakeLLM([])
    runner = ToolLoopRunner(llm, tool_registry)

    result = runner.run(make_case(), context)

    assert result.ok is False
    assert result.error


def test_empty_model_response_returns_controlled_failure(tool_registry, context):
    llm = FakeLLM([LLMResponse(finish_reason=FinishReason.STOP)])
    runner = ToolLoopRunner(llm, tool_registry)

    result = runner.run(make_case(), context)

    assert result.ok is False
    assert "既没有工具调用也没有最终结论" in (result.error or "")


def test_context_diagnosis_mismatch_returns_controlled_failure(tool_registry, static_gateway):
    llm = FakeLLM([_final_response()])
    runner = ToolLoopRunner(llm, tool_registry)
    wrong_context = make_tool_context("diag_other", gateway=static_gateway)

    result = runner.run(make_case(), wrong_context)

    assert result.ok is False
    assert "不一致" in (result.error or "")


def test_runner_does_not_mutate_case(tool_registry, context):
    case = make_case()
    before_status = case.status
    before_updated_at = case.updated_at
    llm = FakeLLM([_tool_call_response(), _final_response()])
    runner = ToolLoopRunner(llm, tool_registry)

    runner.run(case, context)

    assert case.status is before_status
    assert case.status is SecurityDiagnosisStatus.CREATED
    assert case.evidence == []
    assert case.conclusion is None
    assert case.reviews == []
    assert case.updated_at == before_updated_at
