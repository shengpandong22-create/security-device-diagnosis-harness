"""最小受控 Tool Loop Runner。

Runner 只负责"让模型在预算内通过 Registry 调工具并产出候选结论"，
它不修改 `SecurityDiagnosisCase` 的状态，也不把 EvidenceDraft 落成
`DiagnosisEvidence`（这是应用服务的职责）。

超时语义（Phase 11+ 修正）：Runner 不再只是"放弃等待"——超时后它会置位一次
运行级的**协作取消令牌**并等待底层操作退出。底层模型 / 工具 / Adapter 必须通过
:func:`current_cancellation` 读取该令牌，在长阻塞步骤或循环之间检查 ``cancelled``
并主动停止。Python 无法抢占式杀死线程，因此取消是**协作式契约**：Runner 已经
提供了真实的取消信号与有限等待，而不是用加大 timeout、无界 join、吞迟到结果或
``Future.cancel()`` 冒充取消。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic
from typing import Any, TypeVar

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

_T = TypeVar("_T")

#: daemon worker 线程名的稳定常量：测试用它验证"超时返回后没有遗留 worker"。
TOOL_LOOP_WORKER_NAME = "tool-loop-budget-worker"

#: 置位取消令牌后，等待底层操作协作退出的**有限**宽限时间。
#: 这是有界等待，不是无界 join；仍未退出说明操作不遵守取消契约。
_CANCELLATION_GRACE_SECONDS = 0.05


class _RunnerDeadlineExceeded(TimeoutError):
    pass


class OperationCancelledError(RuntimeError):
    """底层操作观察到协作取消后主动放弃时抛出的受控信号。

    异常文本不含任何外部内容，可安全向上传递。
    """

    def __init__(self) -> None:
        super().__init__("操作已因运行超时被协作取消")


class RunCancellation:
    """一次 Runner 运行的**协作取消令牌**。

    Runner 越过 deadline 后会立即 ``cancel()``，随后有限等待底层操作退出。底层
    模型 / 工具 / Adapter 通过 :func:`current_cancellation` 拿到令牌，在循环或长
    阻塞步骤之间检查 ``cancelled``（或调用 ``raise_if_cancelled``），从而在
    Runner 返回 timeout 后不再继续产生副作用。
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        """置位取消：幂等，可重复调用。"""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """已取消时抛出 :class:`OperationCancelledError`，供底层操作快速退出。"""
        if self._event.is_set():
            raise OperationCancelledError()


#: 当前 worker 线程可见的协作取消令牌。ContextVar 不会被新线程继承，因此
#: Runner 在 worker 内显式 set/reset，保证底层操作一定读到本次运行的令牌。
_CURRENT_CANCELLATION: ContextVar[RunCancellation | None] = ContextVar(
    "tool_loop_cancellation", default=None
)


def current_cancellation() -> RunCancellation | None:
    """返回当前线程可见的协作取消令牌；不在 Runner worker 内时为 ``None``。"""
    return _CURRENT_CANCELLATION.get()


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
            except _RunnerDeadlineExceeded:
                return self._deadline_failure(
                    case.diagnosis_id,
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
                    except _RunnerDeadlineExceeded:
                        return self._deadline_failure(
                            case.diagnosis_id,
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

    def _deadline_failure(self, diagnosis_id: str, **state: Any) -> ToolLoopResult:
        return self._failure(
            diagnosis_id,
            f"超出运行时间预算 timeout_seconds={self._budget.timeout_seconds}",
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
        """在 daemon worker 中执行操作；超时则协作取消并等待其退出。

        语义：

        - worker 在协作取消令牌下运行操作，因此底层模型 / 工具 / Adapter 可以
          通过 :func:`current_cancellation` 观察取消；
        - 若在 deadline 内完成，本方法按既有语义返回结果或原样抛出异常；
        - 若越过 deadline，则**先置位取消令牌**再有限等待 worker 协作退出，最后
          抛出超时异常；迟到结果一律丢弃，绝不冒充取消。

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
            token = _CURRENT_CANCELLATION.set(cancellation)
            try:
                result = operation()
            except BaseException as exc:  # 原异常交由调用线程按既有语义处理。
                outcome.put((False, exc))
                return
            finally:
                _CURRENT_CANCELLATION.reset(token)
            outcome.put((True, result))

        worker = Thread(target=execute, daemon=True, name=TOOL_LOOP_WORKER_NAME)
        worker.start()
        worker.join(remaining)
        if worker.is_alive():
            # 真取消：置位协作取消令牌，让操作停止产生副作用，再有限等待其退出。
            cancellation.cancel()
            worker.join(_CANCELLATION_GRACE_SECONDS)
            raise _RunnerDeadlineExceeded
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
