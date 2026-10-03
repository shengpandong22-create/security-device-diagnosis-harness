from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from scripts.eval_phase7_real_model import main as real_model_main

from security_diagnosis_harness.evaluation import (
    DatasetRegistry,
    DatasetSplit,
    EvaluationModelResponse,
    ModelEvaluationError,
    OpenAICompatibleEvaluationClient,
    RealModelConfigurationError,
    RealModelEvaluationRunner,
    RealModelSettings,
    build_whitelisted_model_input,
    write_real_model_report,
)

DATASET_ROOT = (
    Path(__file__).resolve().parents[2] / "datasets" / "security-diagnosis" / "1.0.0"
)


@pytest.fixture(scope="module")
def validation_cases():
    return DatasetRegistry.load(DATASET_ROOT).cases(DatasetSplit.VALIDATION)


class RecordingClient:
    def __init__(self, *, fail_first: bool = False):
        self.payloads = []
        self.fail_first = fail_first

    def evaluate(self, payload, *, max_completion_tokens):
        self.payloads.append(payload)
        if self.fail_first and len(self.payloads) == 1:
            raise ModelEvaluationError("timeout")
        candidate = payload["candidate_label_options"][0]
        evidence_type = payload["allowed_evidence_types"][0]
        return EvaluationModelResponse(
            model_version="model-v1",
            candidate_label=candidate,
            explanation="reason",
            selected_tools=(payload["allowed_tools"][0],),
            cited_evidence_types=(evidence_type,),
            prompt_tokens=100,
            completion_tokens=20,
        )


def _settings(**changes):
    values = {
        "base_url": "https://model.example/v1",
        "model_name": "example-model",
        "api_key": "unit-test-key",
        "timeout_seconds": 60,
        "max_cases": 4,
        "input_cost_per_million": 2,
        "output_cost_per_million": 4,
    }
    values.update(changes)
    return RealModelSettings(**values)


def test_whitelist_excludes_expected_answers_and_source(validation_cases):
    payload = build_whitelisted_model_input(validation_cases[0])
    dumped = json.dumps(payload, ensure_ascii=False)
    assert set(payload) == {
        "case_id",
        "fault_type",
        "input_facts",
        "allowed_tools",
        "candidate_label_options",
        "allowed_evidence_types",
        "budget",
    }
    assert "expected_candidate" not in dumped
    assert "required_evidence_types" not in dumped
    assert "source_record_id" not in dumped
    assert "template_group_id" not in dumped
    assert set(validation_cases[0].expected_tools) != set(payload["allowed_tools"])
    assert set(validation_cases[0].required_evidence_types) != set(
        payload["allowed_evidence_types"]
    )


def test_whitelist_exposes_complete_options_not_case_answer(validation_cases):
    first, second = validation_cases
    first_payload = build_whitelisted_model_input(first)
    second_payload = build_whitelisted_model_input(second)
    assert len(first_payload["candidate_label_options"]) > 1
    assert len(second_payload["candidate_label_options"]) > 1
    assert first.expected_candidate in first_payload["candidate_label_options"]
    assert second.expected_candidate in second_payload["candidate_label_options"]
    assert "expected_candidate" not in first_payload
    assert "expected_tools" not in first_payload
    assert "required_evidence_types" not in first_payload


def test_runner_calls_every_case_exactly_once(validation_cases):
    client = RecordingClient()
    report = RealModelEvaluationRunner(client, _settings()).run(validation_cases)
    assert len(client.payloads) == 2
    assert report.total_cases == 2
    assert report.total_calls == 2
    assert all(item.call_count == 1 for item in report.results)
    assert report.report_kind == "real_model"
    assert report.candidate_accuracy == 0
    assert report.review_pending_cases == 2
    assert len(report.review_candidates) == 2


def test_failure_is_recorded_without_retry_and_next_case_continues(validation_cases):
    client = RecordingClient(fail_first=True)
    report = RealModelEvaluationRunner(client, _settings()).run(validation_cases)
    assert len(client.payloads) == 2
    assert report.failed_cases == 1
    assert report.successful_cases == 1
    assert report.results[0].error_type == "timeout"
    assert report.results[0].call_count == 1


