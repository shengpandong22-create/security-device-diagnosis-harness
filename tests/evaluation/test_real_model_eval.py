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
        return EvaluationModelResponse(
            model_version="model-v1",
            candidate_label="candidate",
            explanation="reason",
            selected_tools=("tool-a",),
            cited_evidence_types=("device_status",),
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
    assert set(payload) == {"case_id", "fault_type", "input_facts", "allowed_tools", "budget"}
    assert "expected_candidate" not in dumped
    assert "required_evidence_types" not in dumped
    assert "source_record_id" not in dumped
    assert "template_group_id" not in dumped


def test_runner_calls_every_case_exactly_once(validation_cases):
    client = RecordingClient()
    report = RealModelEvaluationRunner(client, _settings()).run(validation_cases)
    assert len(client.payloads) == 2
    assert report.total_cases == 2
    assert report.total_calls == 2
    assert all(item.call_count == 1 for item in report.results)
    assert report.report_kind == "real_model"
    assert report.candidate_accuracy == 0


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
