"""Phase 3C 门禁刷卡异常固定案例评测脚本验收。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "eval_phase3_access_card_failed.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("eval_phase3_access_card_failed", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_phase3_eval_runs_all_cases():
    module = _load_module()

    payload = module.run_eval()

    assert payload["summary"]["total"] == 5
    assert {item["case_id"] for item in payload["cases"]} == {
        "credential_frozen",
        "permission_denied",
        "time_window_denied",
        "controller_offline",
        "door_lock_jammed",
    }


def test_phase3_eval_label_accuracy_is_full():
    module = _load_module()

    payload = module.run_eval()

    assert payload["summary"]["passed"] == payload["summary"]["total"]
    assert payload["summary"]["label_accuracy"] == 1.0


def test_phase3_eval_citation_compliance_is_full():
    module = _load_module()

    payload = module.run_eval()

    assert payload["summary"]["citation_compliance"] == 1.0
    assert all(item["citation_compliant"] for item in payload["cases"])


def test_phase3_eval_does_not_call_external_model():
    module = _load_module()

    payload = module.run_eval()

    assert payload["summary"]["external_model_called"] is False
    assert all(item["external_model_called"] is False for item in payload["cases"])


def test_phase3_eval_does_not_leak_sensitive_values():
    module = _load_module()

    payload = module.run_eval()

    assert payload["summary"]["sensitive_leak_count"] == 0
    assert all(item["sensitive_leak_count"] == 0 for item in payload["cases"])


def test_phase3_eval_case_fields_are_complete():
    module = _load_module()

    payload = module.run_eval()

    for item in payload["cases"]:
        assert item["expected_label"]
        assert item["actual_label"]
        assert item["matched"] is True
        assert item["status"] == "confirmed"
        assert item["evidence_count"] >= 5
        assert item["cited_evidence_ids"]
        assert item["review_status"] == "confirmed"


def test_phase3_eval_markdown_report_contains_summary():
    module = _load_module()
    payload = module.run_eval()

    markdown = module.render_markdown(payload)

    assert "Phase 3C 门禁刷卡异常评测报告" in markdown
    assert "label_accuracy: 1.0" in markdown
    assert "citation_compliance: 1.0" in markdown
    assert "external_model_called: false" in markdown
    assert "credential_frozen" in markdown