def test_token_and_cost_are_aggregated(validation_cases):
    report = RealModelEvaluationRunner(RecordingClient(), _settings()).run(validation_cases)
    assert report.prompt_tokens == 200
    assert report.completion_tokens == 40
    assert report.estimated_cost == pytest.approx(0.00056)


def test_exact_canonical_response_gets_separate_quality_scores(validation_cases):
    case = validation_cases[0]

    class ExactClient:
        def evaluate(self, payload, *, max_completion_tokens):
            return EvaluationModelResponse(
                model_version="model-v1",
                candidate_label=case.expected_candidate,
                explanation="reason",
                selected_tools=case.expected_tools,
                cited_evidence_types=tuple(
                    item.value for item in case.required_evidence_types
                ),
            )

    report = RealModelEvaluationRunner(ExactClient(), _settings()).run((case,))
    result = report.results[0]
    assert result.ok is True
    assert result.candidate_label_valid is True
    assert result.tools_valid is True
    assert result.evidence_types_valid is True
    assert result.tool_precision == 1
    assert result.tool_recall == 1
    assert result.evidence_recall == 1
    assert result.review_pending is False
    assert report.candidate_accuracy == 1
    assert report.evidence_compliance == 1


def test_out_of_taxonomy_response_fails_and_enters_review(validation_cases):
    case = validation_cases[1]

    class UnboundedClient:
        def evaluate(self, payload, *, max_completion_tokens):
            return EvaluationModelResponse(
                model_version="model-v1",
                candidate_label="environmental_interference_suspected_false_positive",
                explanation="语义接近但不是规范标签",
                selected_tools=("unknown__tool",),
                cited_evidence_types=("environment",),
            )

    report = RealModelEvaluationRunner(UnboundedClient(), _settings()).run((case,))
    result = report.results[0]
    assert result.ok is False
    assert result.quality_errors == (
        "candidate_label_out_of_taxonomy",
        "tool_selection_invalid",
        "evidence_type_out_of_taxonomy",
    )
    assert result.review_pending is True
    assert report.review_pending_cases == 1
    assert report.review_candidates[0].status == "review_pending"
    assert report.review_candidates[0].observed_candidate == result.candidate_label


def test_valid_but_wrong_candidate_is_not_auto_normalized(validation_cases):
    case = validation_cases[0]

    class WrongClient:
        def evaluate(self, payload, *, max_completion_tokens):
            return EvaluationModelResponse(
                model_version="model-v1",
                candidate_label=payload["candidate_label_options"][0],
                explanation="wrong",
                selected_tools=case.expected_tools,
                cited_evidence_types=tuple(
                    item.value for item in case.required_evidence_types
                ),
            )

    report = RealModelEvaluationRunner(WrongClient(), _settings()).run((case,))
    result = report.results[0]
    assert result.ok is True
    assert result.expected_match is False
    assert result.review_pending is True
    assert report.candidate_accuracy == 0
    assert report.review_candidates[0].expected_candidate == case.expected_candidate


@pytest.mark.parametrize(
    ("response_changes", "settings_changes", "error_type"),
    [
        ({"prompt_tokens": 2001}, {}, "prompt_token_budget_exceeded"),
        ({"completion_tokens": 501}, {}, "completion_token_budget_exceeded"),
        (
            {"prompt_tokens": 1000, "completion_tokens": 500},
            {"input_cost_per_million": 100, "output_cost_per_million": 100},
            "cost_budget_exceeded",
        ),
    ],
)
def test_response_budget_overrun_fails_without_retry(
    validation_cases, response_changes, settings_changes, error_type
):
    class BudgetClient(RecordingClient):
        def evaluate(self, payload, *, max_completion_tokens):
            response = super().evaluate(
                payload, max_completion_tokens=max_completion_tokens
            )
            return response.model_copy(update=response_changes)

    client = BudgetClient()
    report = RealModelEvaluationRunner(client, _settings(**settings_changes)).run(
        validation_cases[:1]
    )
    assert len(client.payloads) == 1
    assert report.failed_cases == 1
    assert report.results[0].error_type == error_type
    assert report.results[0].expected_match is None


