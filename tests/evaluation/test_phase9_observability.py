"""Phase 9D 指标安全与影子评测。"""

import pytest
from scripts.eval_phase9d_simulator_shadow import evaluate

from security_diagnosis_harness.adapters.observability import InMemoryObservabilityAdapter
from security_diagnosis_harness.ports.observability import MetricPoint


def test_metric_labels_reject_identifiers_and_secrets():
    for labels in (
        {"device_id": "camera-01"},
        {"endpoint": "http://127.0.0.1"},
        {"operation": "token=plain-secret"},
    ):
        with pytest.raises(ValueError):
            MetricPoint(name="tool_calls_total", value=1, labels=labels)


def test_in_memory_adapter_returns_immutable_snapshot():
    sink = InMemoryObservabilityAdapter()
    sink.record(MetricPoint(name="tool_calls_total", value=1))
    assert sink.snapshot() == (MetricPoint(name="tool_calls_total", value=1),)


def test_simulator_shadow_runs_formal_runtime_and_separates_report_kind():
    payload = evaluate()
    summary = payload["summary"]

    assert summary["report_kind"] == "simulator_e2e"
    assert summary["shadow_mode"] is True
    assert summary["total"] == 3
    assert summary["controlled_degradation_cases"] >= 2
    assert summary["gate_allowed"] is True
    assert summary["device_writes"] == 0
    assert summary["external_notifications"] == 0


def test_shadow_metrics_cover_required_families_without_sensitive_labels():
    payload = evaluate()
    names = {point["name"] for point in payload["metrics"]}
    assert {
        "tool_calls_total",
        "tool_calls_failed",
        "adapter_call_latency_ms",
        "evidence_total",
        "evidence_missing",
        "diagnosis_completed",
        "diagnosis_status_total",
        "citation_compliant",
        "model_calls_total",
        "model_tokens_total",
        "model_estimated_cost_microunits",
        "budget_blocks_total",
        "p0_findings_total",
        "human_reviews_total",
        "human_confirmation_latency_ms",
        "candidate_adopted",
    } <= names
    serialized = json_dump(payload["metrics"])
    assert "camera-3f-001" not in serialized
    assert "token=" not in serialized.lower()


def json_dump(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
