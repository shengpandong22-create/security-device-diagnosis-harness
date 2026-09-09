"""Phase 2C 评测脚本验收。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "eval_phase2_recording_missing.py"

MIN_LABEL_ACCURACY = 1.0
MIN_CASES = 5


def _load_module():
    spec = importlib.util.spec_from_file_location("eval_phase2_recording_missing", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _cleanup(module) -> None:
    for path in (module.JSON_PATH, module.MD_PATH):
        if path.exists():
            path.unlink()


def test_eval_script_runs_and_writes_outputs():
    module = _load_module()
    _cleanup(module)

    exit_code = module.main()

    assert exit_code == 0
    assert module.JSON_PATH.exists()
    assert module.MD_PATH.exists()

    payload = json.loads(module.JSON_PATH.read_text(encoding="utf-8"))
    markdown = module.MD_PATH.read_text(encoding="utf-8")

    assert markdown.startswith("# Phase 2C 录像缺失评测报告")
    assert set(payload) == {"summary", "cases"}

    _cleanup(module)


def test_eval_covers_five_cases():
    module = _load_module()
    payload = module.run_eval()

    assert payload["summary"]["total"] == MIN_CASES
    assert len(payload["cases"]) == MIN_CASES


def test_eval_label_accuracy_is_full():
    module = _load_module()
    payload = module.run_eval()

    assert payload["summary"]["label_accuracy"] == MIN_LABEL_ACCURACY
    for item in payload["cases"]:
        assert item["matched"] is True
        assert item["actual_label"] == item["expected_label"]


def test_eval_citation_compliance_is_full():
    module = _load_module()
    payload = module.run_eval()

    assert payload["summary"]["citation_compliance"] == 1.0
    for item in payload["cases"]:
        assert item["citation_compliant"] is True
        assert item["cited_evidence_ids"]


def test_eval_does_not_call_external_model():
    module = _load_module()
    payload = module.run_eval()

    assert payload["summary"]["external_model_called"] is False
    for item in payload["cases"]:
        assert item["external_model_called"] is False


def test_eval_has_no_sensitive_leak():
    module = _load_module()
    payload = module.run_eval()

    assert payload["summary"]["sensitive_leak_count"] == 0
    for item in payload["cases"]:
        assert item["sensitive_leak_count"] == 0


def test_eval_cases_carry_required_fields():
    module = _load_module()
    payload = module.run_eval()

    required = {
        "case_id",
        "device_id",
        "expected_label",
        "actual_label",
        "matched",
        "status",
        "evidence_count",
        "cited_evidence_ids",
        "external_model_called",
        "report_path",
    }
    for item in payload["cases"]:
        assert required <= set(item)
        assert item["evidence_count"] >= 3
        assert item["status"] == "confirmed"


def test_eval_reports_are_generated():
    module = _load_module()
    payload = module.run_eval()

    for item in payload["cases"]:
        report_path = REPO_ROOT / item["report_path"]
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert item["device_id"] in content