def test_model_explanation_is_redacted(validation_cases):
    class SecretClient(RecordingClient):
        def evaluate(self, payload, *, max_completion_tokens):
            response = super().evaluate(
                payload, max_completion_tokens=max_completion_tokens
            )
            return response.model_copy(update={"explanation": "token=plain-secret-value"})

    report = RealModelEvaluationRunner(SecretClient(), _settings()).run(validation_cases[:1])
    assert "plain-secret-value" not in (report.results[0].explanation or "")
    assert "***REDACTED***" in (report.results[0].explanation or "")


def test_all_provider_controlled_text_is_redacted(validation_cases):
    class SecretFieldsClient(RecordingClient):
        def evaluate(self, payload, *, max_completion_tokens):
            super().evaluate(payload, max_completion_tokens=max_completion_tokens)
            return EvaluationModelResponse(
                model_version="token=model-version-secret",
                candidate_label="password=candidate-secret",
                explanation="safe",
                selected_tools=("token=tool-secret",),
                cited_evidence_types=("password=evidence-secret",),
            )

    report = RealModelEvaluationRunner(SecretFieldsClient(), _settings()).run(
        validation_cases[:1]
    )
    dumped = report.model_dump_json()
    for secret in ("model-version-secret", "candidate-secret", "tool-secret", "evidence-secret"):
        assert secret not in dumped


def test_case_count_budget_is_enforced_before_calls(validation_cases):
    client = RecordingClient()
    with pytest.raises(RealModelConfigurationError, match="案例数量"):
        RealModelEvaluationRunner(client, _settings(max_cases=1)).run(validation_cases)
    assert client.payloads == []


def test_empty_batch_is_rejected_without_calls():
    client = RecordingClient()
    with pytest.raises(RealModelConfigurationError, match="至少需要一个案例"):
        RealModelEvaluationRunner(client, _settings()).run(())
    assert client.payloads == []


def test_timeout_cannot_exceed_dataset_budget(validation_cases):
    client = RecordingClient()
    with pytest.raises(RealModelConfigurationError, match="超时配置"):
        RealModelEvaluationRunner(client, _settings(timeout_seconds=61)).run(validation_cases)
    assert client.payloads == []


def test_case_must_explicitly_allow_exactly_one_model_call():
    dev_cases = DatasetRegistry.load(DATASET_ROOT).cases(DatasetSplit.DEV)
    client = RecordingClient()
    with pytest.raises(RealModelConfigurationError, match="恰好为 1"):
        RealModelEvaluationRunner(client, _settings()).run(dev_cases)
    assert client.payloads == []


def test_entire_batch_is_preflighted_before_first_call(validation_cases):
    invalid_case = validation_cases[0].model_copy(
        update={
            "case_id": "invalid-later-case",
            "budget": validation_cases[0].budget.model_copy(
                update={"max_model_calls": 0}
            ),
        }
    )
    client = RecordingClient()
    with pytest.raises(RealModelConfigurationError, match="恰好为 1"):
        RealModelEvaluationRunner(client, _settings()).run(
            (validation_cases[0], invalid_case)
        )
    assert client.payloads == []


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example/v1",
        "file:///tmp/model",
        "ftp://model.example",
        "https://user:password@model.example/v1",
        "https://model.example/v1?api_key=secret",
    ],
)
def test_non_https_remote_model_url_is_rejected(url):
    with pytest.raises(RealModelConfigurationError):
        _settings(base_url=url)


def test_local_http_model_url_is_allowed():
    assert _settings(base_url="http://127.0.0.1:18080/v1").base_url.startswith("http://")


