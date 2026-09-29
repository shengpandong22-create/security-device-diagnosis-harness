"""最小 ToolLoopRunner 验收。"""

from __future__ import annotations

import multiprocessing
import threading
from collections.abc import Mapping
from time import monotonic, sleep
from typing import Any

import pytest
from pydantic import BaseModel

from security_diagnosis_harness.adapters.llm.fake import FakeLLM
from security_diagnosis_harness.agent.runner import (
    TOOL_LOOP_WORKER_NAME,
    OperationCancelledError,
    ToolLoopBudget,
    ToolLoopRunner,
    current_cancellation,
    supports_cooperative_cancellation,
)
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceType
from security_diagnosis_harness.ports.llm import (
    ConclusionDraft,
    FinishReason,
    LLMResponse,
    ToolCall,
)
from security_diagnosis_harness.tools.contracts import (
    BaseTool,
    ToolExecutionContext,
    ToolExecutionResult,
)
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


def test_deadline_is_still_enforced_for_a_non_cooperative_model(tool_registry, context):
    """非严格模式：deadline 仍然有界生效，但必须如实上报"取消未确认"。"""

    class SlowLLM:
        def complete(self, request):
            sleep(0.3)
            return _final_response()

    runner = ToolLoopRunner(
        SlowLLM(),
        tool_registry,
        ToolLoopBudget(max_rounds=1, max_tool_calls=1, timeout_seconds=0.03),
        require_cancellation_aware=False,
    )

    started = monotonic()
    result = runner.run(make_case(), context)
    elapsed = monotonic() - started

    assert result.ok is False
    assert "timeout_seconds=0.03" in (result.error or "")
    assert elapsed < 0.2
    assert result.rounds == 1
    # 未声明参与者只能被"放弃等待"，因此绝不能声称取消已确认。
    assert result.cancellation_confirmed is False
    assert "取消未确认" in (result.error or "")


def test_deadline_is_still_enforced_for_a_non_cooperative_tool(
    tool_registry, context, monkeypatch
):
    def slow_execute(*args, **kwargs):
        sleep(0.3)
        return ToolExecutionResult(tool_name="device__query_status", observation="late")

    monkeypatch.setattr(tool_registry, "execute", slow_execute)
    runner = ToolLoopRunner(
        FakeLLM([_tool_call_response()]),
        tool_registry,
        ToolLoopBudget(max_rounds=1, max_tool_calls=1, timeout_seconds=0.03),
        require_cancellation_aware=False,
    )

    started = monotonic()
    result = runner.run(make_case(), context)

    assert result.ok is False
    assert "timeout_seconds=0.03" in (result.error or "")
    assert monotonic() - started < 0.2
    assert result.tool_calls == 1
    assert result.tool_results == []
    assert result.cancellation_confirmed is False


@pytest.mark.parametrize(
    "changes",
    [
        {"max_rounds": 0},
        {"max_tool_calls": -1},
        {"timeout_seconds": 0},
    ],
)
def test_invalid_budget_is_rejected(changes):
    with pytest.raises(ValueError, match="ToolLoopBudget"):
        ToolLoopBudget(**changes)


def test_raw_model_exception_is_converted_without_leaking_detail(tool_registry, context):
    class BrokenLLM:
        cancellation_aware = True

        def complete(self, request):
            raise RuntimeError("provider password=plain-secret-detail")

    result = ToolLoopRunner(BrokenLLM(), tool_registry).run(make_case(), context)

    assert result.ok is False
    assert result.error == "模型调用失败"
    assert "secret" not in result.model_dump_json().lower()


