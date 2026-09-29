"""最小受控 Tool Loop Runner。

Runner 只负责"让模型在预算内通过 Registry 调工具并产出候选结论"，
它不修改 `SecurityDiagnosisCase` 的状态，也不把 EvidenceDraft 落成
`DiagnosisEvidence`（这是应用服务的职责）。

超时语义（Phase 12 修正，收窄且可证明）：

Python **无法抢占式终止**正在运行的线程，因此本模块**不声称**"超时后任意代码都
会停止"。实际保证分两层：

1. **准入闸门（严格模式，默认）**：`run()` 在调用任何模型或工具之前，先检查每个
   参与者是否显式声明 ``cancellation_aware = True``（见
   :func:`supports_cooperative_cancellation`）。未声明的实现——也就是"不可控后台
   工作"——**根本不会被调用**，因此不可能在 Runner 返回后产生副作用。
2. **协作取消 + 有限等待**：已声明的参与者通过 :func:`current_cancellation`
   观察取消令牌。越过 deadline 后 Runner 置位令牌并**有限**等待其退出
   （:data:`_CANCELLATION_GRACE_SECONDS`，不是无界 join）；迟到结果一律丢弃。

残余风险被显式上报而不是静默接受：如果已声明的参与者违反契约、在宽限期内仍未
退出，结果会带上 ``cancellation_confirmed=False``，调用方必须把该次运行视为不可信。
未声明取消能力的参与者可以在非严格模式下以
``require_cancellation_aware=False`` 显式放行，仅用于评测与实验。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Thread
from time import monotonic
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.ports.cancellation import (
    CANCELLATION_AWARE_ATTRIBUTE,
    OperationCancelledError,
    RunCancellation,
    current_cancellation,
    raise_if_cancelled,
    reset_current_cancellation,
    set_current_cancellation,
    supports_cooperative_cancellation,
)
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
from security_diagnosis_harness.tools.registry import ToolNotFoundError, ToolRegistry

__all__ = [
    "CANCELLATION_AWARE_ATTRIBUTE",
    "OperationCancelledError",
    "RunCancellation",
    "SYSTEM_PROMPT",
    "TOOL_LOOP_WORKER_NAME",
    "ToolLoopBudget",
    "ToolLoopResult",
    "ToolLoopRunner",
    "current_cancellation",
    "raise_if_cancelled",
    "reset_current_cancellation",
    "set_current_cancellation",
    "supports_cooperative_cancellation",
]

SYSTEM_PROMPT = (
    "你是安防设备运维诊断助手。"
    "只能基于只读工具返回的设备事实推理，不得编造证据，"
    "不得把结论标记为 confirmed，最终结论必须由人工确认。"
)

_T = TypeVar("_T")

#: daemon worker 线程名的稳定常量：测试用它验证"超时返回后没有遗留 worker"。
TOOL_LOOP_WORKER_NAME = "tool-loop-budget-worker"

#: 置位取消令牌后，等待底层操作协作退出的**有限**宽限时间。
#: 这是有界等待，不是无界 join；仍未退出说明操作不遵守取消契约。
_CANCELLATION_GRACE_SECONDS = 0.05


class _RunnerDeadlineExceeded(TimeoutError):
    """越过 deadline 的内部信号。

    ``cancellation_confirmed`` 为 ``False`` 表示：取消令牌已置位，但底层的已声明
    参与者没有在有限宽限期内退出，因此**无法确认**它之后不会再产生副作用。调用方
    必须把这种运行视为不可信，而不是当成一次干净的普通超时。
    """

    def __init__(self, *, cancellation_confirmed: bool = True) -> None:
        self.cancellation_confirmed = cancellation_confirmed
        super().__init__("运行超出 deadline")


@dataclass(frozen=True)
class ToolLoopBudget:
    """Runner 的最小预算。"""

    max_rounds: int = 3
    max_tool_calls: int = 5
    timeout_seconds: float = 30.0
    max_model_response_chars: int = 64_000
    max_observation_chars: int = 16_000
    max_context_chars: int = 128_000

    def __post_init__(self) -> None:
        if (
            self.max_rounds < 1
            or self.max_tool_calls < 0
            or self.timeout_seconds <= 0
            or self.max_model_response_chars < 1
            or self.max_observation_chars < 1
            or self.max_context_chars < 1
        ):
            raise ValueError("ToolLoopBudget 必须使用正轮次、非负工具次数和正超时")


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
    #: 是否已确认"底层操作已停止"。
    #:
    #: - ``True``：正常返回 / 正常失败 / 超时后已确认取消（含未声明的参与者被拒绝
    #:   执行的情形，因为它从未开始运行）；
    #: - ``False``：已声明的参与者违反契约、在宽限期内未退出，或本次运行因参与者
    #:   未声明取消能力而被拒绝执行。调用方必须把该次运行视为不可信，不得把它的
    #:   结果当作"干净的超时"。
    cancellation_confirmed: bool = True


class ToolLoopRunner:
    """受控 Agent Loop：LLM 决策 + 确定性闸门 + 工具结果回传。

    Args:
        llm: 模型客户端；严格模式下必须显式声明 ``cancellation_aware = True``。
        registry: 工具注册表。
        budget: 运行预算。
        require_cancellation_aware: 严格模式开关（默认 ``True``）。为 ``True`` 时，
            Runner 在调用之前先拒绝所有未声明协作取消契约的模型 / 工具，从而保证
            "Runner 返回后不会再出现迟到的副作用"。显式设为 ``False`` 只应用于评测
            与实验场景，此时无法保证未声明参与者的迟到行为。
    """

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        budget: ToolLoopBudget | None = None,
        *,
        require_cancellation_aware: bool = True,
    ):
        self._llm = llm
        self._registry = registry
        self._budget = budget or ToolLoopBudget()
        self._require_cancellation_aware = require_cancellation_aware

    @property
    def budget(self) -> ToolLoopBudget:
        return self._budget

    @property
    def require_cancellation_aware(self) -> bool:
        return self._require_cancellation_aware

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
        if self._require_cancellation_aware:
            unsupported = self._unsupported_participants(allowed)
            if unsupported:
                # 拒绝执行，而不是"启动后再放弃等待"：未声明的参与者从未运行，
                # 因此不存在它在 Runner 返回后继续产生副作用的可能。
                return self._failure(
                    case.diagnosis_id,
                    "运行参与者未声明支持协作取消，已拒绝执行: "
                    + ", ".join(unsupported),
                    cancellation_confirmed=False,
                )
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
        deadline = monotonic() + self._budget.timeout_seconds
        # 运行级协作取消令牌：超时后置位，底层模型 / 工具 / Adapter 据此停止。
        cancellation = RunCancellation()

        for round_index in range(1, self._budget.max_rounds + 1):
            if self._message_chars(messages) > self._budget.max_context_chars:
                return self._failure(
                    case.diagnosis_id,
                    f"超出上下文预算 max_context_chars={self._budget.max_context_chars}",
                    rounds=round_index,
                    tool_calls=tool_calls_used,
                    messages=messages,
                    tool_results=tool_results,
                    evidence_drafts=evidence_drafts,
                )
            request = LLMRequest(
                messages=list(messages),
                available_tools=allowed,
                metadata={
                    "diagnosis_id": case.diagnosis_id,
                    "device_id": case.device_id,
                    "round": round_index,
                    "fault_type": case.fault_type.value,
                    "deadline_monotonic": deadline,
                },
            )
            try:
                response = self._within_deadline(
                    lambda request=request: self._llm.complete(request),
                    deadline,
                    cancellation,
                )
            except _RunnerDeadlineExceeded as exc:
                return self._deadline_failure(
                    case.diagnosis_id,
                    cancellation_confirmed=exc.cancellation_confirmed,
                    rounds=round_index,
                    tool_calls=tool_calls_used,
                    messages=messages,
                    tool_results=tool_results,
                    evidence_drafts=evidence_drafts,
                )
            except Exception:
                return self._failure(
                    case.diagnosis_id,
                    "模型调用失败",
                    rounds=round_index,
                    tool_calls=tool_calls_used,
                    messages=messages,
                    tool_results=tool_results,
                    evidence_drafts=evidence_drafts,
                )
            if len(response.model_dump_json()) > self._budget.max_model_response_chars:
                return self._failure(
                    case.diagnosis_id,
                    "模型响应超过大小预算 "
                    f"max_model_response_chars={self._budget.max_model_response_chars}",
                    rounds=round_index,
                    tool_calls=tool_calls_used,
                    messages=messages,
                    tool_results=tool_results,
                    evidence_drafts=evidence_drafts,
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

                    try:
                        result = self._within_deadline(
                            lambda call=call: self._invoke(
                                call.tool_name,
                                call.arguments,
                                allowed,
                                context,
                            ),
                            deadline,
                            cancellation,
                        )
                    except _RunnerDeadlineExceeded as exc:
                        return self._deadline_failure(
                            case.diagnosis_id,
                            cancellation_confirmed=exc.cancellation_confirmed,
                            rounds=round_index,
                            tool_calls=tool_calls_used,
                            messages=messages,
                            tool_results=tool_results,
                            evidence_drafts=evidence_drafts,
                        )
                    except Exception:
                        return self._failure(
                            case.diagnosis_id,
                            f"工具执行失败: {call.tool_name}",
                            rounds=round_index,
                            tool_calls=tool_calls_used,
                            messages=messages,
                            tool_results=tool_results,
                            evidence_drafts=evidence_drafts,
                        )
                    if len(result.observation) > self._budget.max_observation_chars:
                        return self._failure(
                            case.diagnosis_id,
                            "工具 observation 超过大小预算 "
                            f"max_observation_chars={self._budget.max_observation_chars}",
                            rounds=round_index,
                            tool_calls=tool_calls_used,
                            messages=messages,
                            tool_results=tool_results,
                            evidence_drafts=evidence_drafts,
                        )
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

    def _unsupported_participants(self, allowed: Sequence[str]) -> list[str]:
        """列出未显式声明协作取消契约的参与者（模型客户端与本次允许的工具）。"""
        participants: list[tuple[str, object]] = [("模型客户端", self._llm)]
        for name in allowed:
            try:
                participants.append((f"工具 {name}", self._registry.get(name)))
            except ToolNotFoundError:  # pragma: no cover - allowlist 已按注册表过滤
                continue
        return [
            label
            for label, participant in participants
            if not supports_cooperative_cancellation(participant)
        ]

    def _deadline_failure(
        self,
        diagnosis_id: str,
        *,
        cancellation_confirmed: bool = True,
        **state: Any,
    ) -> ToolLoopResult:
        reason = (
            f"超出运行时间预算 timeout_seconds={self._budget.timeout_seconds}"
        )
        if not cancellation_confirmed:
            # 已声明的参与者违反契约：必须显式上报，绝不能伪装成一次干净的超时。
            reason += "；底层操作未在宽限期内确认停止，取消未确认"
        return self._failure(
            diagnosis_id,
            reason,
            cancellation_confirmed=cancellation_confirmed,
            **state,
        )

    @staticmethod
    def _message_chars(messages: Sequence[ChatMessage]) -> int:
        return sum(len(item.content) + len(item.name or "") for item in messages)

    @staticmethod
    def _within_deadline(
        operation: Callable[[], _T],
        deadline: float,
        cancellation: RunCancellation,
    ) -> _T:
        """在 daemon worker 中执行操作；超时则置位取消并**有限**等待其退出。

        语义（严格表述，不夸大）：

        - worker 在协作取消令牌下运行操作，遵守契约的实现可以通过
          :func:`current_cancellation` 观察取消；
        - 若在 deadline 内完成，本方法按既有语义返回结果或原样抛出异常；
        - 若越过 deadline，则先置位令牌再有限等待 worker 协作退出，最后抛出
          :class:`_RunnerDeadlineExceeded`；迟到结果一律丢弃。

        **本方法不能终止任意代码**：置位令牌只对遵守契约的实现有效。若 worker 在
        有限宽限期后仍然存活（实现违反契约），异常会带上
        ``cancellation_confirmed=False``，由调用方把该次运行标记为不可信。因此严格
        模式会在调用之前就拒绝未声明的实现，使这里只可能遇到"已声明却违约"的残余
        情况。

        这里的等待是有界 join（``_CANCELLATION_GRACE_SECONDS``），不是无界 join。
        """
        remaining = deadline - monotonic()
        if remaining <= 0:
            cancellation.cancel()
            raise _RunnerDeadlineExceeded
        # worker 每个生命周期只 put 一次，因此队列不会满；一次 put 也保证读取
        # 到的就是本次运行的唯一结果。
        outcome: Queue[tuple[bool, _T | BaseException]] = Queue()

        def execute() -> None:
            token = set_current_cancellation(cancellation)
            try:
                result = operation()
            except BaseException as exc:  # 原异常交由调用线程按既有语义处理。
                outcome.put((False, exc))
                return
            finally:
                reset_current_cancellation(token)
            outcome.put((True, result))

        worker = Thread(target=execute, daemon=True, name=TOOL_LOOP_WORKER_NAME)
        worker.start()
        worker.join(remaining)
        if worker.is_alive():
            # 真取消：置位协作取消令牌，让遵守契约的操作停止产生副作用，再有限等待。
            cancellation.cancel()
            worker.join(_CANCELLATION_GRACE_SECONDS)
            # 仍在运行说明实现违反契约：如实上报"取消未确认"，而不是假装已停止。
            raise _RunnerDeadlineExceeded(
                cancellation_confirmed=not worker.is_alive()
            )
        try:
            succeeded, value = outcome.get_nowait()
        except Empty:  # pragma: no cover - worker 已退出但未 put，仅防御性处理。
            raise _RunnerDeadlineExceeded from None
        if not succeeded:
            raise value
        return value

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
        cancellation_confirmed: bool = True,
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
            cancellation_confirmed=cancellation_confirmed,
        )