def test_settings_repr_never_exposes_key():
    rendered = repr(_settings(api_key="do-not-print-this"))
    assert "do-not-print-this" not in rendered
    assert "***REDACTED***" in rendered


def test_model_name_cannot_contain_sensitive_material():
    with pytest.raises(RealModelConfigurationError, match="敏感信息"):
        _settings(model_name="token=do-not-store-this")


def test_settings_from_env_does_not_require_dotenv(monkeypatch):
    monkeypatch.setenv("SECURITY_DIAGNOSIS_EVAL_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("SECURITY_DIAGNOSIS_EVAL_MODEL", "model")
    monkeypatch.setenv("SECURITY_DIAGNOSIS_EVAL_API_KEY", "key")
    settings = RealModelSettings.from_env()
    assert settings.model_name == "model"


def test_missing_environment_is_controlled(monkeypatch):
    for key in (
        "SECURITY_DIAGNOSIS_EVAL_BASE_URL",
        "SECURITY_DIAGNOSIS_EVAL_MODEL",
        "SECURITY_DIAGNOSIS_EVAL_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RealModelConfigurationError, match="环境变量"):
        RealModelSettings.from_env()


def test_openai_compatible_client_sends_one_request_and_parses_usage():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "server-model-v2",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "candidate_label": "label",
                                    "explanation": "reason",
                                    "selected_tools": ["tool"],
                                    "cited_evidence_types": ["device_status"],
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://model.test")
    client = OpenAICompatibleEvaluationClient(_settings(), http)
    response = client.evaluate({"facts": {"online": False}}, max_completion_tokens=123)
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer unit-test-key"
    assert json.loads(requests[0].content)["max_tokens"] == 123
    assert json.loads(requests[0].content)["thinking"] == {"type": "disabled"}
    assert response.model_version == "server-model-v2"
    assert response.prompt_tokens == 12
    assert response.completion_tokens == 4


def test_http_error_does_not_retry_or_expose_response_body():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, text="secret provider response")

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://model.test")
    client = OpenAICompatibleEvaluationClient(_settings(), http)
    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=100)
    assert calls == 1
    assert excinfo.value.error_type == "http_429"
    assert "secret provider response" not in str(excinfo.value)


def test_invalid_provider_json_is_controlled():
    http = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": []})),
        base_url="https://model.test",
    )
    client = OpenAICompatibleEvaluationClient(_settings(), http)
    with pytest.raises(ModelEvaluationError, match="invalid_response"):
        client.evaluate({"facts": {}}, max_completion_tokens=100)


@pytest.mark.parametrize(
    ("choice", "error_type"),
    [
        (
            {"finish_reason": "length", "message": {"content": "{}"}},
            "output_truncated",
        ),
        ({"finish_reason": "stop", "message": {"content": ""}}, "empty_content"),
        (
            {"finish_reason": "stop", "message": {"content": "not-json"}},
            "invalid_json",
        ),
    ],
)
def test_provider_output_failures_have_precise_safe_error_types(choice, error_type):
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"choices": [choice]})
        ),
        base_url="https://model.test",
    )
    client = OpenAICompatibleEvaluationClient(_settings(), http)
    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=100)
    assert excinfo.value.error_type == error_type


_VALID_CONTENT = json.dumps({"candidate_label": "label", "explanation": "reason"})