def test_oversized_observation_never_enters_messages_or_evidence(
    tool_registry, context, monkeypatch
):
    oversized = "x" * 2_000
    monkeypatch.setattr(
        tool_registry,
        "execute",
        lambda *args, **kwargs: ToolExecutionResult(
            tool_name="device__query_status",
            observation=oversized,
        ),
    )
    runner = ToolLoopRunner(
        FakeLLM([_tool_call_response()]),
        tool_registry,
        ToolLoopBudget(max_observation_chars=100),
    )

    result = runner.run(make_case(), context)

    assert result.ok is False
    assert "max_observation_chars=100" in (result.error or "")
    assert result.tool_results == []
    assert result.evidence_drafts == []
    assert all(oversized not in message.content for message in result.messages)


def test_raw_tool_exception_is_converted_without_leaking_detail(
    tool_registry, context, monkeypatch
):
    def broken_execute(*args, **kwargs):
        raise RuntimeError("device token=plain-secret-detail")

    monkeypatch.setattr(tool_registry, "execute", broken_execute)
    result = ToolLoopRunner(FakeLLM([_tool_call_response()]), tool_registry).run(
        make_case(), context
    )

    assert result.ok is False
    assert result.error == "工具执行失败: device__query_status"
    assert "secret" not in result.model_dump_json().lower()


def test_oversized_model_response_is_rejected(tool_registry, context):
    response = _final_response().model_copy(
        update={
            "final_conclusion": _final_response().final_conclusion.model_copy(
                update={"summary": "x" * 2_000}
            )
        }
    )
    runner = ToolLoopRunner(
        FakeLLM([response]),
        tool_registry,
        ToolLoopBudget(max_model_response_chars=200),
    )

    result = runner.run(make_case(), context)

    assert result.ok is False
    assert "max_model_response_chars=200" in (result.error or "")
    assert result.final_conclusion is None


def test_initial_context_budget_is_enforced_before_model_call(tool_registry, context):
    case = make_case()
    case.description = "x" * 500
    llm = FakeLLM([_final_response()])
    runner = ToolLoopRunner(llm, tool_registry, ToolLoopBudget(max_context_chars=100))

    result = runner.run(case, context)

    assert result.ok is False
    assert "max_context_chars=100" in (result.error or "")
    assert llm.call_count == 0


def _live_workers() -> set[int]:
    return {
        thread.ident
        for thread in threading.enumerate()
        if thread.name == TOOL_LOOP_WORKER_NAME and thread.is_alive()
    }


def test_timeout_cooperatively_cancels_the_model_operation(tool_registry, context):
    """超时后底层模型操作必须停止，而不是在 daemon worker 中继续运行。"""
    side_effects: list[int] = []
    observed_cancel: list[bool] = []
    started_calls: list[int] = []

    class CooperativeSlowLLM:
        cancellation_aware = True

        def complete(self, request):
            started_calls.append(1)
            token = current_cancellation()
            assert token is not None, "底层模型必须能看到协作取消令牌"
            while not token.cancelled:
                side_effects.append(1)
                sleep(0.005)
            observed_cancel.append(True)
            return _final_response()

    before = _live_workers()
    runner = ToolLoopRunner(
        CooperativeSlowLLM(),
        tool_registry,
        ToolLoopBudget(max_rounds=1, max_tool_calls=1, timeout_seconds=0.05),
    )

    result = runner.run(make_case(), context)

    assert result.ok is False
    assert "timeout_seconds=0.05" in (result.error or "")
    assert result.final_conclusion is None
    assert result.tool_results == []
    assert result.evidence_drafts == []
    # 操作确实观察到取消并主动退出：取消已被确认。
    assert result.cancellation_confirmed is True
    # 且只被调用一次（无重试）。
    assert observed_cancel == [True]
    assert started_calls == [1]
    # 返回后副作用计数不再变化。
    settled = len(side_effects)
    assert settled > 0
    sleep(0.15)
    assert len(side_effects) == settled
    # 返回后没有遗留 worker。
    assert _live_workers() <= before


