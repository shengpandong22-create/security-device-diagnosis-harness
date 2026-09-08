"""FakeLLM 验收。"""

from __future__ import annotations

import socket

import pytest

from security_diagnosis_harness.adapters.llm.fake import FakeLLM
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.ports.llm import (
    ChatMessage,
    ChatRole,
    ConclusionDraft,
    FinishReason,
    LLMRequest,
    LLMResponse,
    ToolCall,
)


def _request() -> LLMRequest:
    return LLMRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="3 号楼摄像头黑屏")],
        available_tools=["device__query_status"],
    )


def test_fake_llm_returns_preset_tool_calls():
    response = LLMResponse(
        tool_calls=[
            ToolCall(
                call_id="c1",
                tool_name="device__query_status",
                arguments={"device_id": "camera-3f-001"},
            )
        ],
        finish_reason=FinishReason.TOOL_CALLS,
    )
    llm = FakeLLM([response])

    result = llm.complete(_request())

    assert result.finish_reason is FinishReason.TOOL_CALLS
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "device__query_status"
    assert result.final_conclusion is None


def test_fake_llm_returns_preset_final_conclusion():
    conclusion = ConclusionDraft(
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
        summary="主码流发布失败导致黑屏",
        confidence="probable",
    )
    llm = FakeLLM([LLMResponse(final_conclusion=conclusion, finish_reason=FinishReason.STOP)])

    result = llm.complete(_request())

    assert result.final_conclusion is not None
    assert result.final_conclusion.summary == "主码流发布失败导致黑屏"
    assert result.tool_calls == []


def test_fake_llm_records_requests_in_order():
    llm = FakeLLM(
        [
            LLMResponse(finish_reason=FinishReason.TOOL_CALLS),
            LLMResponse(finish_reason=FinishReason.STOP),
        ]
    )

    llm.complete(_request())
    second = _request()
    llm.complete(second)

    assert llm.call_count == 2
    assert llm.requests[-1] is second


def test_fake_llm_returns_controlled_error_when_responses_exhausted():
    llm = FakeLLM([])

    result = llm.complete(_request())

    assert result.finish_reason is FinishReason.ERROR
    assert result.error


def test_fake_llm_does_not_access_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("单元测试不允许访问网络")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    llm = FakeLLM([LLMResponse(finish_reason=FinishReason.STOP)])

    assert llm.complete(_request()).finish_reason is FinishReason.STOP


def test_fake_llm_rejects_confirmed_confidence():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ConclusionDraft(
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            summary="模型不能把自己标记为 confirmed",
            confidence="confirmed",
        )