def _usage_client(body: dict, **settings_changes) -> OpenAICompatibleEvaluationClient:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        base_url="https://model.test",
    )
    return OpenAICompatibleEvaluationClient(_settings(**settings_changes), http)


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": 12},
        {"completion_tokens": 4},
        {"prompt_tokens": "12", "completion_tokens": 4},
        {"prompt_tokens": 12.0, "completion_tokens": 4},
        {"prompt_tokens": -1, "completion_tokens": 4},
        {"prompt_tokens": True, "completion_tokens": 4},
    ],
)
def test_missing_or_invalid_usage_fails_closed(usage):
    """usage 缺失或非法必须 fail closed，不得退化成 0 token / 0 cost。"""
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body: dict = {"choices": [{"message": {"content": _VALID_CONTENT}}]}
        if usage is not None:
            body["usage"] = usage
        return httpx.Response(200, json=body)

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://model.test"
    )
    client = OpenAICompatibleEvaluationClient(_settings(), http)

    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=100)

    assert excinfo.value.error_type in {"missing_usage", "invalid_usage"}
    # 每案例仍然只发一次请求，无重试。
    assert calls == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"max_response_bytes": 0},
        {"max_response_bytes": 64 * 1_048_576 + 1},
        {"max_content_chars": 0},
        {"max_content_chars": 4_000_001},
    ],
)
def test_invalid_response_size_limits_are_rejected(changes):
    with pytest.raises(RealModelConfigurationError, match="max_"):
        _settings(**changes)


def test_oversized_response_body_fails_closed_without_parsing():
    secret = "provider-secret-body-content"
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=("x" * 5_000) + secret)

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://model.test"
    )
    client = OpenAICompatibleEvaluationClient(_settings(max_response_bytes=1_000), http)

    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=100)

    assert excinfo.value.error_type == "response_too_large"
    assert calls == 1
    # 错误不得包含响应正文或 endpoint。
    assert secret not in str(excinfo.value)
    assert "model.test" not in str(excinfo.value)


def test_oversized_content_fails_closed():
    body = {
        "choices": [{"message": {"content": "a" * 5_000}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    client = _usage_client(body, max_content_chars=1_000)

    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=100)

    assert excinfo.value.error_type == "content_too_large"


def test_error_chain_never_carries_httpx_exception_or_endpoint():
    """受控错误不得把 httpx 异常（含 URL / 正文）挂在因果链上。"""
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, text="secret provider response")

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://model.test"
    )
    client = OpenAICompatibleEvaluationClient(_settings(), http)

    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=100)

    assert calls == 1
    assert excinfo.value.error_type == "http_429"
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    assert "secret provider response" not in str(excinfo.value)
    assert "model.test" not in str(excinfo.value)


