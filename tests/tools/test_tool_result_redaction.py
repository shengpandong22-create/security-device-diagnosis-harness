from __future__ import annotations

from pydantic import BaseModel

from security_diagnosis_harness.adapters.llm.fake import FakeLLM
from security_diagnosis_harness.agent.runner import ToolLoopRunner
from security_diagnosis_harness.domain.enums import SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceSource, EvidenceType
from security_diagnosis_harness.ports.llm import FinishReason, LLMResponse, ToolCall
from security_diagnosis_harness.tools.contracts import (
    BaseTool,
    ToolEvidenceDraft,
    ToolExecutionContext,
    ToolExecutionResult,
    failure_result,
)
from security_diagnosis_harness.tools.registry import ToolRegistry

from ..conftest import make_case


class EmptyInput(BaseModel):
    pass


class SecretExceptionTool(BaseTool):
    name = "test__secret_exception"
    input_model = EmptyInput

    def _execute(self, arguments, context):
        raise RuntimeError("password=PLAIN-GATEWAY-SECRET")


class MutatedSecretResultTool(BaseTool):
    name = "test__mutated_result"
    input_model = EmptyInput

    def _execute(self, arguments, context):
        result = ToolExecutionResult(
            tool_name=self.name,
            observation="initial safe result",
            metadata={"status": "safe"},
            evidence_drafts=[
                ToolEvidenceDraft(
                    evidence_type=EvidenceType.DEVICE_STATUS,
                    source=EvidenceSource.DEVICE_GATEWAY,
                    summary="initial safe evidence",
                )
            ],
        )
        result.observation = "Bearer MUTATED-SECRET-TOKEN"
        result.metadata["api_key"] = "MUTATED-API-SECRET"
        result.evidence_drafts[0].payload["password"] = "MUTATED-PAYLOAD-SECRET"
        return result


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        diagnosis_id="diag_phase0a",
        fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
    )


def test_failure_result_redacts_common_secret_shapes():
    result = failure_result(
        "test", "password=PLAIN Bearer ABCDEFGHIJK https://user:pass@example.test/path"
    )
    dumped = result.model_dump_json()
    for secret in ("PLAIN", "ABCDEFGHIJK", "user:pass", "example.test"):
        assert secret not in dumped
    assert "***REDACTED***" in dumped


def test_evidence_draft_redacts_nested_payload_before_runner_sees_it():
    draft = ToolEvidenceDraft(
        evidence_type=EvidenceType.DEVICE_STATUS,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary="token=SUMMARY-SECRET",
        payload={"nested": {"client_secret": "PAYLOAD-SECRET"}},
    )
    assert "SUMMARY-SECRET" not in draft.summary
    assert "PAYLOAD-SECRET" not in draft.model_dump_json()
    assert draft.redacted is True


def test_registry_revalidates_post_construction_mutated_tool_results():
    registry = ToolRegistry()
    registry.register(MutatedSecretResultTool())
    result = registry.execute(MutatedSecretResultTool.name, {}, _context())
    dumped = result.model_dump_json()
    for secret in ("MUTATED-SECRET-TOKEN", "MUTATED-API-SECRET", "MUTATED-PAYLOAD-SECRET"):
        assert secret not in dumped
    assert "***REDACTED***" in dumped
    assert result.evidence_drafts[0].redacted is True


def test_registry_exception_does_not_expose_gateway_secret():
    registry = ToolRegistry()
    registry.register(SecretExceptionTool())
    result = registry.execute(SecretExceptionTool.name, {}, _context())
    assert result.ok is False
    assert "PLAIN-GATEWAY-SECRET" not in (result.error or "")
    assert result.evidence_drafts == []


def test_runner_never_sends_tool_exception_secret_back_to_llm():
    registry = ToolRegistry()
    registry.register(SecretExceptionTool())
    llm = FakeLLM(
        [
            LLMResponse(
                tool_calls=[ToolCall(tool_name=SecretExceptionTool.name)],
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(error="controlled stop", finish_reason=FinishReason.ERROR),
        ]
    )
    runner = ToolLoopRunner(llm, registry)
    result = runner.run(
        make_case(), _context(), tool_allowlist=[SecretExceptionTool.name]
    )
    assert result.ok is False
    serialized_requests = " ".join(request.model_dump_json() for request in llm.requests)
    assert "PLAIN-GATEWAY-SECRET" not in serialized_requests
    assert "***REDACTED***" in serialized_requests


def test_failed_result_cannot_carry_evidence():
    result = ToolExecutionResult(
        tool_name="test",
        ok=False,
        error="failed",
        evidence_drafts=[
            ToolEvidenceDraft(
                evidence_type=EvidenceType.DEVICE_STATUS,
                source=EvidenceSource.DEVICE_GATEWAY,
                summary="must not survive",
            )
        ],
    )
    assert result.evidence_drafts == []
