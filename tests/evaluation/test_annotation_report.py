from __future__ import annotations

import json
from pathlib import Path

from scripts.eval_phase8_annotation_agreement import evaluate

from security_diagnosis_harness.domain.enums import SecurityFaultType

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = (
    REPO_ROOT / "evaluation-fixtures" / "phase8" / "annotation-pairs-1.0.0.json"
)


def test_fixed_annotation_fixture_is_explicitly_synthetic():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["fixture_kind"] == "synthetic_protocol_fixture"
    assert fixture["dataset_version"] == "1.0.0"
    assert len(fixture["pairs"]) == 4


def test_fixture_does_not_contain_expected_or_model_answers():
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "expected_candidate",
        "expected_tools",
        "required_evidence_types",
        "model_output",
        "api_key",
        "password",
    ):
        assert forbidden not in text.lower()


def test_annotation_evaluation_produces_expected_aggregate(tmp_path):
    result = evaluate(tmp_path)
    assert result["fixture_kind"] == "synthetic_protocol_fixture"
    assert result["task_count"] == 4
    assert result["label_agreement_rate"] == 0.75
    assert result["mean_tool_jaccard"] == 11 / 12
    assert result["mean_evidence_jaccard"] == 11 / 12
    assert result["adjudication_rate"] == 0.5
    assert -1 <= result["label_kappa"] <= 1


def test_report_has_one_summary_per_fault_type(tmp_path):
    result = evaluate(tmp_path)
    report = json.loads(Path(result["json_report"]).read_text(encoding="utf-8"))
    summaries = report["fault_type_summaries"]
    assert {item["fault_type"] for item in summaries} == {
        item.value for item in SecurityFaultType
    }
    assert all(item["task_count"] == 1 for item in summaries)
    alarm = next(
        item
        for item in summaries
        if item["fault_type"] == SecurityFaultType.ALARM_FALSE_POSITIVE.value
    )
    assert alarm["label_agreement_rate"] == 0
    assert alarm["adjudication_rate"] == 1


def test_report_contains_no_facts_reviewer_names_or_rationales(tmp_path):
    result = evaluate(tmp_path)
    json_text = Path(result["json_report"]).read_text(encoding="utf-8")
    markdown_text = Path(result["markdown_report"]).read_text(encoding="utf-8")
    combined = json_text + markdown_text
    for forbidden in (
        "input_facts",
        "synthetic-reviewer-a",
        "synthetic-reviewer-b",
        "rationale",
        "device_id",
    ):
        assert forbidden not in combined


def test_report_files_are_atomic_and_repeatable(tmp_path):
    first = evaluate(tmp_path)
    second = evaluate(tmp_path)
    assert Path(first["json_report"]).read_text(encoding="utf-8") == Path(
        second["json_report"]
    ).read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_markdown_identifies_fixture_limitation(tmp_path):
    result = evaluate(tmp_path)
    markdown = Path(result["markdown_report"]).read_text(encoding="utf-8")
    assert "synthetic_protocol_fixture" in markdown
    assert "不冒充真实专家标注" in markdown
    assert "alarm_false_positive" in markdown
