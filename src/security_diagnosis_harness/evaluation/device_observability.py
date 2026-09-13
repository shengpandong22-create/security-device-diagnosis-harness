"""Phase 9D 设备端到端运行的聚合指标生成。"""

from __future__ import annotations

from security_diagnosis_harness.adapters.device_gateway.simulator import SimulatorCallTrace
from security_diagnosis_harness.application.diagnoses import RunDiagnosisResult
from security_diagnosis_harness.domain.case import SecurityDiagnosisCase
from security_diagnosis_harness.ports.observability import MetricPoint, ObservabilityPort


def record_shadow_metrics(
    sink: ObservabilityPort,
    run: RunDiagnosisResult,
    case: SecurityDiagnosisCase,
    traces: list[SimulatorCallTrace],
) -> None:
    """只记录聚合与低基数维度，不保存设备标识或原始输入输出。"""
    total = len(traces)
    succeeded = sum(trace.ok for trace in traces)
    labels = {"fault_type": case.fault_type.value, "status": case.status.value}
    evidence_ids = {item.evidence_id for item in case.evidence}
    cited_ids = set(case.conclusion.cited_evidence_ids) if case.conclusion else set()
    citation_compliant = bool(cited_ids) and cited_ids <= evidence_ids
    review_latency_ms = (
        max(0.0, (case.reviews[-1].reviewed_at - case.created_at).total_seconds() * 1000)
        if case.reviews
        else 0.0
    )
    for name, value in (
        ("tool_calls_total", run.tool_calls),
        ("tool_calls_succeeded", succeeded),
        ("tool_calls_failed", total - succeeded),
        ("evidence_total", len(case.evidence)),
        ("evidence_missing", max(0, total - len(case.evidence))),
        ("diagnosis_completed", int(run.ok)),
        ("diagnosis_status_total", 1),
        ("citation_compliant", int(citation_compliant)),
        ("model_calls_total", run.rounds),
        ("model_tokens_total", 0),
        ("model_estimated_cost_microunits", 0),
        ("budget_blocks_total", int("预算" in (run.error or ""))),
        ("p0_findings_total", 0),
        ("human_reviews_total", len(case.reviews)),
        ("human_confirmation_latency_ms", review_latency_ms),
        ("candidate_adopted", int(case.status.value == "confirmed")),
    ):
        sink.record(MetricPoint(name=name, value=value, labels=labels))
    for trace in traces:
        sink.record(
            MetricPoint(
                name="adapter_call_latency_ms",
                value=trace.simulated_latency_ms,
                labels={
                    "adapter": "simulator",
                    "capability": trace.capability.value,
                    "operation": trace.operation,
                    "failure_kind": trace.error_kind.value if trace.error_kind else "none",
                },
            )
        )