def test_timeout_cooperatively_cancels_the_tool_operation(
    tool_registry, context, monkeypatch
):
    """超时后底层工具 / Adapter 操作必须停止，不产生 Evidence。"""
    ticks: list[int] = []
    calls: list[int] = []

    def cooperative_execute(tool_name, arguments, tool_context):
        calls.append(1)
        token = current_cancellation()
        assert token is not None, "工具必须能看到协作取消令牌"
        while not token.cancelled:
            ticks.append(1)
            sleep(0.005)
        raise OperationCancelledError()

    monkeypatch.setattr(tool_registry, "execute", cooperative_execute)
    before = _live_workers()
    runner = ToolLoopRunner(
        FakeLLM([_tool_call_response()]),
        tool_registry,
        ToolLoopBudget(max_rounds=1, max_tool_calls=1, timeout_seconds=0.05),
    )

    result = runner.run(make_case(), context)

    assert result.ok is False
    assert "timeout_seconds=0.05" in (result.error or "")
    assert result.cancellation_confirmed is True
    assert result.tool_calls == 1
    assert result.tool_results == []
    assert result.evidence_drafts == []
    assert calls == [1]
    settled = len(ticks)
    assert settled > 0
    sleep(0.15)
    assert len(ticks) == settled
    assert _live_workers() <= before


def test_cancellation_token_is_absent_outside_a_runner_worker():
    assert current_cancellation() is None


# ------------------------------------------------- 执行边界：未声明参与者被拒绝执行
class _NoArgs(BaseModel):
    pass


class NonCooperativeLLM:
    """未声明协作取消、且会阻塞并产生迟到副作用的模型客户端。"""

    def __init__(self, side_effects: list[int]) -> None:
        self._side_effects = side_effects
        self.started = 0

    def complete(self, request):
        self.started += 1
        sleep(0.15)
        self._side_effects.append(1)
        return _final_response()


class UnannouncedTool(BaseTool):
    """显式放弃协作取消声明的工具：在严格模式下必须被拒绝执行。"""

    name = "probe__unannounced"
    description = "测试用：不声明协作取消的工具"
    cancellation_aware = False
    input_model = _NoArgs

    def __init__(self, side_effects: list[int]) -> None:
        self._side_effects = side_effects
        self.started = 0

    def _execute(self, arguments, context):
        self.started += 1
        self._side_effects.append(1)
        return ToolExecutionResult(tool_name=self.name, observation="side effect done")


def _no_new_threads_or_processes(before_threads: set[int], before_children: list) -> None:
    assert _live_workers() <= before_threads
    assert set(multiprocessing.active_children()) <= set(before_children)


def test_strict_mode_refuses_non_cooperative_model_before_it_runs(tool_registry, context):
    """未声明的模型客户端在严格模式下根本不会被调用。"""
    side_effects: list[int] = []
    llm = NonCooperativeLLM(side_effects)
    before_threads = _live_workers()
    before_children = multiprocessing.active_children()
    runner = ToolLoopRunner(
        llm,
        tool_registry,
        ToolLoopBudget(max_rounds=1, max_tool_calls=1, timeout_seconds=0.05),
    )

    started = monotonic()
    result = runner.run(make_case(), context)
    elapsed = monotonic() - started

    assert result.ok is False
    assert "未声明支持协作取消" in (result.error or "")
    assert result.cancellation_confirmed is False
    assert result.final_conclusion is None
    assert result.tool_results == []
    assert result.evidence_drafts == []
    assert llm.started == 0
    # 拒绝发生在调用之前，因此不依赖 deadline，也没有等待宽限期。
    assert elapsed < 0.05
    _no_new_threads_or_processes(before_threads, before_children)
    # Runner 返回后等待足够长时间：也不会出现迟到的副作用。
    sleep(0.2)
    assert side_effects == []
    assert llm.started == 0  # 无重试


