"""Phase 9C-3 应用层受控降级。"""

from security_diagnosis_harness.adapters.device_gateway.static import StaticDeviceGateway
from security_diagnosis_harness.agent.runner import ToolLoopResult
from security_diagnosis_harness.application.diagnoses import SecurityDiagnosisApplicationService
from security_diagnosis_harness.application.repository import InMemoryDiagnosisRepository
from security_diagnosis_harness.bootstrap.container import DEFAULT_DEVICE_DATA_PATH, build_registry
from security_diagnosis_harness.domain.citation_policy import CitationPolicy
from security_diagnosis_harness.domain.enums import SecurityDiagnosisStatus, SecurityFaultType
from security_diagnosis_harness.domain.evidence import EvidenceSource, EvidenceType
from security_diagnosis_harness.ports.llm import ConclusionDraft
from security_diagnosis_harness.tools.contracts import (
    ToolEvidenceDraft,
    ToolExecutionResult,
)


class StubRunner:
    def __init__(self, result: ToolLoopResult) -> None:
        self.result = result
        self.calls = 0

    def run(self, case, context, tool_allowlist=None):
        self.calls += 1
        return self.result.model_copy(update={"diagnosis_id": case.diagnosis_id})


def _service(result: ToolLoopResult):
    runner = StubRunner(result)
    registry = build_registry()
    service = SecurityDiagnosisApplicationService(
        InMemoryDiagnosisRepository(), runner, registry,
        StaticDeviceGateway(DEFAULT_DEVICE_DATA_PATH), CitationPolicy()
    )
    case = service.create_diagnosis(
        "camera-3f-001", SecurityFaultType.CAMERA_BLACK_SCREEN, "tester"
    )
    return service, runner, case.diagnosis_id


def _failure(kind: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name="device__query_status", ok=False, error="受控失败",
        metadata={"failure_kind": kind, "operation": "query_status"},
    )


def test_timeout_without_evidence_waits_for_input_without_retry():
    service, runner, diagnosis_id = _service(ToolLoopResult(
        diagnosis_id="placeholder", ok=False, tool_calls=1,
        tool_results=[_failure("timeout")], error="未产出结论",
    ))
    result = service.run_diagnosis(diagnosis_id)

    assert result.status is SecurityDiagnosisStatus.WAITING_FOR_INPUT
    assert result.degraded is True
    assert result.failure_kinds == ["timeout"]
    assert result.evidence_count == 0
    assert result.conclusion is None
    assert runner.calls == 1


def test_missing_capability_without_evidence_is_inconclusive():
    service, _, diagnosis_id = _service(ToolLoopResult(
        diagnosis_id="placeholder", ok=False,
        tool_results=[_failure("capability_missing")], error="未产出结论",
    ))
    result = service.run_diagnosis(diagnosis_id)

    assert result.status is SecurityDiagnosisStatus.INCONCLUSIVE
    assert result.failure_kinds == ["capability_missing"]
    assert result.conclusion is None


def test_partial_success_persists_only_real_evidence_and_marks_degraded():
    draft = ToolEvidenceDraft(
        evidence_type=EvidenceType.DEVICE_STATUS,
        source=EvidenceSource.DEVICE_GATEWAY,
        summary="设备状态事实", payload={"online": True},
    )
    loop = ToolLoopResult(
        diagnosis_id="placeholder", tool_calls=2,
        tool_results=[
            ToolExecutionResult(tool_name="ok", evidence_drafts=[draft]),
            _failure("timeout"),
        ],
        evidence_drafts=[draft],
        final_conclusion=ConclusionDraft(
            fault_type=SecurityFaultType.CAMERA_BLACK_SCREEN,
            summary="候选判断", confidence="possible", cited_evidence_ids=[],
        ),
    )
    service, _, diagnosis_id = _service(loop)
    result = service.run_diagnosis(diagnosis_id)

    assert result.ok is True
    assert result.degraded is True
    assert result.failure_kinds == ["timeout"]
    assert result.evidence_count == 1
    assert len(service.list_evidence(diagnosis_id)) == 1
