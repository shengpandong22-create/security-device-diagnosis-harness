"""LLMClient Port。

只描述"模型输入输出"的契约，不绑定任何模型供应商，也不出现任何 API Key。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.conclusion import ConclusionConfidence
from security_diagnosis_harness.domain.enums import SecurityFaultType


class ChatRole(StrEnum):
    """对话角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(BaseModel):
    """一条对话消息。"""

    model_config = ConfigDict(extra="forbid")

    role: ChatRole
    content: str
    name: str | None = None


class ToolCall(BaseModel):
    """模型请求的一次工具调用。"""

    model_config = ConfigDict(extra="forbid")

    call_id: str = "call"
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ConclusionDraft(BaseModel):
    """模型给出的候选结论草稿。

    注意：这里只能用 `possible` / `probable`，`ConclusionConfidence` 中不存在
    `confirmed`，模型无法把自己标记为 confirmed。
    """

    model_config = ConfigDict(extra="forbid")

    fault_type: SecurityFaultType
    summary: str = Field(min_length=1)
    root_cause: str | None = None
    confidence: ConclusionConfidence = ConclusionConfidence.POSSIBLE
    cited_evidence_ids: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class FinishReason(StrEnum):
    """一次模型响应的结束原因。"""

    TOOL_CALLS = "tool_calls"
    STOP = "stop"
    ERROR = "error"


class LLMRequest(BaseModel):
    """发给模型的一次请求。"""

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """模型的一次响应：要么请求工具，要么给出候选结论。"""

    model_config = ConfigDict(extra="forbid")

    tool_calls: list[ToolCall] = Field(default_factory=list)
    final_conclusion: ConclusionDraft | None = None
    finish_reason: FinishReason = FinishReason.STOP
    error: str | None = None


@runtime_checkable
class LLMClient(Protocol):
    """模型客户端契约。FakeLLM 与未来的 OpenAI-compatible LLM 都实现它。"""

    def complete(self, request: LLMRequest) -> LLMResponse:
        """执行一次模型调用。"""
        ...
