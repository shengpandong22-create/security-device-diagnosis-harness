"""Phase 7D 低频真实模型评测：白名单输入、单次调用、无自动重试。"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from security_diagnosis_harness.domain.redaction import redact_mapping, redact_text
from security_diagnosis_harness.evaluation.dataset import DatasetCase


class RealModelConfigurationError(ValueError):
    """真实模型评测配置无效。"""


class ModelEvaluationError(RuntimeError):
    """一次真实模型调用受控失败，不携带响应正文或密钥。"""

    def __init__(self, error_type: str):
        self.error_type = error_type
        super().__init__(f"真实模型评测失败: {error_type}")


@dataclass(frozen=True)
class RealModelSettings:
    base_url: str
    model_name: str
    api_key: str
    timeout_seconds: float = 60.0
    max_cases: int = 4
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
            raise RealModelConfigurationError("模型地址必须使用 HTTPS 或本机 HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RealModelConfigurationError("模型地址不得包含凭证、查询参数或片段")
        if not self.model_name.strip() or not self.api_key.strip():
            raise RealModelConfigurationError("模型名称和 API Key 不能为空")
        safe_model_name, model_name_changed = redact_text(self.model_name)
        if model_name_changed or safe_model_name != self.model_name:
            raise RealModelConfigurationError("模型名称不得包含敏感信息")
        if not 1 <= self.max_cases <= 20:
            raise RealModelConfigurationError("max_cases 必须在 1 到 20 之间")
        if not 1 <= self.timeout_seconds <= 300:
            raise RealModelConfigurationError("timeout_seconds 必须在 1 到 300 秒之间")
        if self.input_cost_per_million < 0 or self.output_cost_per_million < 0:
            raise RealModelConfigurationError("token 单价不能为负数")

    @classmethod
    def from_env(cls) -> RealModelSettings:
        """只读取显式环境变量，不加载 `.env`。"""
        try:
            return cls(
                base_url=os.environ["SECURITY_DIAGNOSIS_EVAL_BASE_URL"],
                model_name=os.environ["SECURITY_DIAGNOSIS_EVAL_MODEL"],
                api_key=os.environ["SECURITY_DIAGNOSIS_EVAL_API_KEY"],
                timeout_seconds=float(os.getenv("SECURITY_DIAGNOSIS_EVAL_TIMEOUT", "60")),
                max_cases=int(os.getenv("SECURITY_DIAGNOSIS_EVAL_MAX_CASES", "4")),
                input_cost_per_million=float(
                    os.getenv("SECURITY_DIAGNOSIS_EVAL_INPUT_COST_PER_MILLION", "0")
                ),
                output_cost_per_million=float(
                    os.getenv("SECURITY_DIAGNOSIS_EVAL_OUTPUT_COST_PER_MILLION", "0")
                ),
            )
        except (KeyError, ValueError) as exc:
            raise RealModelConfigurationError("真实模型评测环境变量缺失或格式非法") from exc

    def __repr__(self) -> str:
        return (
            "RealModelSettings("
            f"base_url={self.base_url!r}, model_name={self.model_name!r}, "
            "api_key='***REDACTED***', "
            f"timeout_seconds={self.timeout_seconds}, max_cases={self.max_cases})"
        )


class EvaluationModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_version: str
    candidate_label: str
    explanation: str
    selected_tools: tuple[str, ...] = ()
    cited_evidence_types: tuple[str, ...] = ()
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class EvaluationModelClient(Protocol):
    def evaluate(
        self, payload: Mapping[str, Any], *, max_completion_tokens: int
    ) -> EvaluationModelResponse: ...


class OpenAICompatibleEvaluationClient:
    """单次 `/chat/completions` 调用；本类没有重试循环。"""

    def __init__(self, settings: RealModelSettings, client: httpx.Client | None = None):
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=settings.base_url.rstrip("/"), timeout=settings.timeout_seconds
        )

    def evaluate(
        self, payload: Mapping[str, Any], *, max_completion_tokens: int
    ) -> EvaluationModelResponse:
        try:
            response = self._client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {self._settings.api_key}"},
                json={
                    "model": self._settings.model_name,
                    "temperature": 0,
                    "max_tokens": max_completion_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是安防诊断评测模型。只基于输入事实返回 JSON，字段为 "
                                "candidate_label、explanation、selected_tools、"
                                "cited_evidence_types；不得返回 confirmed。"
                            ),
                        },
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                },
            )
            response.raise_for_status()
            body = response.json()
            content = json.loads(body["choices"][0]["message"]["content"])
            usage = body.get("usage", {})
            return EvaluationModelResponse(
                model_version=str(body.get("model") or self._settings.model_name),
                candidate_label=content["candidate_label"],
                explanation=content["explanation"],
                selected_tools=tuple(content.get("selected_tools", ())),
                cited_evidence_types=tuple(content.get("cited_evidence_types", ())),
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
            )
        except httpx.TimeoutException as exc:
            raise ModelEvaluationError("timeout") from exc
        except httpx.HTTPStatusError as exc:
            raise ModelEvaluationError(f"http_{exc.response.status_code}") from exc
        except (httpx.HTTPError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise ModelEvaluationError("invalid_response") from exc

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OpenAICompatibleEvaluationClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class RealModelCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    ok: bool
    model_version: str
    candidate_label: str | None = None
    explanation: str | None = None
    selected_tools: tuple[str, ...] = ()
    cited_evidence_types: tuple[str, ...] = ()
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0
    estimated_cost: float = 0
    error_type: str | None = None
    call_count: int = 1
    expected_match: bool | None = None


class RealModelBatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_kind: str = "real_model"
    model_name: str
    total_cases: int
    total_calls: int
    successful_cases: int
    failed_cases: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost: float
    candidate_accuracy: float
    results: tuple[RealModelCaseResult, ...]

    def to_markdown(self) -> str:
        lines = [
            "# Phase 7D 低频真实模型评测",
            "",
            "- 报告类型：`real_model`（不得与 Fake 基线混报）",
            f"- 模型：`{self.model_name}`",
            f"- 案例数 / 调用数：`{self.total_cases}` / `{self.total_calls}`",
            f"- 成功 / 失败：`{self.successful_cases}` / `{self.failed_cases}`",
            f"- Candidate Accuracy：`{self.candidate_accuracy:.4f}`",
            f"- Token：`{self.prompt_tokens}` input / `{self.completion_tokens}` output",
            f"- 估算成本：`{self.estimated_cost:.8f}`",
            "",
            "## 案例结果",
            "",
            "| Case | 成功 | 模型版本 | 候选 | 匹配 | 时延(ms) | 错误 |",
            "| --- | --- | --- | --- | --- | ---: | --- |",
        ]
        for item in self.results:
            lines.append(
                f"| {item.case_id} | {item.ok} | {item.model_version} | "
                f"{item.candidate_label or '-'} | {item.expected_match} | "
                f"{item.latency_ms:.2f} | {item.error_type or '-'} |"
            )
        return "\n".join(lines) + "\n"


class RealModelEvaluationRunner:
    def __init__(self, client: EvaluationModelClient, settings: RealModelSettings):
        self._client = client
        self._settings = settings

    def run(self, cases: Sequence[DatasetCase]) -> RealModelBatchReport:
        if not cases:
            raise RealModelConfigurationError("真实模型评测至少需要一个案例")
        if len(cases) > self._settings.max_cases:
            raise RealModelConfigurationError("案例数量超过本次真实模型评测上限")
        for case in cases:
            if case.budget.max_model_calls != 1:
                raise RealModelConfigurationError(
                    f"案例 {case.case_id} 的 max_model_calls 必须恰好为 1"
                )
            if self._settings.timeout_seconds > case.budget.timeout_seconds:
                raise RealModelConfigurationError(
                    f"案例 {case.case_id} 的模型超时配置超过数据集预算"
                )

        # 必须先完成整批预算预检，避免后续案例配置非法时已经消耗真实调用。
        results: list[RealModelCaseResult] = []
        for case in cases:
            payload = build_whitelisted_model_input(case)
            started = time.perf_counter()
            try:
                response = self._client.evaluate(
                    payload,
                    max_completion_tokens=case.budget.max_completion_tokens,
                )
                latency_ms = (time.perf_counter() - started) * 1000
                estimated_cost = self._cost(response)
                budget_error = _response_budget_error(case, response, estimated_cost)
                results.append(
                    RealModelCaseResult(
                        case_id=case.case_id,
                        ok=budget_error is None,
                        model_version=_safe_text(response.model_version),
                        candidate_label=_safe_text(response.candidate_label),
                        explanation=_safe_text(response.explanation),
                        selected_tools=tuple(_safe_text(item) for item in response.selected_tools),
                        cited_evidence_types=tuple(
                            _safe_text(item) for item in response.cited_evidence_types
                        ),
                        prompt_tokens=response.prompt_tokens,
                        completion_tokens=response.completion_tokens,
                        latency_ms=latency_ms,
                        estimated_cost=estimated_cost,
                        error_type=budget_error,
                        expected_match=(
                            response.candidate_label == case.expected_candidate
                            if budget_error is None
                            else None
                        ),
                    )
                )
            except ModelEvaluationError as exc:
                results.append(
                    RealModelCaseResult(
                        case_id=case.case_id,
                        ok=False,
                        model_version=_safe_text(self._settings.model_name),
                        latency_ms=(time.perf_counter() - started) * 1000,
                        error_type=exc.error_type,
                    )
                )
        return RealModelBatchReport(
            model_name=self._settings.model_name,
            total_cases=len(results),
            total_calls=len(results),
            successful_cases=sum(item.ok for item in results),
            failed_cases=sum(not item.ok for item in results),
            prompt_tokens=sum(item.prompt_tokens for item in results),
            completion_tokens=sum(item.completion_tokens for item in results),
            estimated_cost=sum(item.estimated_cost for item in results),
            candidate_accuracy=(
                sum(item.expected_match is True for item in results) / len(results)
                if results
                else 0.0
            ),
            results=tuple(results),
        )

    def _cost(self, response: EvaluationModelResponse) -> float:
        return (
            response.prompt_tokens * self._settings.input_cost_per_million
            + response.completion_tokens * self._settings.output_cost_per_million
        ) / 1_000_000


def build_whitelisted_model_input(case: DatasetCase) -> dict[str, Any]:
    """只发送业务输入，不发送 expected label、split 来源或数据集答案。"""
    safe_facts, changed = redact_mapping(case.input_facts)
    if changed or safe_facts != case.input_facts:
        raise RealModelConfigurationError("案例输入未通过脱敏白名单")
    return {
        "case_id": case.case_id,
        "fault_type": case.fault_type.value,
        "input_facts": safe_facts,
        "allowed_tools": list(case.allowed_tools),
        "budget": {
            "max_rounds": case.budget.max_rounds,
            "max_tool_calls": case.budget.max_tool_calls,
        },
    }


def _safe_text(text: str) -> str:
    cleaned, _ = redact_text(text)
    return cleaned


def _response_budget_error(
    case: DatasetCase,
    response: EvaluationModelResponse,
    estimated_cost: float,
) -> str | None:
    if response.prompt_tokens > case.budget.max_prompt_tokens:
        return "prompt_token_budget_exceeded"
    if response.completion_tokens > case.budget.max_completion_tokens:
        return "completion_token_budget_exceeded"
    if estimated_cost > case.budget.max_estimated_cost:
        return "cost_budget_exceeded"
    return None


def write_real_model_report(
    report: RealModelBatchReport, output_directory: Path
) -> tuple[Path, Path]:
    """只保存受控结果，不保存发送给模型的原始 facts。"""
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "phase7-real-model-report.json"
    markdown_path = output_directory / "phase7-real-model-report.md"
    _atomic_write(json_path, report.model_dump_json(indent=2))
    _atomic_write(markdown_path, report.to_markdown())
    return json_path, markdown_path


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
