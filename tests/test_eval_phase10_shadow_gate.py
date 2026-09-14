"""Phase 10A 固定脚本：baseline → candidate → 逐项 Gate。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scripts import eval_phase10_shadow_gate as script
from scripts.eval_phase9d_simulator_shadow import evaluate, shadow_run

from security_diagnosis_harness.evaluation import (
    CORE_SHADOW_RATES,
    SAFETY_BLOCKERS,
    ShadowRunSummary,
    compare_shadow_runs,
)

ROOT = Path(script.__file__).resolve().parents[1]
CREATED_AT = datetime(2026, 9, 14, tzinfo=UTC)


@pytest.fixture(scope="module")
def payload() -> dict:
    return script.run_fixed_gate()


def test_phase10_gate_script_is_offline_repeatable_and_per_item(capsys):
    assert script.main() == 0
    first = json.loads(capsys.readouterr().out)
    assert script.main() == 0
    second = json.loads(capsys.readouterr().out)
    assert first == second
    assert first["report_kind"] == "simulator_e2e"
    assert first["adapter_kind"] == "simulator"
    assert first["history_records"] == 2
    assert first["gate_allowed"] is True
    assert first["blocking_reasons"] == []
    assert first["history_written_to_repository"] is False
    assert first["external_model_called"] is False


def test_phase10_gate_script_reports_per_item_conclusions(payload):
    assert {item["metric"] for item in payload["metric_deltas"]} == {
        metric for metric, _ in CORE_SHADOW_RATES
    }
    assert {item["metric"] for item in payload["safety_checks"]} == {
        metric for metric, _ in SAFETY_BLOCKERS
    }
    assert all(item["blocked"] is False for item in payload["safety_checks"])
    assert all(item["regressed"] is False for item in payload["metric_deltas"])
    # 逐项重算结论必须与历史记录中落盘的 Gate 布尔值一致。
    assert payload["per_item_gate_matches_record"] is True
    assert payload["gate_allowed"] == payload["per_item_gate_matches_record"]


def test_phase10_gate_script_writes_no_history_into_repository(capsys):
    before = {path.name for path in ROOT.iterdir()}
    assert script.run_fixed_gate()["history_written_to_repository"] is False
    capsys.readouterr()
    assert {path.name for path in ROOT.iterdir()} == before
    assert not list(ROOT.glob("shadow-history.json"))


def test_phase9d_exposes_stable_strong_typed_shadow_input():
    baseline = shadow_run(run_id="phase10a-a", created_at=CREATED_AT, code_commit="a" * 40)
    candidate = shadow_run(
        run_id="phase10a-b",
        created_at=CREATED_AT + timedelta(seconds=3),
        code_commit="b" * 40,
    )
    assert baseline.identity.report_kind == "simulator_e2e"
    assert baseline.identity.adapter_kind == "simulator"
    assert baseline.identity.controlled_variables() == candidate.identity.controlled_variables()
    assert baseline.scenarios == candidate.scenarios

    before = ShadowRunSummary.from_run(baseline)
    after = ShadowRunSummary.from_run(candidate)
    assert before.comparison_fingerprint == after.comparison_fingerprint
    assert before.metrics.total == len(baseline.scenarios) == 3
    assert before.metrics.completion_rate == 1.0
    assert before.metrics.p0_findings == sum(item.p0_findings for item in baseline.scenarios)
    assert before.metrics.evidence_violations == 0
    assert before.metrics.device_writes == 0
    assert before.metrics.external_notifications == 0

    report = compare_shadow_runs(before, after)
    assert report.allowed is True
    assert report.blocked_by_safety is False
    assert report.safety_checks


def test_phase9d_summary_aggregates_match_scenario_facts():
    summary = evaluate()["summary"]
    assert summary["report_kind"] == "simulator_e2e"
    assert summary["gate_allowed"] is True
    assert summary["p0_findings"] == 0
    assert summary["sensitive_leaks"] == 0
    assert summary["evidence_violations"] == 0
    assert summary["device_writes"] == 0
    assert summary["external_notifications"] == 0
