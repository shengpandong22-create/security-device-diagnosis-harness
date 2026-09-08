"""FakeLLM：用于单元测试的确定性模型客户端。

它只回放预设响应，不访问网络，也不需要任何 API Key。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from security_diagnosis_harness.ports.llm import (
    FinishReason,
    LLMRequest,
    LLMResponse,
)

Responder = Callable[[LLMRequest], LLMResponse]


class FakeLLM:
    """按预设顺序回放响应的假模型。

    两种工作模式：

    - 预设序列模式：按 `responses` 顺序回放，用于单元测试；
    - 脚本模式：传入 `responder`，根据请求内容生成响应，可重复运行，用于 demo/API 闭环。

    两种模式都不访问网络，也不需要任何 API Key。
    """

    def __init__(
        self,
        responses: Sequence[LLMResponse] = (),
        responder: Responder | None = None,
    ) -> None:
        self._responses: list[LLMResponse] = list(responses)
        self._responder = responder
        self.requests: list[LLMRequest] = []

    @property
    def external_model_called(self) -> bool:
        """FakeLLM 永远不调用外部模型，用于报告与测试断言。"""
        return False

    def complete(self, request: LLMRequest) -> LLMResponse:
        """回放下一条预设响应，并记录本次请求。"""
        self.requests.append(request)
        if self._responder is not None:
            return self._responder(request)
        if not self._responses:
            return LLMResponse(
                finish_reason=FinishReason.ERROR,
                error="FakeLLM 没有更多预设响应",
            )
        return self._responses.pop(0)

    @property
    def call_count(self) -> int:
        """已收到的请求次数。"""
        return len(self.requests)