def test_valid_usage_is_required_and_forwarded(validation_cases):
    """合法 usage 仍被正常解析；同时确认每案例只有一次请求。"""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "server-model",
                "choices": [{"message": {"content": _VALID_CONTENT}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://model.test"
    )
    client = OpenAICompatibleEvaluationClient(_settings(), http)
    response = client.evaluate({"facts": {}}, max_completion_tokens=50)

    assert len(requests) == 1
    assert response.prompt_tokens == 7
    assert response.completion_tokens == 3


# ------------------------------------------------- 传输期响应边界（流式限长读取）
class CountingStream(httpx.SyncByteStream):
    """分块响应体，记录被真正读取的块数与是否被关闭。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


def _streaming_client(stream: CountingStream) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, stream=stream, headers={"content-type": "application/json"}
            )
        ),
        base_url="https://model.test",
    )


def _model_client(stream: CountingStream, **changes) -> OpenAICompatibleEvaluationClient:
    return OpenAICompatibleEvaluationClient(
        _settings(**changes), _streaming_client(stream)
    )


def _valid_body() -> bytes:
    return json.dumps(
        {
            "model": "server-model",
            "choices": [{"message": {"content": _VALID_CONTENT}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }
    ).encode()


def test_small_response_is_read_completely_and_parsed():
    body = _valid_body()
    stream = CountingStream([body])
    client = _model_client(stream, max_response_bytes=len(body) + 10)

    response = client.evaluate({"facts": {}}, max_completion_tokens=50)

    assert response.prompt_tokens == 7
    assert stream.consumed == 1
    assert stream.closed is True


def test_response_exactly_at_the_limit_is_accepted():
    body = _valid_body()
    stream = CountingStream([body])
    client = _model_client(stream, max_response_bytes=len(body))

    response = client.evaluate({"facts": {}}, max_completion_tokens=50)

    assert response.completion_tokens == 3
    assert stream.consumed == 1


def test_response_one_byte_over_the_limit_is_rejected():
    body = _valid_body()
    stream = CountingStream([body])
    client = _model_client(stream, max_response_bytes=len(body) - 1)

    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=50)

    assert excinfo.value.error_type == "response_too_large"
    assert stream.closed is True


def test_stream_reading_stops_at_the_first_over_limit_chunk():
    """第一块就超限时必须立刻停止，不读取后续块。"""
    stream = CountingStream([b"x" * 100, b"y" * 100, b"z" * 100])
    client = _model_client(stream, max_response_bytes=10)

    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=50)

    assert excinfo.value.error_type == "response_too_large"
    assert stream.consumed == 1  # 后续块从未被读取
    assert stream.closed is True


def test_stream_reading_stops_after_accumulated_chunks_exceed_the_limit():
    """多块累计后才超限时，剩余块不得被读取（不是完整缓冲后才报错）。"""
    stream = CountingStream([b"x" * 1_000 for _ in range(20)])
    client = _model_client(stream, max_response_bytes=5_000)

    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=50)

    assert excinfo.value.error_type == "response_too_large"
    assert stream.consumed == 6  # 5 块正好到达上限，第 6 块触发停止
    assert stream.consumed < len(stream.chunks)
    assert stream.closed is True


def test_over_limit_response_never_reaches_json_parsing():
    """超限的响应体不是合法 JSON：若先解析就会得到 invalid_response。"""
    stream = CountingStream([b"not-json" * 500])
    client = _model_client(stream, max_response_bytes=16)

    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=50)

    assert excinfo.value.error_type == "response_too_large"


def test_over_limit_error_leaks_no_endpoint_body_or_credentials():
    secret = b"PROVIDER-SECRET-BODY"
    stream = CountingStream([secret * 200])
    client = _model_client(stream, max_response_bytes=32)

    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=50)

    text = str(excinfo.value)
    assert "PROVIDER-SECRET-BODY" not in text
    assert "model.test" not in text
    assert "unit-test-key" not in text
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_stream_limit_keeps_one_request_and_no_retry():
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, stream=CountingStream([b"x" * 1_000]))

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://model.test"
    )
    client = OpenAICompatibleEvaluationClient(_settings(max_response_bytes=16), http)

    with pytest.raises(ModelEvaluationError) as excinfo:
        client.evaluate({"facts": {}}, max_completion_tokens=50)

    assert excinfo.value.error_type == "response_too_large"
    assert calls == [1]


def test_http_status_and_invalid_json_classification_are_preserved():
    def status_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    http = httpx.Client(
        transport=httpx.MockTransport(status_handler), base_url="https://model.test"
    )
    with pytest.raises(ModelEvaluationError) as status_error:
        OpenAICompatibleEvaluationClient(_settings(), http).evaluate(
            {"facts": {}}, max_completion_tokens=50
        )
    assert status_error.value.error_type == "http_429"

    bad_stream = CountingStream([b"{not json"])
    with pytest.raises(ModelEvaluationError) as json_error:
        _model_client(bad_stream, max_response_bytes=1_000).evaluate(
            {"facts": {}}, max_completion_tokens=50
        )
    assert json_error.value.error_type == "invalid_response"


def test_report_files_are_separate_and_contain_no_input_facts(validation_cases, tmp_path):
    report = RealModelEvaluationRunner(RecordingClient(), _settings()).run(validation_cases)
    json_path, markdown_path = write_real_model_report(report, tmp_path)
    combined = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")
    assert "real_model" in combined
    assert "input_facts" not in combined
    assert "presented_badge_state" not in combined
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_requires_explicit_real_model_flag(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["eval_phase7_real_model.py"])
    assert real_model_main() == 2
    assert '"executed": false' in capsys.readouterr().out.lower()


def test_cli_requires_pre_signed_dataset_anchor(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["eval_phase7_real_model.py", "--execute-real-model"],
    )

    assert real_model_main() == 2
    assert "--dataset-anchor" in capsys.readouterr().out