def test_strict_mode_refuses_non_cooperative_tool_before_it_runs(context):
    """未声明的工具在严格模式下根本不会被调用。"""
    side_effects: list[int] = []
    tool = UnannouncedTool(side_effects)
    registry = ToolRegistry()
    registry.register(tool)
    llm = FakeLLM(
        [
            LLMResponse(
                tool_calls=[ToolCall(call_id="c1", tool_name=UnannouncedTool.name)],
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            _final_response(),
        ]
    )
    before_threads = _live_workers()
    before_children = multiprocessing.active_children()
    runner = ToolLoopRunner(
        llm,
        registry,
        ToolLoopBudget(max_rounds=2, max_tool_calls=2, timeout_seconds=0.05),
    )

    result = runner.run(make_case(), context, tool_allowlist=[UnannouncedTool.name])

    assert result.ok is False
    assert "未声明支持协作取消" in (result.error or "")
    assert result.tool_results == []
    assert result.evidence_drafts == []
    assert tool.started == 0
    assert llm.call_count == 0  # 连模型都没被调用
    _no_new_threads_or_processes(before_threads, before_children)
    sleep(0.2)
    assert side_effects == []
    assert tool.started == 0


def test_declared_but_violating_model_reports_unconfirmed_cancellation(
    tool_registry, context
):
    """已声明却违反契约的参与者：残余风险必须被显式上报，不能伪装成干净超时。"""

    class LyingLLM:
        cancellation_aware = True

        def complete(self, request):
            sleep(0.3)
            return _final_response()

    runner = ToolLoopRunner(
        LyingLLM(),
        tool_registry,
        ToolLoopBudget(max_rounds=1, max_tool_calls=1, timeout_seconds=0.05),
    )

    result = runner.run(make_case(), context)

    assert result.ok is False
    assert "timeout_seconds=0.05" in (result.error or "")
    assert "取消未确认" in (result.error or "")
    assert result.cancellation_confirmed is False
    assert result.evidence_drafts == []
    assert result.final_conclusion is None


def test_cancellation_confirmed_is_true_on_normal_and_error_paths(tool_registry, context):
    """正常返回与原始异常路径都必须是"取消已确认"。"""
    ok_result = ToolLoopRunner(FakeLLM([_final_response()]), tool_registry).run(
        make_case(), context
    )
    assert ok_result.cancellation_confirmed is True

    class BrokenLLM:
        cancellation_aware = True

        def complete(self, request):
            raise RuntimeError("boom")

    broken = ToolLoopRunner(BrokenLLM(), tool_registry).run(make_case(), context)
    assert broken.ok is False
    assert broken.error == "模型调用失败"
    assert broken.cancellation_confirmed is True


def test_cooperative_cancellation_is_declared_by_base_tool():
    assert supports_cooperative_cancellation(UnannouncedTool([])) is False

    class DeclaredTool(BaseTool):
        name = "probe__declared"
        description = "测试用：继承基类声明"
        input_model = _NoArgs

        def _execute(self, arguments, context):  # pragma: no cover - 不执行
            return ToolExecutionResult(tool_name=self.name, ok=False)

    assert supports_cooperative_cancellation(DeclaredTool()) is True


def test_formal_participants_all_declare_cooperative_cancellation():
    """证明正式装配的 LLM 与全部工具都遵守协作取消契约。"""
    from security_diagnosis_harness.bootstrap.container import (
        build_access_registry,
        build_alarm_registry,
        build_recording_registry,
        build_registry,
    )

    assert supports_cooperative_cancellation(FakeLLM()) is True
    for builder in (
        build_registry,
        build_recording_registry,
        build_access_registry,
        build_alarm_registry,
    ):
        registry = builder()
        assert registry.names(), builder.__name__
        for name in registry.names():
            assert supports_cooperative_cancellation(registry.get(name)), (
                f"{builder.__name__}::{name}"
            )


def test_strict_mode_is_the_default():
    runner = ToolLoopRunner(FakeLLM([_final_response()]), ToolRegistry())
    assert runner.require_cancellation_aware is True
