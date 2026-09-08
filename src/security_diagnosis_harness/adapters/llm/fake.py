"""FakeLLM：用于单元测试的确定性模型客户端。

它只回放预设响应，不访问网络，也不需要任何 API Key。
"""

from __future__ import annotations

from collections.abc import Sequence

from security_diagnosis_harness.ports.llm import (
    FinishReason,
    LLMRequest,
    LLMResponse,
)


class FakeLLM:
    """按预设顺序回放响应的假模型。"""

    def __init__(self, responses: Sequence[LLMResponse] = ()) -> None:
        self._responses: list[LLMResponse] = list(responses)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        """回放下一条预设响应，并记录本次请求。"""
        self.requests.append(request)
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
