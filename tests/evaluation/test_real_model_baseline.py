from __future__ import annotations

import json
from pathlib import Path

BASELINE = (
    Path(__file__).resolve().parents[2]
    / "evaluation-baselines"
    / "phase7"
    / "validation-1.0.0-deepseek-flash.json"
)


def test_real_model_baseline_records_final_validation_metrics():
    report = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert report["report_kind"] == "real_model"
    assert report["dataset_version"] == "1.0.0"
    assert report["split"] == "validation"
    assert report["total_calls"] == report["total_cases"] == 2
    assert report["failed_cases"] == 0
    assert report["candidate_accuracy"] == 1
    assert report["tool_precision"] == 0.75
    assert report["tool_recall"] == 1
    assert report["evidence_compliance"] == 1
    assert report["evidence_recall"] == 1


def test_real_model_baseline_contains_only_sanitized_results():
    text = BASELINE.read_text(encoding="utf-8")
    report = json.loads(text)
    assert report["security"] == {
        "contains_input_facts": False,
        "contains_api_key": False,
        "contains_base_url": False,
        "automatic_retry": False,
    }
    for forbidden in (
        "SECURITY_DIAGNOSIS_EVAL_API_KEY",
        "api.deepseek.com",
        '"input_facts"',
        "Bearer ",
    ):
        assert forbidden not in text


def test_real_model_baseline_does_not_claim_cost_measurement():
    report = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert report["estimated_cost"] == 0
    assert any("token 单价" in item for item in report["limitations"])
