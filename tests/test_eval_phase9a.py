from scripts.eval_phase9a_simulator_e2e import evaluate

from security_diagnosis_harness.bootstrap.container import PHASE1_TOOLS, build_registry


def test_simulator_e2e_runs_full_fixed_suite() -> None:
    payload = evaluate()
    assert payload["summary"] == {
        "report_kind": "simulator_e2e",
        "total": 3,
        "completed": 3,
        "controlled_failure_scenarios": 2,
        "failed_result_evidence_violations": 0,
        "external_model_called": False,
    }


def test_simulator_e2e_keeps_report_kinds_separate() -> None:
    assert evaluate()["summary"]["report_kind"] == "simulator_e2e"


def test_simulator_e2e_only_human_review_confirms() -> None:
    rows = evaluate()["scenarios"]
    assert all(row["status"] == "confirmed" for row in rows)


def test_simulator_failures_are_controlled_without_evidence() -> None:
    rows = evaluate()["scenarios"]
    failures = [row for row in rows if row["failed_gateway_calls"]]
    assert len(failures) == 2
    assert all(row["failed_result_evidence_count"] == 0 for row in failures)


def test_simulator_e2e_uses_real_registry_call_path() -> None:
    rows = evaluate()["scenarios"]
    assert all(row["tool_calls"] == 7 for row in rows)
    assert all(row["gateway_calls"] == 6 for row in rows)


def test_model_visible_tool_schemas_cannot_select_connection_or_adapter() -> None:
    registry = build_registry()
    forbidden = {"endpoint", "endpoint_alias", "credential", "adapter", "adapter_key"}
    for tool_name in PHASE1_TOOLS:
        fields = set(registry.get(tool_name).input_model.model_fields)
        assert fields.isdisjoint(forbidden), tool_name
