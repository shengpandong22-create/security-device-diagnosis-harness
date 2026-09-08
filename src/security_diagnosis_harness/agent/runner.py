"""最小受控 Tool Loop Runner。

Runner 只负责"让模型在预算内通过 Registry 调工具并产出候选结论"，
它不修改 `SecurityDiagnosisCase` 的状态，也不把 EvidenceDraft 落成
`DiagnosisEvidence`（这是应用服务的职责）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.ports.llm import (
    ChatMessage,
    ChatRole,
    ConclusionDraft,
    FinishReason,
    LLMClient,
    LLMRequest,
)
from security_diagnosis_harness.tools.contracts import (
    ToolEvidenceDraft,
    ToolExecutionContext,
    ToolExecutionResult,
)
from security_diagnosis_harness.tools.registry import ToolRegistry

SYSTEM_PROMPT = (
    "你是安防设备运维诊断助手。"
    "只能基于只读工具返回的设备事实推理，不得编造证据，"
    "不得把结论标记为 confirmed，最终结论必须由人工确认。"
)


@dataclass(frozen=True)
class ToolLoopBudget:
    """Runner 的最小预算。"""

    max_rounds: int = 3
    max_tool_calls: int = 5


class ToolLoopResult(BaseModel):
    """一次 Runner 运行的结果。"""

    model_config = ConfigDict(extra="forbid")

    diagnosis_id: str
    ok: bool = True
    rounds: int = 0
    tool_calls: int = 0
    messages: list[ChatMessage] = Field(default_factory=list)
    tool_results: list[ToolExecutionResult] = Field(default_factory=list)
    evidence_drafts: list[ToolEvidenceDraft] = Field(default_factory=list)
    final_conclusion: ConclusionDraft | None = None
    error: str | None = None


class ToolLoopRunner:
    """受控 Agent Loop：LLM 决策 + 确定性闸门 + 工具结果回传。"""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        budget: ToolLoopBudget | None = None,
    ):
        self._llm = llm
        self._registry = registry
        self._budget = budget or ToolLoopBudget()

    @property
    def budget(self) -> ToolLoopBudget:
        return self._budget

    def run(
        self,
        case: SecurityDiagnosisCase,
        context: ToolExecutionContext,
        tool_allowlist: Sequence[str] | None = None,
    ) -> ToolLoopResult:
        """执行一次受控循环。

        Args:
            case: 诊断用例，Runner 只读它，不修改状态。
            context: 工具执行上下文，其 `diagnosis_id` 必须与 case 一致。
            tool_allowlist: 允许调用的工具名；None 表示允许全部已注册工具。
        """
        if context.diagnosis_id != case.diagnosis_id:
            return self._failure(
                case.diagnosis_id,
                f"执行上下文 diagnosis_id={context.diagnosis_id} 与诊断 {case.diagnosis_id} 不一致",
            )

        allowed = self._resolve_allowlist(tool_allowlist)
        messages = [
            ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM_PROMPT),
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    f"故障类型: {case.fault_type.value}\n"
                    f"设备: {case.device_id}\n"
                    f"现象: {case.description or '未提供'}"
                ),
            ),
        ]

        tool_results: list[ToolExecutionResult] = []
        evidence_drafts: list[ToolEvidenceDraft] = []
        tool_calls_used = 0

        for round_index in range(1, self._budget.max_rounds + 1):
            response = self._llm.complete(
                LLMRequest(
                    messages=list(messages),
                    available_tools=allowed,
                    metadata={
                        "diagnosis_id": case.diagnosis_id,
                        "device_id": case.device_id,
                        "round": round_index,
                        "fault_type": case.fault_type.value,
                    },
                )
            )
            if response.error or response.finish_reason is FinishReason.ERROR:
                return self._failure(
                    case.diagnosis_id,
                    response.error or "模型返回错误",
                    rounds=round_index,
                    tool_calls=tool_calls_used,
                    messages=messages,
                    tool_results=tool_results,
                    evidence_drafts=evidence_drafts,
                )

            if response.tool_calls:
                for call in response.tool_calls:
                    if tool_calls_used >= self._budget.max_tool_calls:
                        return self._failure(
                            case.diagnosis_id,
                            f"超出工具调用预算 max_tool_calls={self._budget.max_tool_calls}",
                            rounds=round_index,
                            tool_calls=tool_calls_used,
                            messages=messages,
                            tool_results=tool_results,
                            evidence_drafts=evidence_drafts,
                        )
                    tool_calls_used += 1

                    result = self._invoke(call.tool_name, call.arguments, allowed, context)
                    tool_results.append(result)
                    evidence_drafts.extend(result.evidence_drafts)
                    messages.append(
                        ChatMessage(
                            role=ChatRole.TOOL,
                            name=call.tool_name,
                            content=result.observation or (result.error or "工具无输出"),
                        )
                    )
                continue

            if response.final_conclusion is not None:
                return ToolLoopResult(
                    diagnosis_id=case.diagnosis_id,
                    ok=True,
                    rounds=round_index,
                    tool_calls=tool_calls_used,
                    messages=messages,
                    tool_results=tool_results,
                    evidence_drafts=evidence_drafts,
                    final_conclusion=response.final_conclusion,
                )

            return self._failure(
                case.diagnosis_id,
                "模型响应既没有工具调用也没有最终结论",
                rounds=round_index,
                tool_calls=tool_calls_used,
                messages=messages,
                tool_results=tool_results,
                evidence_drafts=evidence_drafts,
            )

        return self._failure(
            case.diagnosis_id,
            f"超出轮次预算 max_rounds={self._budget.max_rounds}",
            rounds=self._budget.max_rounds,
            tool_calls=tool_calls_used,
            messages=messages,
            tool_results=tool_results,
            evidence_drafts=evidence_drafts,
        )

    def _invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        allowed: list[str],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """所有工具调用都必须经过 Registry。"""
        if tool_name not in allowed:
            return ToolExecutionResult(
                tool_name=tool_name,
                ok=False,
                error=f"工具 {tool_name} 不在本次运行的白名单内",
            )
        return self._registry.execute(tool_name, arguments, context)

    def _resolve_allowlist(self, tool_allowlist: Sequence[str] | None) -> list[str]:
        if tool_allowlist is None:
            return self._registry.names()
        allowed = [name for name in tool_allowlist if self._registry.has(name)]
        return allowed

    @staticmethod
    def _failure(
        diagnosis_id: str,
        error: str,
        *,
        rounds: int = 0,
        tool_calls: int = 0,
        messages: list[ChatMessage] | None = None,
        tool_results: list[ToolExecutionResult] | None = None,
        evidence_drafts: list[ToolEvidenceDraft] | None = None,
    ) -> ToolLoopResult:
        return ToolLoopResult(
            diagnosis_id=diagnosis_id,
            ok=False,
            rounds=rounds,
            tool_calls=tool_calls,
            messages=messages or [],
            tool_results=tool_results or [],
            evidence_drafts=evidence_drafts or [],
            error=error,
        )
